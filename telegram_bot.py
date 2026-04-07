"""
Telegram Bot for Jellyfin Media Copy
Run this on your Raspberry Pi to control media operations from your phone.
"""

import os
import sys
import re
import yaml
import json
import logging
import asyncio
import httpx
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

from scanner import FolderScanner
from copier import RsyncCopier, ExternalCopier
from jellyfin import refresh_jellyfin_library
from updater import SystemUpdater

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
MODE_SELECTION, CONTENT_SELECTION, CONFIRMATION, COPYING = range(4)

# Store user data and ideas
user_data: Dict[int, dict] = {}
ideas_data: List[dict] = []  # List of ideas with date, text, status

USER_DATA_FILE = "user_data.json"
IDEAS_FILE = "ideas.json"

def load_persistent_data():
    """Load user data and ideas from JSON files."""
    global user_data, ideas_data
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, 'r') as f:
                user_data = {int(k): v for k, v in json.load(f).items()}
        except Exception:
            user_data = {}
    if os.path.exists(IDEAS_FILE):
        try:
            with open(IDEAS_FILE, 'r') as f:
                ideas_data = json.load(f)
        except Exception:
            ideas_data = []

def save_user_data():
    """Save user data to JSON file."""
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(user_data, f)

def save_ideas():
    """Save ideas to JSON file."""
    with open(IDEAS_FILE, 'w') as f:
        json.dump(ideas_data, f, indent=2, default=str)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes == 0:
        return "0 B"
    import math
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


def is_authorized(user_id: int, allowed_users: List[int]) -> bool:
    """Check if user is authorized to use the bot."""
    return not allowed_users or user_id in allowed_users


# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the bot and check authorization."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text(
            "⛔ Unauthorized. Your user ID is not allowed to use this bot.\n"
            f"Your ID: `{user_id}`",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    user_data[user_id] = {'config': config}
    
    keyboard = [
        [
            InlineKeyboardButton("📁 Internal (Pi → Pi)", callback_data='internal'),
            InlineKeyboardButton("💻 External (Pi → Laptop)", callback_data='external'),
        ],
        [InlineKeyboardButton("🔧 Maintenance (Update Pi)", callback_data='update')],
    ]
    
    await update.message.reply_text(
        "🎬 *Jellyfin Media Manager*\n\n"
        "Select operation mode:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return MODE_SELECTION


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle mode selection."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    mode = query.data
    user_data[user_id]['mode'] = mode
    
    if mode == 'update':
        return await handle_update_mode(update, context)
    
    # For internal/external modes, scan content
    await query.edit_message_text(
        f"Selected: *{mode.upper()}* mode\n\n"
        "🔍 Scanning downloads folder...",
        parse_mode='Markdown'
    )
    
    config = user_data[user_id]['config']
    tmdb_key = config.get('tmdb', {}).get('api_key')
    scanner = FolderScanner(config['pi'], tmdb_api_key=tmdb_key)
    
    if not scanner.connect():
        await query.edit_message_text(
            "❌ Failed to connect to Raspberry Pi.\n"
            "Check your configuration."
        )
        return ConversationHandler.END
    
    user_data[user_id]['scanner'] = scanner
    
    downloads_path = config['paths']['downloads']
    items = scanner.scan_folder(downloads_path)
    
    if not items:
        await query.edit_message_text(
            "📂 No items found in downloads folder."
        )
        scanner.close()
        return ConversationHandler.END
    
    # Organize items
    tv_shows = {}
    movies = {}
    
    for item in items:
        content_type = item.get('content_type', 'movie')
        show_name = item.get('show', item['name'])
        
        if content_type == 'tv':
            season = item.get('season', 'Unknown')
            if show_name not in tv_shows:
                tv_shows[show_name] = {}
            if season not in tv_shows[show_name]:
                tv_shows[show_name][season] = []
            tv_shows[show_name][season].append(item)
        else:
            if show_name not in movies:
                movies[show_name] = []
            movies[show_name].append(item)
    
    # Build selection keyboard
    keyboard = []
    selected_items = []
    
    # Add TV shows
    for show_name in sorted(tv_shows.keys()):
        seasons = tv_shows[show_name]
        for season in sorted(seasons.keys(), key=lambda x: str(x)):
            items_list = seasons[season]
            path = items_list[0]['path']
            display = f"📺 {show_name} - S{season} ({len(items_list)} items)"
            selected_items.append({
                'show': show_name,
                'season': season,
                'path': path,
                'items': items_list,
                'content_type': 'tv',
                'display': display
            })
    
    # Add movies
    for movie_name in sorted(movies.keys()):
        items_list = movies[movie_name]
        path = items_list[0]['path']
        display = f"🎬 {movie_name} ({len(items_list)} items)"
        selected_items.append({
            'show': movie_name,
            'season': None,
            'path': path,
            'items': items_list,
            'content_type': 'movie',
            'display': display
        })
    
    user_data[user_id]['available_items'] = selected_items
    user_data[user_id]['selected_indices'] = set()
    user_data[user_id]['page'] = 0
    
    await query.edit_message_text(
        f"📁 Found {len(selected_items)} item(s)\n\n"
        "Tap to select/deselect items:",
        reply_markup=build_page_keyboard(selected_items, set(), 0)
    )
    
    return CONTENT_SELECTION


PAGE_SIZE = 8


def build_page_keyboard(available_items: list, selected_indices: set, page: int) -> InlineKeyboardMarkup:
    """Build a paginated inline keyboard for item selection."""
    total = len(available_items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    
    keyboard = []
    for i in range(start, end):
        item = available_items[i]
        checkbox = "☑" if i in selected_indices else "⬜"
        keyboard.append([
            InlineKeyboardButton(
                f"{checkbox} {item['display']}",
                callback_data=f'toggle_{i}'
            )
        ])
    
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f'page_{page - 1}'))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data='noop'))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f'page_{page + 1}'))
    keyboard.append(nav_row)
    
    selected_count = len(selected_indices)
    keyboard.append([
        InlineKeyboardButton(f"✅ Confirm ({selected_count} selected)", callback_data='confirm_selection'),
        InlineKeyboardButton("❌ Cancel", callback_data='cancel')
    ])
    
    return InlineKeyboardMarkup(keyboard)


async def toggle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle item selection."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    available_items = user_data[user_id]['available_items']
    selected_indices = user_data[user_id]['selected_indices']
    page = user_data[user_id].get('page', 0)
    
    if data.startswith('page_'):
        page = int(data.split('_')[1])
        user_data[user_id]['page'] = page
    elif data.startswith('toggle_'):
        idx = int(data.split('_')[1])
        if idx in selected_indices:
            selected_indices.remove(idx)
        else:
            selected_indices.add(idx)
    
    await query.edit_message_reply_markup(
        reply_markup=build_page_keyboard(available_items, selected_indices, page)
    )
    
    return CONTENT_SELECTION


