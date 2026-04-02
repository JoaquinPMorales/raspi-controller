#!/bin/bash
# WiFi Monitor Installation Script for Raspberry Pi / Ubuntu
# This script installs and configures the WiFi monitoring service

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/wifi_monitor"
CONFIG_DIR="/etc/wifi_monitor"
SERVICE_DIR="/etc/systemd/system"

echo "=== WiFi Monitor Installation ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root or with sudo"
    exit 1
fi

# Check if NetworkManager is installed
if ! command -v nmcli &> /dev/null; then
    echo "ERROR: NetworkManager (nmcli) is not installed."
    echo "Install it with: sudo apt install network-manager"
    exit 1
fi

echo "[1/5] Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"

echo "[2/5] Installing Python script..."
cp "$SCRIPT_DIR/wifi_monitor.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/wifi_monitor.py"

echo "[3/5] Installing systemd service and timer..."
cp "$SCRIPT_DIR/wifi-monitor.service" "$SERVICE_DIR/"
cp "$SCRIPT_DIR/wifi-monitor.timer" "$SERVICE_DIR/"

# Reload systemd
echo "[4/5] Reloading systemd..."
systemctl daemon-reload

echo "[5/5] Configuration..."
if [ -f "$SCRIPT_DIR/config.yaml" ]; then
    # Copy WiFi config from project config
    python3 -c "
import yaml
with open('$SCRIPT_DIR/config.yaml', 'r') as f:
    config = yaml.safe_load(f)
wifi_config = config.get('wifi', {})
with open('$CONFIG_DIR/config.yaml', 'w') as f:
    yaml.dump({'wifi': wifi_config}, f, default_flow_style=False)
print('WiFi configuration extracted from config.yaml')
"
else
    # Create default config
    cat > "$CONFIG_DIR/config.yaml" << 'EOF'
wifi:
  enabled: true
  check_host: "8.8.8.8"
  check_timeout: 5
  restart_manager: true
  primary_ssid: null
  primary_password: null
  fallback_ssid: null
  fallback_password: null
EOF
    echo "Default configuration created at $CONFIG_DIR/config.yaml"
    echo "Please edit it to add your WiFi credentials."
fi

# Set permissions
chmod 600 "$CONFIG_DIR/config.yaml"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Configuration file: $CONFIG_DIR/config.yaml"
echo ""
echo "Commands:"
echo "  Start service now:   sudo systemctl start wifi-monitor.service"
echo "  Enable timer:        sudo systemctl enable wifi-monitor.timer"
echo "  Start timer:         sudo systemctl start wifi-monitor.timer"
echo "  Check status:        sudo systemctl status wifi-monitor.timer"
echo "  View logs:           sudo journalctl -u wifi-monitor -f"
echo ""
echo "The service checks WiFi connectivity every 30 seconds."
