"""
Telegram Bot for Jellyfin Media Copy
Run this on your Raspberry Pi to control media operations from your phone.
"""

import os
import sys
import yaml
import logging
import asyncio
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

# Store user data
user_data: Dict[int, dict] = {}


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
    scanner = FolderScanner(config['pi'])
    
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
    
    summary = (
        f"📊 *Selection Summary*\n\n"
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
        "⏳ Copying... (this may take a while)"
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
    
    try:
        if mode == 'internal':
            copier = RsyncCopier(config['pi'], config['paths'], config['options'])
        else:
            local_paths = {
                'local_destination': config['paths'].get('local_destination', './downloads')
            }
            copier = ExternalCopier(config['pi'], local_paths, config['options'])
        
        # Create a simple console-like object for copier
        class TelegramConsole:
            def __init__(self, message, context):
                self.message = message
                self.context = context
            
            def print(self, text):
                # Strip rich formatting tags
                import re
                clean_text = re.sub(r'\[/?[^\]]+\]', '', text)
                # Don't spam updates, only on significant messages
                if any(keyword in clean_text.lower() for keyword in ['completed', 'error', 'failed', 'success']):
                    asyncio.create_task(
                        self.message.edit_text(
                            f"{self.message.text}\n{clean_text}"
                        )
                    )
        
        console = TelegramConsole(query.message, context)
        success = copier.copy_items(selected, console)
        
        if success:
            result_text = (
                "✅ *Copy completed successfully!*\n\n"
                f"Copied {len(selected)} item(s)"
            )
            
            # Refresh Jellyfin for internal mode
            if mode == 'internal':
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
        logger.error(f"Copy error: {e}")
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
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id in user_data and 'scanner' in user_data[user_id]:
        user_data[user_id]['scanner'].close()
    
    await query.edit_message_text("❌ Operation cancelled.")
    
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
        "/cancel - Cancel current operation\n\n"
        "How to use:\n"
        "1. Use /start to begin\n"
        "2. Select mode: 📁 Internal, 💻 External, or 🔧 Update\n"
        "3. Tap items to select/deselect ☑/⬜\n"
        "4. Confirm and execute\n\n"
        "Tips:\n"
        "• Bot checks disk space before copying\n"
        "• Use /status anytime to check free space\n"
        "• Updates require sudo_password in config"
    )
    await update.message.reply_text(help_text)


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
    
    # Register bot commands in Telegram's command menu (shown when typing /)
    from telegram import BotCommand
    
    async def post_init(app):
        await app.bot.set_my_commands([
            BotCommand('start', 'Start media copy operation'),
            BotCommand('help', 'Show available commands'),
            BotCommand('status', 'Check disk space on Pi'),
            BotCommand('cancel', 'Cancel current operation'),
        ])
    
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
            CallbackQueryHandler(cancel, pattern='^cancel$'),
        ],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('status', status_command))
    
    # Run the bot
    print("Starting Telegram bot...")
    print("Available commands: /start, /help, /status, /cancel")
    print("Press Ctrl+C to stop")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