async def confirm_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show confirmation with disk space check."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    selected_indices = user_data[user_id]['selected_indices']
    available_items = user_data[user_id]['available_items']
    mode = user_data[user_id]['mode']
    config = user_data[user_id]['config']
    scanner = user_data[user_id]['scanner']
    
    if not selected_indices:
        await query.answer("No items selected!", show_alert=True)
        return CONTENT_SELECTION
    
    # Get selected items
    selected = [available_items[i] for i in selected_indices]
    user_data[user_id]['selected'] = selected
    
    # Calculate sizes
    total_size = scanner.calculate_items_size(selected)
    
    # Get destination space
    if mode == 'internal':
        shows_path = config['paths'].get('jellyfin_shows', '/mnt/media/Shows')
        movies_path = config['paths'].get('jellyfin_movies', '/mnt/media/Movies')
        shows_space = scanner.get_disk_space(shows_path)
        movies_space = scanner.get_disk_space(movies_path)
        dest_free = min(shows_space['available'], movies_space['available'])
        dest_path = "/mnt/media"
    else:
        local_dest = config['paths'].get('local_destination', './downloads')
        local_dest = os.path.abspath(os.path.expanduser(local_dest))
        try:
            import shutil
            stat = shutil.disk_usage(local_dest)
            dest_free = stat.free
        except Exception:
            dest_free = 0
        dest_path = local_dest
    
    # Build summary
    tv_count = sum(1 for s in selected if s['content_type'] == 'tv')
    movie_count = sum(1 for s in selected if s['content_type'] == 'movie')
    
    space_ok = total_size <= dest_free
    space_emoji = "✅" if space_ok else "⚠️"
    
    # Check dry-run mode
    dry_run = user_data[user_id].get('dry_run', config.get('options', {}).get('dry_run', False))
    dry_run_emoji = "🧪" if dry_run else "▶️"
    dry_run_text = "DRY-RUN (Preview only)" if dry_run else "LIVE (Will copy files)"
    
    summary = (
        f"📊 *Selection Summary*\n\n"
        f"{dry_run_emoji} *Mode:* {dry_run_text}\n"
        f"Selected: {len(selected)} item(s)\n"
        f"  📺 TV Shows: {tv_count}\n"
        f"  🎬 Movies: {movie_count}\n"
        f"  📦 Size: {format_size(total_size)}\n\n"
        f"{space_emoji} Destination: {format_size(dest_free)} free at {dest_path}"
    )
    
    if not space_ok:
        shortfall = total_size - dest_free
        summary += f"\n⚠️ *Need {format_size(shortfall)} more space*"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Proceed", callback_data='proceed_copy'),
            InlineKeyboardButton("❌ Cancel", callback_data='cancel'),
        ]
    ]
    
    await query.edit_message_text(
        summary,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return CONFIRMATION


async def proceed_copy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start copying process."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    selected = user_data[user_id]['selected']
    mode = user_data[user_id]['mode']
    config = user_data[user_id]['config']
    
    await query.edit_message_text(
        f"🚀 Starting {mode.upper()} copy...\n"
        f"Items: {len(selected)}\n"
        "⏳ Connecting and preparing transfer..."
    )
    
    # Run copy in background to not block
    asyncio.create_task(
        run_copy_process(update, context, selected, mode, config)
    )
    
    return COPYING


async def run_copy_process(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           selected: List[dict], mode: str, config: dict):
    """Run the actual copy process and send progress updates."""
    query = update.callback_query
    user_id = update.effective_user.id
    scanner = user_data[user_id]['scanner']
    
    # Get dry-run mode for this user
    dry_run = user_data[user_id].get('dry_run', config.get('options', {}).get('dry_run', False))
    
    # Progress tracking for Telegram updates
    import time as _time
    last_update_time = 0
    copy_state = {
        'item_idx': 0,
        'percent': 0,
        'speed': '',
        'eta': '',
        'filename': '',
        'start_time': _time.time(),
        'completed': [],  # list of display names already done
        'ep_num': 0,
        'ep_total': 0,
    }
    total_items = len(selected)
    _event_loop = asyncio.get_event_loop()
    
    async def update_progress_message():
        """Update the Telegram message with current progress."""
        nonlocal last_update_time
        
        now = _time.time()
        # Throttle updates to every 3 seconds to avoid Telegram rate limits
        if now - last_update_time < 3:
            return
        last_update_time = now
        
        i = copy_state['item_idx']
        pct = copy_state['percent']
        speed = copy_state['speed']
        eta = copy_state['eta']
        completed = copy_state['completed']
        num_done = len(completed)
        
        # Build progress bar string (10 chars wide)
        filled = int(pct / 10)
        bar = '█' * filled + '░' * (10 - filled)
        
        # Current item display name
        item_name = selected[i - 1]['show'] if i > 0 else ''
        if i > 0 and selected[i - 1].get('season'):
            item_name += f" S{selected[i - 1]['season']}"
        
        elapsed = int(now - copy_state['start_time'])
        
        ep_num = copy_state['ep_num']
        ep_total = copy_state['ep_total']
        
        lines = [f"📋 *{num_done}/{total_items} items done* — {elapsed}s elapsed"]
        lines.append("")
        
        # Show completed items (limit to last 10 to keep message short)
        if completed:
            if len(completed) > 10:
                lines.append(f"✅ ... ({len(completed) - 10} more)")
                for name in completed[-10:]:
                    lines.append(f"✅ {name}")
            else:
                for name in completed:
                    lines.append(f"✅ {name}")
            lines.append("")
        
        # Show current item in progress
        if i > 0:
            cur_item = selected[i - 1]
            is_tv = cur_item.get('content_type') == 'tv'
            if is_tv and ep_total > 0:
                lines.append(f"🔄 *{item_name}* — episode {ep_num}/{ep_total}")
            else:
                lines.append(f"🔄 *{item_name}*")
            lines.append(f"`{bar}` {pct}%")
            if speed:
                lines.append(f"⚡ `{speed}`")
            if eta and eta not in ('0:00:00', ''):
                lines.append(f"⏱ ETA `{eta}`")
        
        text = '\n'.join(lines)
        
        # Truncate if too long (Telegram limit is 4096)
        if len(text) > 4000:
            text = text[:3997] + '...'
        
        try:
            await query.message.edit_text(
                text,
                parse_mode='Markdown'
            )
        except Exception as e:
            # Log the actual error for debugging
            logger.warning(f"Failed to edit message: {e}")
    
    def progress_callback(item_num, total, percent, filename, speed="", eta="", ep_num=0, ep_total=0):
        """Called by copier from executor thread — schedule coroutine on the event loop."""
        prev_idx = copy_state['item_idx']
        copy_state['item_idx'] = item_num
        copy_state['percent'] = percent
        copy_state['speed'] = speed
        copy_state['eta'] = eta
        copy_state['filename'] = filename
        copy_state['ep_num'] = ep_num
        copy_state['ep_total'] = ep_total
        
        # When moving to a new item, mark the previous one as completed
        if item_num > prev_idx and prev_idx > 0:
            prev = selected[prev_idx - 1]
            name = prev['show']
            if prev.get('season'):
                name += f" S{prev['season']}"
            if name not in copy_state['completed']:
                copy_state['completed'].append(name)
        
        # Also mark as complete when percent hits 100 (last item)
        if percent >= 100:
            cur = selected[item_num - 1]
            name = cur['show']
            if cur.get('season'):
                name += f" S{cur['season']}"
            if name not in copy_state['completed']:
                copy_state['completed'].append(name)
        
        # Must use run_coroutine_threadsafe since this is called from a thread
        asyncio.run_coroutine_threadsafe(update_progress_message(), _event_loop)
    
    try:
        # Create copier options with dry_run flag
        copier_options = dict(config['options'])
        copier_options['dry_run'] = dry_run
        
        if mode == 'internal':
            copier = RsyncCopier(config['pi'], config['paths'], copier_options)
        else:
            local_paths = {
                'local_destination': config['paths'].get('local_destination', './downloads')
            }
            copier = ExternalCopier(config['pi'], local_paths, copier_options)
        
        # Create a simple console-like object for copier.
        # print() is called from an executor thread, so must use run_coroutine_threadsafe.
        class TelegramConsole:
            def __init__(self, message, event_loop):
                self.message = message
                self._loop = event_loop
            
            def print(self, text):
                import re
                clean_text = re.sub(r'\[/?[^\]]+\]', '', text).strip()
                if not clean_text:
                    return
                # Always log to stdout/journald
                logger.info(f"[copier] {clean_text}")
                # Send errors to Telegram immediately
                if any(keyword in clean_text.lower() for keyword in ['error', 'failed']):
                    async def _send():
                        try:
                            await self.message.edit_text(f"❌ {clean_text}")
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(_send(), self._loop)
        
        console = TelegramConsole(query.message, _event_loop)
        
        # copy_items is synchronous/blocking (SSH + rsync I/O).
        # Run it in a thread executor so the asyncio event loop stays free
        # to process progress message updates and other bot events.
        success = await _event_loop.run_in_executor(
            None,
            lambda: copier.copy_items(selected, console, progress_callback=progress_callback)
        )
        
        if success:
            if dry_run:
                result_text = (
                    "🧪 *Dry-Run Preview Completed*\n\n"
                    f"Previewed {len(selected)} item(s)\n"
                    "✅ No files were copied (preview only)\n\n"
                    "Use `/dryrun` to disable preview mode, then copy again."
                )
            else:
                result_text = (
                    "✅ *Copy completed successfully!*\n\n"
                    f"Copied {len(selected)} item(s)"
                )
            
            # Refresh Jellyfin for internal mode (skip in dry-run)
            if mode == 'internal' and not dry_run:
                result_text += "\n🔄 Refreshing Jellyfin library..."
                await query.message.edit_text(result_text)
                
                jellyfin_config = config.get('jellyfin', {})
                refresh_success = refresh_jellyfin_library(
                    host=jellyfin_config.get('host', config['pi']['host']),
                    port=jellyfin_config.get('port', 8096),
                    api_key=jellyfin_config.get('api_key'),
                    scanner=scanner
                )
                
                if refresh_success:
                    result_text += "\n✅ Jellyfin refreshed!"
                else:
                    result_text += "\n⚠️ Jellyfin refresh failed"
        else:
            result_text = "❌ *Copy failed*\nCheck logs for details."
        
        await query.message.edit_text(result_text, parse_mode='Markdown')
        
    except Exception as e:
        import traceback
        logger.error(f"Copy process failed: {e}\n{traceback.format_exc()}")
        await query.message.edit_text(
            f"❌ *Error during copy:*\n`{str(e)}`",
            parse_mode='Markdown'
        )
    finally:
        scanner.close()
        # Clean up user data
        if user_id in user_data:
            del user_data[user_id]


async def handle_update_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle maintenance/update mode."""
    query = update.callback_query
    user_id = update.effective_user.id
    config = user_data[user_id]['config']
    
    # Check if sudo_password is configured
    sudo_password = config['pi'].get('sudo_password')
    if not sudo_password:
        await query.edit_message_text(
            "❌ *Update mode requires sudo_password*\n\n"
            "Add `sudo_password` to your `config.yaml` under the `pi` section.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Proceed with Updates", callback_data='proceed_update'),
            InlineKeyboardButton("❌ Cancel", callback_data='cancel'),
        ]
    ]
    
    await query.edit_message_text(
        "🔧 *Maintenance Mode*\n\n"
        "This will update:\n"
        "• System packages (apt)\n"
        "• Flatpak applications\n\n"
        "⚠️ This may take several minutes.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return CONFIRMATION


async def proceed_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Run system updates."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    config = user_data[user_id]['config']
    
    await query.edit_message_text(
        "🔧 Starting system updates...\n"
        "⏳ Running apt update..."
    )
    
    # Run updates in background
    asyncio.create_task(
        run_update_process(update, context, config)
    )
    
    return COPYING


async def run_update_process(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict):
    """Run the actual update process."""
    query = update.callback_query
    user_id = update.effective_user.id
    updater = SystemUpdater(config['pi'])
    
    class TelegramConsole:
        def __init__(self, message):
            self.message = message
        
        def print(self, text):
            import re
            clean_text = re.sub(r'\[/?[^\]]+\]', '', text)
            if any(keyword in clean_text.lower() for keyword in ['completed', 'error', 'failed', 'success', '✓']):
                asyncio.create_task(
                    self.message.edit_text(
                        f"{self.message.text}\n{clean_text}"
                    )
                )
    
    console = TelegramConsole(query.message)
    
    try:
        success = updater.perform_updates(console, dry_run=False)
        
        if success:
            await query.message.edit_text(
                "✅ *All updates completed successfully!*",
                parse_mode='Markdown'
            )
        else:
            await query.message.edit_text(
                "⚠️ *Some updates failed*\nCheck the output above.",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Update error: {e}")
        await query.message.edit_text(
            f"❌ *Error during updates:*\n`{str(e)}`",
            parse_mode='Markdown'
        )
    finally:
        if user_id in user_data:
            del user_data[user_id]


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the operation."""
    user_id = update.effective_user.id
    
    # Handle both callback queries and direct commands
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        edit_func = query.edit_message_text
    else:
        message = update.message
        edit_func = None
    
    # Close scanner if open
    if user_id in user_data and 'scanner' in user_data[user_id]:
        user_data[user_id]['scanner'].close()
    
    # Show cancel confirmation
    cancel_text = "❌ Operation cancelled."
    if edit_func:
        await edit_func(cancel_text)
    elif message:
        await message.reply_text(cancel_text)
    
    # Clean up user data
    if user_id in user_data:
        del user_data[user_id]
    
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message with available commands."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    help_text = (
        "🎬 RasPi Controller Bot - Commands\n\n"
        "Main Commands:\n"
        "/start - Start media copy operation\n"
        "/help - Show this help message\n"
        "/status - Check disk space on Pi\n"
        "/health - Check disk health (SMART)\n"
        "/services - Check Jellyfin/qBittorrent status\n"
        "/downloads - Show active downloads\n"
        "/pause - Pause all downloads\n"
        "/speed - Run internet speed test\n"
        "/search - Search downloads folder (e.g. /search Batman)\n"
        "/temp - Show CPU temperature\n"
        "/cpu - Show CPU load and top processes\n"
        "/memory - Show memory usage\n"
        "/backup - Run system backup manually\n"
        "/backupstatus - Show backup status and next scheduled\n"
        "/backupsetup - Cloud backup setup guide\n"
        "/notify - Toggle download finish alerts\n"
        "/dryrun - Toggle dry-run mode (preview before copying)\n"
        "/idea - Save a new idea (e.g. /idea Buy more storage)\n"
        "/ideas - List all ideas by day\n"
        "/finish - Mark idea as done (e.g. /finish 3)\n"
        "/group - Check TV show grouping in media folder (e.g. /group Modern Family)\n"
        "/reboot - Reboot the Pi\n"
        "/cancel - Cancel current operation\n\n"
        "How to use:\n"
        "1. Use /start to begin\n"
        "2. Select mode: 📁 Internal, 💻 External, or 🔧 Update\n"
        "3. Tap items to select/deselect ☑/⬜\n"
        "4. Confirm and execute\n\n"
        "Tips:\n"
        "• Bot checks disk space before copying\n"
        "• Use /status anytime to check free space\n"
        "• Use /health to check disk health (SMART)\n"
        "• Updates require sudo_password in config"
    )
    await update.message.reply_text(help_text)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check disk health using smartctl."""
    config = context.bot_data.get('config')
    if not config:
        await update.message.reply_text("❌ Config not loaded.")
        return
        
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    await update.message.reply_text("🔍 Checking disk health...")
    
    import paramiko
    pi_config = config['pi']
    
    sudo_password = pi_config.get('sudo_password', '')
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        # Detect existing drives first
        _, out, _ = ssh.exec_command('lsblk -dno NAME,TYPE | grep disk')
        detected = [f"/dev/{line.split()[0]}" for line in out.read().decode().strip().splitlines() if line.strip()]
        drives = detected if detected else ['/dev/sda', '/dev/sdb']
        health_info = []
        
        for drive in drives:
            try:
                # Get full SMART attributes
                stdin, stdout, _ = ssh.exec_command(f'sudo -S smartctl -A -H {drive} 2>/dev/null', get_pty=True)
                if sudo_password:
                    stdin.write(sudo_password + '\n')
                    stdin.flush()
                output = stdout.read().decode()
                
                if not output or 'No such device' in output or 'Unable to detect' in output:
                    continue
                
                # Overall health
                passed = 'PASSED' in output
                failed = 'FAILED' in output
                
                # Parse key SMART attributes
                issues = 0
                max_issues = 0
                temp = None
                reallocated = 0
                pending = 0
                uncorrectable = 0
                power_on_hours = None
                wear_level = None  # SSD wear % (100 = new, 0 = worn out)
                
                for line in output.splitlines():
                    parts = line.split()
                    # SMART attribute lines: ID# ATTRIBUTE_NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
                    if len(parts) < 10:
                        continue
                    try:
                        int(parts[0])  # first col is numeric ID
                    except ValueError:
                        continue
                    attr_name = parts[1].lower()
                    raw_val = parts[9]  # RAW_VALUE is always column index 9
                    
                    try:
                        raw_int = int(raw_val.split()[0])
                    except (ValueError, IndexError):
                        raw_int = 0
                    
                    if 'reallocated' in attr_name and 'sector' in attr_name:
                        reallocated = raw_int
                        max_issues += 1
                        if raw_int > 0:
                            issues += 1
                    elif 'pending' in attr_name:
                        pending = raw_int
                        max_issues += 1
                        if raw_int > 0:
                            issues += 1
                    elif 'uncorrectable' in attr_name or 'offline_uncorrect' in attr_name:
                        uncorrectable = raw_int
                        max_issues += 1
                        if raw_int > 0:
                            issues += 1
                    elif 'temperature' in attr_name or 'airflow_temp' in attr_name:
                        temp = raw_int
                    elif 'power_on_hours' in attr_name or 'power_on_time' in attr_name:
                        power_on_hours = raw_int
                    elif 'wear_level' in attr_name or 'wearout' in attr_name or 'ssd_life' in attr_name:
                        # Most SSDs report wear in VALUE column (100=new, 0=dead)
                        # Raw value sometimes has vendor-specific format, so use VALUE
                        wear_level = int(parts[3]) if len(parts) > 3 else None
                
                # Calculate sanity score
                if failed:
                    score = 0
                elif max_issues > 0:
                    score = max(0, round((1 - issues / max_issues) * 100))
                else:
                    score = 100 if passed else 70
                
                # Score emoji
                if score >= 90:
                    score_emoji = "🟢"
                elif score >= 60:
                    score_emoji = "🟡"
                else:
                    score_emoji = "🔴"
                
                # Build drive summary
                bar_filled = round(score / 10)
                bar = "█" * bar_filled + "░" * (10 - bar_filled)
                lines = [f"{score_emoji} *{drive}* — {score}% healthy", f"`[{bar}]`"]
                # Always show sector counts
                sector_emoji = "✅" if reallocated == 0 else "⚠️"
                lines.append(f"  {sector_emoji} Reallocated sectors: {reallocated}")
                pending_emoji = "✅" if pending == 0 else "⚠️"
                lines.append(f"  {pending_emoji} Pending sectors: {pending}")
                uncorr_emoji = "✅" if uncorrectable == 0 else "❌"
                lines.append(f"  {uncorr_emoji} Uncorrectable: {uncorrectable}")
                if temp is not None:
                    temp_emoji = "🌡️" if temp < 50 else "🔥"
                    lines.append(f"  {temp_emoji} Temp: {temp}°C")
                if wear_level is not None:
                    wear_emoji = "💚" if wear_level >= 80 else "💛" if wear_level >= 50 else "❤️"
                    lines.append(f"  {wear_emoji} SSD Wear: {wear_level}% remaining")
                if power_on_hours is not None:
                    lines.append(f"  ⏱️ Power-on: {power_on_hours}h ({power_on_hours // 24}d)")
                
                health_info.append("\n".join(lines))
                
            except Exception:
                pass
        
        ssh.close()
        
        if health_info:
            result = "🩺 *Disk Health Report*\n\n" + "\n\n".join(health_info)
        else:
            result = "⚠️ Could not retrieve disk health.\nMake sure smartmontools is installed:\n`sudo apt install smartmontools`"
        
        await update.message.reply_text(result, parse_mode='Markdown')
        
    except Exception as e:
        error_type = type(e).__name__
        await update.message.reply_text(f"❌ Error checking health: {error_type}: {str(e)}")


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check if Jellyfin and qBittorrent services are running."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    await update.message.reply_text("🔍 Checking services...")
    
    import paramiko
    pi_config = config['pi']
    sudo_password = pi_config.get('sudo_password', '')
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        # Check common service names
        services = {
            'Jellyfin': ['jellyfin'],
            'qBittorrent': ['qbittorrent-nox', 'qbittorrent'],
            'Plex': ['plexmediaserver'],
            'Samba': ['smbd'],
        }
        
        results = []
        for name, possible_names in services.items():
            status = None
            for svc in possible_names:
                try:
                    # Check active state directly - no sudo needed for is-active
                    _, stdout, _ = ssh.exec_command(f'systemctl is-active {svc} 2>/dev/null')
                    output = stdout.read().decode().strip()
                    if output == 'active':
                        status = "✅ Running"
                        break
                    elif output in ('inactive', 'failed', 'activating', 'deactivating'):
                        status = "❌ Stopped"
                        break
                    # empty output means service doesn't exist, try next name
                except Exception:
                    pass
            if status is None:
                status = "⚫ Not installed"
            results.append(f"{name}: {status}")
        
        # Additional check: Try qBittorrent Web UI directly as fallback
        qb_config = config.get('qbittorrent', {})
        if qb_config.get('host') and qb_config.get('password'):
            try:
                base_url = f"http://{qb_config['host']}:{qb_config.get('port', 8080)}/api/v2"
                async with httpx.AsyncClient(timeout=5) as client:
                    login_resp = await client.post(
                        f"{base_url}/auth/login",
                        data={'username': qb_config['username'], 'password': qb_config['password']}
                    )
                    if login_resp.status_code == 200:
                        # qBittorrent Web UI is accessible - it's running
                        for i, line in enumerate(results):
                            if line.startswith('qBittorrent:'):
                                results[i] = "qBittorrent: ✅ Running (via Web UI)"
                                break
            except Exception:
                pass  # Web UI not accessible, keep previous status
        
        ssh.close()
        
        result = "🔧 *Services Status*\n\n" + "\n".join(results)
        await update.message.reply_text(result, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error checking services: {str(e)}")


async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reboot the Pi remotely with confirmation."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    # Check if this is a confirmation
    args = context.args
    if args and args[0] == 'confirm':
        # Execute reboot
        await update.message.reply_text("🔄 Rebooting Pi...")
        
        import paramiko
        pi_config = config['pi']
        sudo_password = pi_config.get('sudo_password', '')
        
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                pi_config['host'],
                port=pi_config.get('port', 22),
                username=pi_config['user'],
                password=pi_config.get('password'),
                key_filename=pi_config.get('key_path')
            )
            
            stdin, _, _ = ssh.exec_command('sudo -S reboot 2>&1', get_pty=True)
            if sudo_password:
                stdin.write(sudo_password + '\n')
                stdin.flush()
            ssh.close()
            
            await update.message.reply_text("✅ Reboot command sent. The Pi will be offline for ~30 seconds.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
    else:
        # Ask for confirmation
        keyboard = [
            [InlineKeyboardButton("✅ Yes, Reboot", callback_data='reboot_confirm')],
            [InlineKeyboardButton("❌ Cancel", callback_data='reboot_cancel')]
        ]
        await update.message.reply_text(
            "⚠️ *Reboot Confirmation*\n\nAre you sure you want to reboot the Pi?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def reboot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle reboot confirmation/cancel."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'reboot_confirm':
        await query.edit_message_text("🔄 Rebooting Pi...")
        
        import paramiko
        config = context.bot_data.get('config')
        pi_config = config['pi']
        sudo_password = pi_config.get('sudo_password', '')
        
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                pi_config['host'],
                port=pi_config.get('port', 22),
                username=pi_config['user'],
                password=pi_config.get('password'),
                key_filename=pi_config.get('key_path')
            )
            
            stdin, _, _ = ssh.exec_command('sudo -S reboot 2>&1', get_pty=True)
            if sudo_password:
                stdin.write(sudo_password + '\n')
                stdin.flush()
            ssh.close()
            
            await query.edit_message_text("✅ Reboot command sent.\nThe Pi will be offline for ~30-60 seconds.")
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {str(e)}")
    else:
        await query.edit_message_text("❌ Reboot cancelled.")


async def downloads_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current qBittorrent download status."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    qb_config = config.get('qbittorrent', {})
    if not qb_config.get('host') or not qb_config.get('password'):
        await update.message.reply_text(
            "⚠️ qBittorrent not configured.\n\n"
            "Add to config.yaml:\n"
            "```\n"
            "qbittorrent:\n"
            "  host: localhost\n"
            "  port: 8080\n"
            "  username: admin\n"
            "  password: your_password\n"
            "```"
        )
        return
    
    await update.message.reply_text("📥 Checking downloads...")
    
    try:
        base_url = f"http://{qb_config['host']}:{qb_config.get('port', 8080)}/api/v2"
        
        # Create client with cookie persistence
        async with httpx.AsyncClient(timeout=10) as client:
            # Login
            login_resp = await client.post(
                f"{base_url}/auth/login",
                data={
                    'username': qb_config['username'],
                    'password': qb_config['password']
                }
            )
            
            if login_resp.status_code != 200 or login_resp.text != 'Ok.':
                await update.message.reply_text("❌ Failed to connect to qBittorrent. Check credentials.")
                return
            
            # Get torrents using same session (cookies auto-preserved)
            torrents_resp = await client.get(f"{base_url}/torrents/info")
            
            if torrents_resp.status_code != 200:
                await update.message.reply_text("❌ Failed to get torrent list.")
                return
            
            torrents = torrents_resp.json()
        
        # Filter to only show actively downloading torrents
        downloading_states = ['downloading', 'stalledDL', 'forcedDL', 'metaDL', 'queuedDL', 'checkingDL']
        active_downloads = [t for t in torrents if t.get('state') in downloading_states]
        
        if not active_downloads:
            await update.message.reply_text("📭 No active downloads (only seeding torrents found).")
            return
        
        # Build status message
        lines = ["📥 *Active Downloads*\n"]
        
        for t in active_downloads[:10]:  # Limit to 10 torrents
            name = t['name'][:30] + "..." if len(t['name']) > 30 else t['name']
            progress = t['progress'] * 100
            state = t['state']
            size = t['total_size'] / (1024**3)  # GB
            dlspeed = t['dlspeed'] / (1024**2)  # MB/s
            eta = t.get('eta', 86400)  # seconds, default to 24h
            
            # Format ETA
            if eta == 0:
                eta_str = "Almost done"
            elif eta >= 86400:
                eta_str = f"{eta // 86400}d remaining"
            elif eta >= 3600:
                eta_str = f"{eta // 3600}h remaining"
            else:
                eta_str = f"{eta // 60}m remaining"
            
            # Progress bar
            bar_filled = round(progress / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)
            
            lines.append(f"⬇️ *{name}*")
            lines.append(f"`[{bar}]` {progress:.1f}%")
            lines.append(f"  💨 {dlspeed:.1f} MB/s | ⏱️ {eta_str}")
            lines.append(f"  📊 {size:.1f} GB")
        
        if len(active_downloads) > 10:
            lines.append(f"\n... and {len(active_downloads) - 10} more downloading")
        
        result = "\n".join(lines)
        await update.message.reply_text(result, parse_mode='Markdown')
        
    except httpx.ConnectError:
        await update.message.reply_text("❌ Cannot connect to qBittorrent.\n\nCheck:\n1. Web UI is enabled\n2. Host/port are correct\n3. qBittorrent is running")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pause all active qBittorrent downloads."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    qb_config = config.get('qbittorrent', {})
    if not qb_config.get('host') or not qb_config.get('password'):
        await update.message.reply_text("⚠️ qBittorrent not configured.")
        return
    
    await update.message.reply_text("⏸️ Pausing downloads...")
    
    try:
        base_url = f"http://{qb_config['host']}:{qb_config.get('port', 8080)}/api/v2"
        
        async with httpx.AsyncClient(timeout=10) as client:
            login_resp = await client.post(
                f"{base_url}/auth/login",
                data={'username': qb_config['username'], 'password': qb_config['password']}
            )
            
            if login_resp.status_code != 200 or login_resp.text != 'Ok.':
                await update.message.reply_text("❌ Failed to connect to qBittorrent.")
                return
            
            await client.post(f"{base_url}/torrents/pause", data={'hashes': 'all'})
            
        await update.message.reply_text("✅ All downloads paused.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def speed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run internet speed test on the Pi."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    await update.message.reply_text("🌐 Running speed test (30-60s)...")
    
    import paramiko
    pi_config = config['pi']
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        stdin, stdout, _ = ssh.exec_command('which speedtest-cli || echo "not_installed"')
        check = stdout.read().decode().strip()
        
        if 'not_installed' in check:
            await update.message.reply_text("📦 Installing speedtest-cli first...")
            ssh.exec_command('sudo apt update && sudo apt install -y speedtest-cli')
        
        stdin, stdout, stderr = ssh.exec_command('speedtest-cli --simple', timeout=90)
        output = stdout.read().decode().strip()
        
        ssh.close()
        
        if output:
            lines = output.splitlines()
            result = "🚀 *Speed Test Results*\n\n"
            for line in lines:
                if 'Ping' in line:
                    result += f"📍 {line}\n"
                elif 'Download' in line:
                    result += f"⬇️ {line}\n"
                elif 'Upload' in line:
                    result += f"⬆️ {line}\n"
            await update.message.reply_text(result, parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Speed test failed. Try installing manually: `sudo apt install speedtest-cli`")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search for media in downloads folder."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("🔍 Usage: `/search <movie or show name>`")
        return
    
    query = ' '.join(context.args).lower()
    await update.message.reply_text(f"🔍 Searching downloads for '*{query}*'...", parse_mode='Markdown')
    
    import paramiko
    pi_config = config['pi']
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        downloads_path = config['paths']['downloads']
        
        # Verify path exists
        _, stdout, _ = ssh.exec_command(f'test -d "{downloads_path}" && echo "ok" || echo "missing"')
        path_ok = stdout.read().decode().strip() == 'ok'
        
        if not path_ok:
            await update.message.reply_text(
                f"❌ Downloads path not found on Pi: `{downloads_path}`\n\nCheck `paths.downloads` in config.yaml",
                parse_mode='Markdown'
            )
            ssh.close()
            return
        
        results = []
        seen_titles = set()
        
        # Find all matching items
        _, stdout, _ = ssh.exec_command(rf'find "{downloads_path}" -maxdepth 3 \( -type d -o -type f \) 2>/dev/null | grep -iv "sample\|featurette\|\.srt\|\.sub\|\.nfo\|\.jpg\|\.png" | grep -i "{query}" | head -30')
        
        for item in stdout.read().decode().strip().splitlines():
            if not item or item == downloads_path:
                continue
            
            # Get the relative path from downloads
            rel_path = item[len(downloads_path):].lstrip('/')
            parts = rel_path.split('/')
            
            # Extract title: first folder for shows, parent folder or filename for movies
            if len(parts) >= 2 and '.' not in parts[0]:
                # It's a show folder with seasons/episodes inside
                title = parts[0]
                icon = "📺"
            else:
                # It's a movie file or loose file
                title = parts[0]
                # Remove year and quality tags like (2023), [1080p], etc.
                title = re.sub(r'[\(\[\{]\d{4}[\)\]\}]', '', title)  # Remove (2023) [2023] {2023}
                title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title)   # Remove [1080p] (WEB-DL) etc
                title = title.replace('.', ' ').replace('_', ' ').strip()
                icon = "🎬"
            
            # Normalize for deduplication
            clean_title = ' '.join(title.lower().split())
            if clean_title and clean_title not in seen_titles:
                seen_titles.add(clean_title)
                results.append(f"{icon} {title.strip()}")
        
        ssh.close()
        
        if results:
            result_text = f"✅ *Found in downloads:*\n\n" + "\n".join(results[:15])
            if len(results) > 15:
                result_text += f"\n\n_...and {len(results) - 15} more_"
        else:
            result_text = f"❌ '*{query}*' not found in downloads folder"
        
        await update.message.reply_text(result_text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle download completion notifications."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    current = user_data.get(user_id, {}).get('notifications', False)
    new_setting = not current
    
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['notifications'] = new_setting
    save_user_data()  # Persist the setting
    
    status = "✅ enabled" if new_setting else "❌ disabled"
    await update.message.reply_text(f"🔔 Notifications {status}. You'll get alerts when downloads finish.")


async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a new idea."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("💡 Usage: `/idea <your idea text>`")
        return
    
    idea_text = ' '.join(context.args)
    now = datetime.now()
    
    idea = {
        'id': len(ideas_data) + 1,
        'text': idea_text,
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M'),
        'status': 'pending',
        'user_id': user_id
    }
    ideas_data.append(idea)
    save_ideas()
    
    await update.message.reply_text(f"💡 Idea #{idea['id']} saved for {idea['date']}!")


async def ideas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List ideas by day."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not ideas_data:
        await update.message.reply_text("💡 No ideas saved yet. Use `/idea <text>` to add one.")
        return
    
    # Group by date
    by_date = {}
    for idea in ideas_data:
        date = idea['date']
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(idea)
    
    # Build output
    lines = ["💡 *Your Ideas*\n"]
    for date in sorted(by_date.keys(), reverse=True):
        lines.append(f"\n📅 *{date}*")
        for idea in by_date[date]:
            status_icon = "✅" if idea['status'] == 'done' else "⏳"
            lines.append(f"  {status_icon} #{idea['id']}: {idea['text']}")
    
    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def finish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark an idea as finished."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("✅ Usage: `/finish <idea number>`\nUse `/ideas` to see numbers.")
        return
    
    try:
        idea_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid idea number.")
        return
    
    for idea in ideas_data:
        if idea['id'] == idea_id:
            idea['status'] = 'done'
            save_ideas()
            await update.message.reply_text(f"✅ Idea #{idea_id} marked as finished!")
            return
    
    await update.message.reply_text(f"❌ Idea #{idea_id} not found.")


async def group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search and group/fix TV shows in the media folder."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    args = context.args or []
    fix_mode = args and args[0].lower() == 'fix'
    
    if fix_mode:
        search_term = ' '.join(args[1:]) if len(args) > 1 else None
        progress_msg = await update.message.reply_text("🔧 Analyzing TV shows for reorganization...")
    else:
        search_term = ' '.join(args) if args else None
        await update.message.reply_text("🔍 Scanning TV Shows folder...")
    
    import os
    import re
    import shutil
    from collections import defaultdict
    
    shows_path = config['paths'].get('jellyfin_shows') or config['paths'].get('jellyfin_tv')
    
    if not shows_path or not os.path.isdir(shows_path):
        await update.message.reply_text(f"❌ Shows folder not found: {shows_path}")
        return
    
    # Normalize show name for grouping — extract base title only
    def normalize(name):
        # Remove anything in brackets/parentheses: [WEBDL...], (2025), [PACK]...
        name = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', name)
        # Remove season markers: S01, Season 1, S01E01
        name = re.sub(r'\s*[Ss]\d{1,2}([Ee]\d{1,2})?\b.*', '', name)
        name = re.sub(r'\s*[Ss]eason\s*\d{1,2}\b.*', '', name, flags=re.IGNORECASE)
        # Remove common release tags that may appear without brackets
        name = re.sub(r'\s*(WEBDL|WEB-DL|BluRay|HDTV|HDO|PACK|1080p|720p|x264|x265|AVC|HEVC|AAC|AC3|DD\+?|EAC3)\b.*', '', name, flags=re.IGNORECASE)
        # Strip leftover separators and whitespace
        name = re.sub(r'[\s\-_\.]+$', '', name.strip())
        # Return lowercase alphanumeric only for comparison
        return re.sub(r'[^a-z0-9]', '', name.lower())
    
    # Group folders by normalized show name
    groups = defaultdict(list)
    try:
        for entry in os.scandir(shows_path):
            if entry.is_dir():
                norm = normalize(entry.name)
                if norm:
                    groups[norm].append(entry.name)
    except Exception as e:
        await update.message.reply_text(f"❌ Error scanning: {str(e)}")
        return
    
    # Filter to groups with multiple folders (potential issues) or matching search
    results = []
    for norm, folders in groups.items():
        if search_term:
            # Include if search matches any folder name
            if any(search_term.lower() in f.lower() for f in folders):
                results.append((norm, folders))
        else:
            # Include if multiple folders for same show (grouping needed)
            if len(folders) > 1:
                results.append((norm, folders))
    
    if not results:
        if search_term:
            await update.message.reply_text(f"🔍 No shows matching '{search_term}' found.")
        else:
            await update.message.reply_text("✅ No duplicate series folders found. All shows are properly grouped!")
        return
    
    if not fix_mode:
        # Build report only
        lines = ["📺 *TV Show Grouping Report*\n"]
        if search_term:
            lines.append(f"Search: `{search_term}`\n")
        
        for norm, folders in sorted(results, key=lambda x: x[0]):
            # Derive the clean target name for display
            clean = None
            for f in sorted(folders):
                if re.search(r'\(\d{4}\)', f):
                    clean = f
                    break
            if not clean:
                clean = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', sorted(folders)[0])
                clean = re.sub(r'\s*[Ss]\d{1,2}([Ee]\d{1,2})?\b.*', '', clean)
                clean = re.sub(r'[\s\-_]+$', '', clean.strip())
            lines.append(f"\n*{clean}*")
            if len(folders) > 1:
                lines.append(f"⚠️ {len(folders)} separate folders → will merge into `{clean}/`:")
            for f in sorted(folders):
                lines.append(f"  📁 `{f}`")
        
        lines.append("\n\n💡 Use `/group fix [Show Name]` to reorganize automatically.")
        
        text = '\n'.join(lines)
        if len(text) > 4000:
            text = text[:3997] + '...'
        
        await update.message.reply_text(text, parse_mode='Markdown')
        return
    
    # FIX MODE: Actually reorganize folders with progress updates
    moves_made = []
    errors = []
    total_shows = len([r for r in results if len(r[1]) > 1])
    processed = 0
    
    async def update_progress(status_text):
        try:
            await progress_msg.edit_text(f"🔧 Reorganizing... ({processed}/{total_shows})\n{status_text}")
        except Exception:
            pass  # Ignore edit errors
    
    for norm, folders in results:
        if len(folders) < 2:
            continue  # Nothing to fix
        
        processed += 1
        
        # Find the best target folder (prefer one with year in name, e.g. "Show (2020)")
        target = None
        for f in sorted(folders):
            if re.search(r'\(\d{4}\)', f):
                target = f
                break
        
        if not target:
            # No clean folder exists — derive a clean name from the first folder
            base_name = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', sorted(folders)[0])
            base_name = re.sub(r'\s*[Ss]\d{1,2}([Ee]\d{1,2})?\b.*', '', base_name)
            base_name = re.sub(r'\s*(WEBDL|WEB-DL|BluRay|HDTV|HDO|PACK|1080p|720p|x264|x265|AVC|HEVC)\b.*', '', base_name, flags=re.IGNORECASE)
            base_name = re.sub(r'[\s\-_]+$', '', base_name.strip())
            target = base_name if base_name else sorted(folders)[0]
        
        await update_progress(f"Processing: *{target}* — {len(folders)} folders")
        
        target_path = os.path.join(shows_path, target)
        os.makedirs(target_path, exist_ok=True)
        
        for folder in folders:
            if folder == target:
                continue  # Skip the target folder itself
            
            source_path = os.path.join(shows_path, folder)
            
            try:
                # Extract season number from the folder name itself (e.g. "Modern Family S02 [...]")
                season_match = re.search(r'[Ss](\d{1,2})\b', folder)
                season_subfolders = [e for e in os.scandir(source_path)
                                     if e.is_dir() and re.match(r'[Ss]eason\s*\d+', e.name, re.IGNORECASE)]
                
                if season_subfolders:
                    # Case A: source contains Season XX subfolders — move each one
                    for entry in season_subfolders:
                        dest_season_path = os.path.join(target_path, entry.name)
                        if os.path.exists(dest_season_path):
                            counter = 1
                            while os.path.exists(f"{dest_season_path}_old{counter}"):
                                counter += 1
                            dest_season_path = f"{dest_season_path}_old{counter}"
                        shutil.move(entry.path, dest_season_path)
                        moves_made.append(f"{folder}/{entry.name} → {target}/")
                elif season_match:
                    # Case B: source IS a season folder (flat episodes inside)
                    # Move the whole folder as Season XX inside target
                    season_num = season_match.group(1).zfill(2)
                    dest_season_path = os.path.join(target_path, f"Season {season_num}")
                    if os.path.exists(dest_season_path):
                        counter = 1
                        while os.path.exists(f"{dest_season_path}_old{counter}"):
                            counter += 1
                        dest_season_path = f"{dest_season_path}_old{counter}"
                    shutil.move(source_path, dest_season_path)
                    moves_made.append(f"{folder} → {target}/Season {season_num}/")
                    continue  # source_path is gone, skip cleanup
                else:
                    moves_made.append(f"⏭️ Skipped {folder} (no season info found)")
                    continue
                
                # Check if source is now empty
                remaining = list(os.scandir(source_path))
                if not remaining:
                    os.rmdir(source_path)
                    moves_made.append(f"🗑 Removed empty folder: {folder}")
                else:
                    moves_made.append(f"⚠️ Left non-empty folder: {folder}")
                    
            except Exception as e:
                errors.append(f"Error processing {folder}: {e}")
    
    # Build detailed result report
    actual_moves = [m for m in moves_made if '→' in m]
    removed = [m for m in moves_made if '🗑' in m]
    skipped = [m for m in moves_made if '⏭️' in m or '⚠️' in m]
    
    if actual_moves or removed:
        lines = ["✅ *Reorganization Complete*\n"]
        lines.append(f"📺 Shows processed: {processed}")
        lines.append(f"📁 Seasons moved: {len(actual_moves)}")
        lines.append(f"🗑 Empty folders removed: {len(removed)}")
        if skipped:
            lines.append(f"⏭️ Skipped: {len(skipped)}\n")
        else:
            lines.append("")
        
        if actual_moves:
            lines.append("*Moves:*")
            for move in actual_moves[:15]:
                lines.append(f"• {move}")
            if len(actual_moves) > 15:
                lines.append(f"... and {len(actual_moves) - 15} more")
        
        if removed and len(actual_moves) <= 10:
            lines.append("\n*Cleanup:*")
            for r in removed[:5]:
                lines.append(f"• {r}")
    else:
        lines = ["ℹ️ *No reorganization needed*\n"]
        lines.append("All seasons are already properly grouped.")
    
    if errors:
        lines.append(f"\n⚠️ *Errors ({len(errors)}):*")
        for err in errors[:5]:
            lines.append(f"• {err}")
        if len(errors) > 5:
            lines.append(f"... and {len(errors) - 5} more")
    
    # Refresh Jellyfin after reorganization
    lines.append("\n🔄 Refreshing Jellyfin library...")
    await progress_msg.edit_text('\n'.join(lines), parse_mode='Markdown')
    
    try:
        from jellyfin import refresh_jellyfin_library
        jellyfin_config = config.get('jellyfin', {})
        refresh_success = refresh_jellyfin_library(
            host=jellyfin_config.get('host', config['pi']['host']),
            port=jellyfin_config.get('port', 8096),
            api_key=jellyfin_config.get('api_key')
        )
        if refresh_success:
            await update.message.reply_text("✅ Jellyfin refreshed successfully!")
        else:
            await update.message.reply_text("⚠️ Jellyfin refresh failed. You may need to refresh manually.")
    except Exception as e:
        logger.warning(f"Jellyfin refresh failed: {e}")
        await update.message.reply_text("⚠️ Could not refresh Jellyfin automatically.")


async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show CPU temperature."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    import paramiko
    pi_config = config['pi']
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        # Get temperature
        _, stdout, _ = ssh.exec_command('cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "N/A"')
        temp_raw = stdout.read().decode().strip()
        
        if temp_raw != "N/A":
            temp_c = int(temp_raw) / 1000
            # Determine status
            if temp_c < 60:
                status = "✅ Normal"
                icon = "🌡️"
            elif temp_c < 75:
                status = "⚠️ Warm"
                icon = "🌡️"
            elif temp_c < 85:
                status = "🔥 Hot"
                icon = "🌡️"
            else:
                status = "❌ Critical"
                icon = "🌡️"
            
            result = f"{icon} *CPU Temperature*\n\n{temp_c:.1f}°C ({temp_c * 9/5 + 32:.1f}°F)\n{status}"
        else:
            result = "❌ Could not read temperature sensor"
        
        ssh.close()
        await update.message.reply_text(result, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def cpu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show CPU load and processes."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    import paramiko
    pi_config = config['pi']
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        # Get CPU info
        _, stdout, _ = ssh.exec_command("uptime | awk -F'load average:' '{print $2}'")
        load_avg = stdout.read().decode().strip()
        
        _, stdout, _ = ssh.exec_command("nproc")
        cores = stdout.read().decode().strip()
        
        # Get CPU usage percentage
        _, stdout, _ = ssh.exec_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
        cpu_percent = stdout.read().decode().strip()
        
        # Get top processes
        _, stdout, _ = ssh.exec_command("ps aux --sort=-%cpu | head -6 | tail -5 | awk '{printf \"%.1f%% %s\\n\", $3, $11}'")
        top_processes = stdout.read().decode().strip()
        
        result = f"⚙️ *CPU Status*\n\n"
        result += f"*Load Average:* {load_avg}\n"
        result += f"*Cores:* {cores}\n"
        if cpu_percent:
            result += f"*Usage:* {cpu_percent}%\n\n"
        result += f"*Top Processes:*\n```{top_processes}```"
        
        ssh.close()
        await update.message.reply_text(result, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show memory usage."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    import paramiko
    pi_config = config['pi']
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            pi_config['host'],
            port=pi_config.get('port', 22),
            username=pi_config['user'],
            password=pi_config.get('password'),
            key_filename=pi_config.get('key_path')
        )
        
        # Get memory info
        _, stdout, _ = ssh.exec_command("free -h | grep '^Mem:'")
        mem_line = stdout.read().decode().strip()
        
        if mem_line:
            parts = mem_line.split()
            total = parts[1]
            used = parts[2]
            free = parts[3]
            shared = parts[4]
            buff_cache = parts[5]
            available = parts[6]
            
            # Calculate percentage
            _, stdout, _ = ssh.exec_command("free | grep '^Mem:' | awk '{printf \"%.0f\", $3/$2 * 100.0}'")
            used_percent = stdout.read().decode().strip()
            
            # Progress bar
            filled = int(int(used_percent) / 10)
            bar = "█" * filled + "░" * (10 - filled)
            
            result = f"🧠 *Memory Usage*\n\n"
            result += f"{bar} {used_percent}%\n\n"
            result += f"*Total:* {total}\n"
            result += f"*Used:* {used}\n"
            result += f"*Free:* {free}\n"
            result += f"*Available:* {available}\n"
            result += f"*Cache:* {buff_cache}"
        else:
            result = "❌ Could not read memory info"
        
        ssh.close()
        await update.message.reply_text(result, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {str(e)}")


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run system backup manually."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    backup_config = config.get('backup', {})
    if not backup_config.get('enabled', False):
        await update.message.reply_text(
            "⚠️ Backups not enabled.\n\n"
            "Add to config.yaml:\n"
            "```\nbackup:\n  enabled: true\n  source_device: /dev/mmcblk0\n  local_path: /mnt/storage/backups\n```",
            parse_mode='Markdown'
        )
        return
    
    from backup import SystemBackup
    backup = SystemBackup(config)
    
    # Send initial message
    message = await update.message.reply_text("📦 Starting backup... This may take 15-30 minutes.")
    
    async def progress_callback(msg):
        try:
            await message.edit_text(f"📦 {msg}")
        except Exception:
            pass  # Ignore edit errors
    
    # Run backup in executor to not block
    loop = asyncio.get_event_loop()
    success, result_msg = await loop.run_in_executor(
        None, 
        lambda: backup.create_backup(lambda msg: asyncio.run_coroutine_threadsafe(progress_callback(msg), loop))
    )
    
    if success:
        await message.edit_text(f"✅ {result_msg}\n\nNext backup due in 30 days.")
        
        # Notify if configured
        if user_data.get(user_id, {}).get('notifications', False):
            # Could send additional notification
            pass
    else:
        await message.edit_text(f"❌ {result_msg}")


async def backupstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show backup status and next scheduled backup."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    backup_config = config.get('backup', {})
    if not backup_config.get('enabled', False):
        await update.message.reply_text(
            "⚠️ Backups not enabled.\n\n"
            "Add to config.yaml:\n"
            "```\nbackup:\n  enabled: true\n  source_device: /dev/mmcblk0\n  local_path: /mnt/storage/backups\n```",
            parse_mode='Markdown'
        )
        return
    
    from backup import SystemBackup
    backup = SystemBackup(config)
    status_text = backup.get_status_text()
    await update.message.reply_text(status_text, parse_mode='Markdown')


async def backupsetup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show setup instructions for cloud backups."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    from backup import setup_rclone_instructions
    instructions = setup_rclone_instructions()
    
    await update.message.reply_text(
        f"📋 *Google Drive Backup Setup*\n\n{instructions}",
        parse_mode='Markdown'
    )


async def dryrun_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle dry-run mode for copy operations."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    # Get current dry-run state (default from config)
    if user_id not in user_data:
        user_data[user_id] = {}
    
    current = user_data[user_id].get('dry_run', config.get('options', {}).get('dry_run', False))
    new_state = not current
    user_data[user_id]['dry_run'] = new_state
    
    status = "✅ *ENABLED*" if new_state else "❌ *DISABLED*"
    mode_text = "preview (no files will be copied)" if new_state else "live (files will be copied)"
    
    await update.message.reply_text(
        f"🧪 *Dry-Run Mode*: {status}\n\n"
        f"Copy operations will run in {mode_text} mode.\n\n"
        f"Use `/dryrun` again to toggle.",
        parse_mode='Markdown'
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show disk space status."""
    config = context.bot_data.get('config')
    allowed_users = config.get('telegram', {}).get('allowed_users', [])
    user_id = update.effective_user.id
    
    if not is_authorized(user_id, allowed_users):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    await update.message.reply_text("🔍 Checking disk space...")
    
    from scanner import FolderScanner
    scanner = FolderScanner(config['pi'])
    
    if not scanner.connect():
        await update.message.reply_text("❌ Failed to connect to Pi.")
        return
    
    try:
        downloads = config['paths']['downloads']
        shows = config['paths'].get('jellyfin_shows', '/mnt/media/Shows')
        movies = config['paths'].get('jellyfin_movies', '/mnt/media/Movies')
        
        dl_space = scanner.get_disk_space(downloads)
        shows_space = scanner.get_disk_space(shows)
        movies_space = scanner.get_disk_space(movies)
        
        status_text = (
            "📊 *Disk Space Status*\n\n"
            f"*Downloads:* {format_size(dl_space['available'])} free\n"
            f"*Shows:* {format_size(shows_space['available'])} free\n"
            f"*Movies:* {format_size(movies_space['available'])} free"
        )
        await update.message.reply_text(status_text, parse_mode='Markdown')
    finally:
        scanner.close()


