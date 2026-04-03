# Jellyfin Media Copy

A Python script to copy TV series and movies from qBittorrent downloads to your Jellyfin library on a Raspberry Pi. Supports two modes: internal copy within the Pi or external copy to your local laptop.

## Features

- **Two Operation Modes:**
  - **Internal**: Copy files within the Raspberry Pi (downloads → Jellyfin library)
  - **External**: Copy files from Raspberry Pi to your local laptop
- **Content Detection**: Automatically identifies TV shows (with season numbers) vs movies
- **Interactive TUI**: Select which content to copy with an intuitive checkbox interface
- **Progress Monitoring**: Real-time progress bars during copy operations
- **Jellyfin Integration**: Automatically refreshes Jellyfin library after internal copies
- **Resume Support**: Uses rsync with partial transfer support for interrupted transfers
- **Dry-Run Mode**: Preview what would be copied without actually copying

## Requirements

- Python 3.8+
- Raspberry Pi with SSH access
- qBittorrent (or any download client)
- Jellyfin server

## Installation

1. **Clone or download this repository:**
```bash
git clone <repository-url>
cd jellyfin-copy
```

2. **Create a virtual environment and install dependencies:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Copy the example configuration:**
```bash
cp config.yaml.example config.yaml
```

4. **Edit `config.yaml` with your settings:**
```yaml
pi:
  host: "<pi-ip-address>"      # Your Raspberry Pi IP address
  user: "<username>"            # SSH username
  password: "<your-password>"     # SSH password (or use key_path)
  key_path: null             # Or: "~/.ssh/id_rsa"

paths:
  downloads: "/mnt/storage/downloads"
  jellyfin_shows: "/mnt/media/Shows"
  jellyfin_movies: "/mnt/media/Movies"
  local_destination: "./downloads"  # For external copy mode

jellyfin:
  api_key: "your-api-key"    # Optional, for auto-refresh

tmdb:
  api_key: "your-tmdb-api-key"  # Optional, fetches year for better matching
```

## Configuration

### TMDB API Key (Optional)

To automatically fetch release years for better Jellyfin matching:

