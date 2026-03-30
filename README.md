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
  host: "192.168.1.174"      # Your Raspberry Pi IP address
  user: "joaquin"            # SSH username
  password: "yourpassword"     # SSH password (or use key_path)
  key_path: null             # Or: "~/.ssh/id_rsa"

paths:
  downloads: "/mnt/storage/downloads"
  jellyfin_shows: "/mnt/media/Shows"
  jellyfin_movies: "/mnt/media/Movies"
  local_destination: "./downloads"  # For external copy mode

jellyfin:
  api_key: "your-api-key"    # Optional, for auto-refresh
```

## Configuration

### Jellyfin API Key (Optional)

To enable automatic library refresh after copying:

1. Open Jellyfin web interface
2. Go to **Dashboard → Advanced → API Keys**
3. Click **+** to create a new API key
4. Copy the key to `config.yaml` under `jellyfin.api_key`

### SSH Key Authentication (Recommended)

Instead of password authentication, use SSH keys:

```bash
ssh-copy-id joaquin@192.168.1.174
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

#### Internal Mode (Pi → Pi)
- Copies files from `downloads` to `Shows`/`Movies` folders on the Raspberry Pi
- Automatically refreshes Jellyfin library (if API key configured)
- Best for: Organizing downloads directly on the server

#### External Mode (Pi → Laptop)
- Downloads files from Raspberry Pi to your local machine
- Saves to `./downloads/TV/` and `./downloads/Movies/` by default
- Best for: Watching content offline or backing up

### Workflow

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
│   ├── Show Name/
│   │   └── Season 01/
│   └── Another Show/
│       └── Season 02/
└── Movies/                     # Movies
    └── Movie Name (2023)/
```

### Local Laptop (External Mode)

```
./downloads/
├── TV/
│   ├── Show Name/
│   │   └── Season 01/
│   └── Another Show/
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

- Verify SSH access: `ssh joaquin@192.168.1.174`
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