def main():
    """Start the bot."""
    # Load config
    config = load_config()
    
    telegram_config = config.get('telegram', {})
    token = telegram_config.get('token')
    
    if not token:
        print("Error: Telegram bot token not configured in config.yaml")
        print("Add telegram.token to your config.yaml")
        sys.exit(1)
    
    # Load persistent data (user preferences and ideas)
    load_persistent_data()
    
    # Register bot commands in Telegram's command menu (shown when typing /)
    from telegram import BotCommand
    
    async def post_init(app):
        await app.bot.set_my_commands([
            BotCommand('start', 'Start media copy operation'),
            BotCommand('help', 'Show available commands'),
            BotCommand('status', 'Check disk space on Pi'),
            BotCommand('health', 'Check disk health (SMART)'),
            BotCommand('services', 'Check Jellyfin/qBittorrent status'),
            BotCommand('downloads', 'Show qBittorrent download status'),
            BotCommand('pause', 'Pause all downloads'),
            BotCommand('speed', 'Run internet speed test'),
            BotCommand('search', 'Search downloads folder'),
            BotCommand('temp', 'Show CPU temperature'),
            BotCommand('cpu', 'Show CPU load and processes'),
            BotCommand('memory', 'Show memory usage'),
            BotCommand('backup', 'Run system backup'),
            BotCommand('backupstatus', 'Show backup status'),
            BotCommand('backupsetup', 'Cloud backup setup'),
            BotCommand('notify', 'Toggle download alerts'),
            BotCommand('dryrun', 'Toggle dry-run mode'),
            BotCommand('idea', 'Save a new idea'),
            BotCommand('ideas', 'List all ideas by day'),
            BotCommand('finish', 'Mark idea as finished'),
            BotCommand('group', 'Check or fix TV show grouping'),
            BotCommand('reboot', 'Reboot the Pi'),
            BotCommand('cancel', 'Cancel current operation'),
        ])
        
        # Start auto-backup scheduler if enabled (runs after event loop starts)
        backup_config = config.get('backup', {})
        if backup_config.get('enabled', False) and backup_config.get('auto_backup', True):
            async def auto_backup_scheduler():
                """Run monthly backup check in background."""
                from backup import SystemBackup
                
                while True:
                    try:
                        backup = SystemBackup(config)
                        if backup.needs_backup():
                            logger.info("Monthly backup is due, starting auto-backup...")
                            success, msg = backup.create_backup()
                            if success:
                                logger.info(f"Auto-backup completed: {msg}")
                                # Notify all users with notifications enabled
                                for uid, data in user_data.items():
                                    if data.get('notifications', False):
                                        try:
                                            await app.bot.send_message(
                                                uid,
                                                f"📦 *Monthly Auto-Backup Completed*\n\n{msg}",
                                                parse_mode='Markdown'
                                            )
                                        except Exception as e:
                                            logger.warning(f"Failed to notify user {uid}: {e}")
                            else:
                                logger.error(f"Auto-backup failed: {msg}")
                        
                        # Check again in 24 hours
                        await asyncio.sleep(86400)
                    except Exception as e:
                        logger.error(f"Backup scheduler error: {e}")
                        await asyncio.sleep(3600)  # Wait 1 hour on error
            
            # Start the scheduler as a background task
            app.create_task(auto_backup_scheduler())
            logger.info("Auto-backup scheduler started (checking daily)")
    
    # Create application with post_init hook and store config
    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data['config'] = config
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MODE_SELECTION: [
                CallbackQueryHandler(mode_callback, pattern='^(internal|external|update)$'),
            ],
            CONTENT_SELECTION: [
                CallbackQueryHandler(toggle_selection, pattern='^toggle_'),
                CallbackQueryHandler(toggle_selection, pattern='^page_'),
                CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern='^noop$'),
                CallbackQueryHandler(confirm_selection, pattern='^confirm_selection$'),
                CallbackQueryHandler(cancel, pattern='^cancel$'),
            ],
            CONFIRMATION: [
                CallbackQueryHandler(proceed_copy, pattern='^proceed_copy$'),
                CallbackQueryHandler(proceed_update, pattern='^proceed_update$'),
                CallbackQueryHandler(cancel, pattern='^cancel$'),
            ],
            COPYING: [],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('help', help_command),
            CommandHandler('status', status_command),
            CommandHandler('health', health_command),
            CommandHandler('services', services_command),
            CommandHandler('downloads', downloads_command),
            CommandHandler('pause', pause_command),
            CommandHandler('speed', speed_command),
            CommandHandler('search', search_command),
            CommandHandler('notify', notify_command),
            CommandHandler('temp', temp_command),
            CommandHandler('cpu', cpu_command),
            CommandHandler('memory', memory_command),
            CommandHandler('backup', backup_command),
            CommandHandler('backupstatus', backupstatus_command),
            CommandHandler('backupsetup', backupsetup_command),
            CommandHandler('dryrun', dryrun_command),
            CommandHandler('idea', idea_command),
            CommandHandler('ideas', ideas_command),
            CommandHandler('finish', finish_command),
            CommandHandler('group', group_command),
            CallbackQueryHandler(cancel, pattern='^cancel$'),
        ],
    )
    
    application.add_handler(conv_handler)
    
    # Add reboot callback handler outside conversation (must be before other handlers)
    application.add_handler(CallbackQueryHandler(reboot_callback, pattern='^reboot_'))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('health', health_command))
    application.add_handler(CommandHandler('services', services_command))
    application.add_handler(CommandHandler('downloads', downloads_command))
    application.add_handler(CommandHandler('pause', pause_command))
    application.add_handler(CommandHandler('speed', speed_command))
    application.add_handler(CommandHandler('search', search_command))
    application.add_handler(CommandHandler('notify', notify_command))
    application.add_handler(CommandHandler('temp', temp_command))
    application.add_handler(CommandHandler('cpu', cpu_command))
    application.add_handler(CommandHandler('memory', memory_command))
    application.add_handler(CommandHandler('backup', backup_command))
    application.add_handler(CommandHandler('backupstatus', backupstatus_command))
    application.add_handler(CommandHandler('backupsetup', backupsetup_command))
    application.add_handler(CommandHandler('dryrun', dryrun_command))
    application.add_handler(CommandHandler('idea', idea_command))
    application.add_handler(CommandHandler('ideas', ideas_command))
    application.add_handler(CommandHandler('finish', finish_command))
    application.add_handler(CommandHandler('group', group_command))
    application.add_handler(CommandHandler('reboot', reboot_command))
    
    # Run the bot
    print("Starting Telegram bot...")
    print("Available: /start, /help, /status, /health, /services, /downloads, /pause, /speed, /search, /temp, /cpu, /memory, /backup, /backupstatus, /backupsetup, /notify, /dryrun, /idea, /ideas, /finish, /reboot, /cancel")
    print("Press Ctrl+C to stop")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