1. Create a free account at [TMDB](https://www.themoviedb.org/)
2. Go to **Settings → API** and request an API key
3. Add it to `config.yaml`:
   ```yaml
   tmdb:
     api_key: "your-tmdb-api-key"
   ```

This helps Jellyfin correctly identify shows and movies, especially when the folder name doesn't include the year.

### Jellyfin API Key (Optional)

To enable automatic library refresh after copying:

1. Open Jellyfin web interface
2. Go to **Dashboard → Advanced → API Keys**
3. Click **+** to create a new API key
4. Copy the key to `config.yaml` under `jellyfin.api_key`

### SSH Key Authentication (Recommended)

Instead of password authentication, use SSH keys:

```bash
ssh-copy-id <username>@<pi-ip-address>
```

Then in `config.yaml`:
```yaml
pi:
  password: null
  key_path: "~/.ssh/id_rsa"
```

## Usage

### Basic Usage

Activate the virtual environment and run:

```bash
source venv/bin/activate
python main.py
```

### Operation Modes

When you start the script, you'll be prompted to select a mode:

#### Maintenance Mode (Update Pi & Flatpak)
- Updates Raspberry Pi system packages via SSH
- Updates Flatpak applications
- Requires `sudo_password` in `config.yaml`
- Best for: Keeping your Pi system up-to-date remotely

#### Internal Mode (Pi → Pi)
- Copies files from `downloads` to `Shows`/`Movies` folders on the Raspberry Pi
- Automatically refreshes Jellyfin library (if API key configured)
- Best for: Organizing downloads directly on the server

#### External Mode (Pi → Laptop)
- Downloads files from Raspberry Pi to your local machine
- Saves to `./downloads/TV/` and `./downloads/Movies/` by default
- Best for: Watching content offline or backing up

### Maintenance Mode (Update Pi) Prerequisites

Update mode requires **sudo_password** configured in `config.yaml`. Add it to the pi section:

```yaml
pi:
  host: "<pi-ip-address>"
  user: "<username>"
  password: null              # SSH password (if using password auth)
  key_path: "~/.ssh/id_rsa"   # SSH key (if using key auth)
  sudo_password: "your-sudo-password"  # Required for update mode
```

The sudo password is passed securely via stdin using `sudo -S` and is never logged.

## VPN / WireGuard

If your Raspberry Pi is behind a WireGuard VPN, connect the VPN **before** running this script.

### Why separate?

Keeping VPN and script separate is recommended because:
- VPN management requires `sudo` privileges
- WireGuard behaves differently across operating systems
- The script should focus on media management, not network configuration

### Setup

1. **Connect WireGuard first:**
   ```bash
   # Linux (NetworkManager)
   nmcli connection up wireguard-pi
   
   # macOS (with WireGuard app)
   wg-quick up ~/Documents/wireguard/pi.conf
   
   # Windows
   # Use WireGuard GUI to activate tunnel
   ```

2. **Verify connectivity:**
   ```bash
   ping <pi-ip-address>  # Your Pi's VPN IP
   ```

3. **Run the script:**
   ```bash
   python main.py
   ```

### VPN Connection Warning

The script checks connectivity before attempting SSH. If the Pi isn't reachable, you'll see:

```
[red]Cannot reach <pi-ip-address>:22[/red]
Possible causes:
• Raspberry Pi is offline or powered off
• Network connection issue
• WireGuard VPN not connected (if using VPN)
```

### Troubleshooting VPN

**Can't connect even with VPN active:**
- Verify VPN IP matches `pi.host` in `config.yaml`
- Check if Pi's WireGuard is running: `sudo wg show`
- Ensure Pi allows SSH over WireGuard interface

**Slow transfers over VPN:**
- Enable compression: add `-z` to rsync flags (internal mode)
- Use lower bandwidth limit: `bwlimit: 5000` (5 MB/s)
- Consider using local network instead of VPN for large transfers

## Workflow

1. **Connect**: Script connects to your Pi via SSH
2. **Scan**: Discovers TV shows and movies in the downloads folder
3. **Display**: Shows organized content (TV shows grouped by season)
4. **Select**: Use **Space** to select items, **Enter** to confirm
5. **Confirm**: Review selection and confirm operation
6. **Copy**: Watch real-time progress bars during copy
7. **Refresh**: Jellyfin library is refreshed (internal mode only)

### Keyboard Shortcuts

- **Space**: Select/deselect an item
- **Enter**: Confirm selection
- **Ctrl+C**: Cancel operation

## Directory Structure

### On Raspberry Pi

```
/mnt/storage/downloads/          # qBittorrent download location
├── Show Name S01/              # TV Show - Season 1
├── Another Show Season 02/     # TV Show - Season 2
└── Movie Name (2023)/          # Movie

/mnt/media/
├── Shows/                      # TV shows organized by Jellyfin
│   ├── Show Name (2023)/
│   │   └── Season 01/
│   └── Another Show (2022)/
│       └── Season 02/
└── Movies/                     # Movies
    └── Movie Name (2023)/
```

### Local Laptop (External Mode)

```
./downloads/
├── TV/
│   ├── Show Name (2023)/
│   │   └── Season 01/
│   └── Another Show (2022)/
│       └── Season 02/
└── Movies/
    └── Movie Name (2023)/
```

## File Naming

The script detects content type based on file/folder names:

**TV Shows (must contain season info):**
- `Show Name S01`
- `Show Name Season 1`
- `Show S01E01` (episode files)

**Movies (no season info):**
- `Movie Name (2023)`
- `Movie.Name.2023`
- `Movie Name`

## Troubleshooting

### Connection Issues

- Verify SSH access: `ssh <user>@<pi-ip-address>`
- Check Pi is powered on and connected to network
- Verify credentials in `config.yaml`

### Permission Denied

- Ensure user has read access to downloads folder
- For internal mode, ensure write access to Shows/Movies folders
- Use `sudo` groups if needed

### Jellyfin Not Refreshing

- Verify API key is correct
- Check Jellyfin is running: `sudo systemctl status jellyfin`
- Check network connectivity to port 8096

### Slow Transfers

- Add bandwidth limit in config: `bwlimit: 10000` (10 MB/s)
- Use wired connection instead of WiFi
- For external mode, consider compression with rsync flags

## Telegram Bot (Control from Your Phone)

Run the Telegram bot on your Raspberry Pi to manage media operations from anywhere using your phone.

### Setup

1. **Get a bot token from Telegram:**
   - Message [@BotFather](https://t.me/botfather) on Telegram
   - Send `/newbot` and follow instructions
   - Copy the bot token (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Add the token to your config:**
   ```yaml
   telegram:
     token: "YOUR_BOT_TOKEN_HERE"
     allowed_users: []  # Leave empty to allow all, or add your user ID for security
   ```

3. **Get your Telegram user ID (optional, for security):**
   - Message [@userinfobot](https://t.me/userinfobot)
   - Copy your ID number
   - Add it to `allowed_users: [123456789]` in config

4. **Run the bot on your Raspberry Pi:**
   ```bash
   # SSH into your Pi first, then:
   cd ~/jellyfin-copy
   source venv/bin/activate
   python telegram_bot.py
   ```

5. **Start the bot on your phone:**
   - Open Telegram, find your bot
   - Send `/start`
   - Select mode: 📁 Internal, 💻 External, or 🔧 Maintenance

### Using the Bot

The bot provides an interactive menu:

1. **Select Mode** - Choose what operation to perform
2. **Select Content** - Tap items to select/deselect (☑/⬜)
3. **Confirm** - Review disk space requirements
5. **Copy Progress** - Real-time updates during copy:
   - Item X/Y completed
   - Current file being copied
   - Progress bar with percentage
   - Transfer speed (MB/s)
   - ETA estimate
   - For TV series: episode X/Y within the season

**Security Note:** The bot only accepts commands from authorized users. If `allowed_users` is empty, anyone with the bot link can use it. For security, always set your user ID.

### Available Commands

Type `/` in Telegram to see the command menu, or use any of these:

| Command | Description |
|---------|-------------|
| `/start` | Start media copy operation (opens interactive menu) |
| `/dryrun` | Toggle dry-run mode (preview without copying) |
| `/status` | Check disk space on all mounted volumes |
| `/health` | Check disk health with SMART data (temperature, wear %, power-on hours) |
| `/services` | Check if Jellyfin, qBittorrent, Plex, and Samba are running |
| `/downloads` | Show active qBittorrent downloads |
| `/pause` | Pause all downloads |
| `/speed` | Run internet speed test |
| `/search` | Search downloads folder for media |
| `/temp` | Show CPU temperature |
| `/cpu` | Show CPU load and processes |
| `/memory` | Show RAM usage |
| `/backup` | Run system backup manually |
| `/backupstatus` | Show backup status and next due date |
| `/backupsetup` | Google Drive backup setup instructions |
| `/reboot` | Reboot the Pi remotely (requires confirmation) |
| `/help` | Show all available commands and usage tips |
| `/cancel` | Cancel current operation |

**System Monitoring Tips:**
- Use `/health` regularly to monitor drive wear on SSDs
- `/services` is useful when Jellyfin isn't responding — check if it crashed
- `/temp`, `/cpu`, `/memory` help diagnose performance issues
- `/backupstatus` shows when your last system backup was made

### System Backup to Google Drive

The bot can create full SD card backups and upload them to Google Drive using rclone.

#### What it does
- Creates compressed image of your SD card (`dd` + `gzip`)
- Saves locally to configured path (e.g., `/mnt/storage/backups`)
- Uploads to Google Drive via rclone
- Keeps only the latest backup (auto-deletes old ones)
- Runs automatically every 30 days (if enabled)

#### Setup

1. **Enable backups in `config.yaml`:**
   ```yaml
   backup:
     enabled: true
     source_device: "/dev/mmcblk0"  # Your SD card device
     local_path: "/mnt/storage/backups"
     cloud_enabled: true
     cloud_remote: "gdrive:Backups"
     auto_backup: true  # Run automatically every 30 days
   ```

2. **Install rclone:**
   ```bash
   sudo apt update && sudo apt install rclone
   ```

3. **Configure Google Drive:**
   ```bash
   rclone config
   ```
   
   Interactive setup:
   - Type `n` for new remote
   - Name: `gdrive`
   - Select `13` (Google Drive) or type `drive`
   - Client ID: Press Enter (use default)
   - Client Secret: Press Enter (use default)
   - Scope: `1` (Full access)
   - Root folder ID: Press Enter
   - Service account: Press Enter
   - Edit advanced config: `n`
   - Use auto config: `y` (opens browser for OAuth)
   - Follow browser authentication
   - Configure as shared drive: `n`
   - Confirm: `y`

4. **Create backup folder in Google Drive:**
   - Go to [drive.google.com](https://drive.google.com)
   - Create a folder named `Backups`

5. **Test the connection:**
   ```bash
   rclone listremotes
   # Should show: gdrive:
   
   rclone lsd gdrive:
   # Should list your Drive folders
   
   rclone mkdir gdrive:Backups
   # Creates the backup folder
   ```

6. **Run your first backup:**
   In Telegram, send `/backup` to the bot. The first backup takes 15-30 minutes.

7. **Check status anytime:**
   Send `/backupstatus` to see:
   - Last backup date and file size
   - Days until next scheduled backup
   - Cloud sync status

#### Troubleshooting

**rclone not found:**
```bash
sudo apt install rclone
```

**Authentication failed:**
- Re-run `rclone config`
- Make sure you're using the same Google account
- Check that the OAuth flow completed in browser

**Upload timeouts:**
- Large backups (>20GB) may take hours to upload
- The bot will continue in background and notify when done
- Check status with `/backupstatus`

**"gdrive" remote not found:**
- Verify `rclone listremotes` shows `gdrive:`
- Check `config.yaml` has `cloud_remote: "gdrive:Backups"`
- Make sure the `:` is included in the remote name

### Running the Bot as a Service (Auto-start on boot)

Create a systemd service so the bot runs automatically using your virtual environment:

```bash
# Adjust these paths to match your setup:
PROJECT_DIR="/home/pi/jellyfin-copy"  # Where you cloned the repo
USER="pi"                              # Your Pi username

# Create service file
sudo tee /etc/systemd/system/jellyfin-bot.service << EOF
[Unit]
Description=Jellyfin Media Copy Telegram Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable jellyfin-bot
sudo systemctl start jellyfin-bot

# Check status
sudo systemctl status jellyfin-bot

# View logs (if needed)
sudo journalctl -u jellyfin-bot -f
```

**To stop the service:**
```bash
sudo systemctl stop jellyfin-bot
```

**To restart after config changes:**
```bash
sudo systemctl restart jellyfin-bot
```

## Safety Features

- **Dry-Run Mode**: Set `dry_run: true` to preview without copying
- **Resume Support**: Interrupted transfers can be resumed
- **No Move/Delete**: Script only copies, never moves or deletes source files
- **Git Ignore**: `config.yaml` is automatically excluded from git (contains passwords)

## License

MIT License - Feel free to modify and distribute.

## Contributing

Pull requests welcome! Please ensure:
- Code follows existing style
- Add tests for new features
- Update README with changes
