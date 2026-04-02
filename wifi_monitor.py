#!/usr/bin/env python3
"""
WiFi Monitor Script
Checks internet connectivity and reconnects to WiFi if needed.
Supports connecting to primary or fallback WiFi networks.
"""

import os
import sys
import time
import subprocess
import logging
from typing import Optional
import yaml

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('wifi_monitor')


def load_config(config_path: str = "/etc/wifi_monitor/config.yaml") -> dict:
    """Load WiFi monitor configuration."""
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        return {}
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def check_internet_connectivity(host: str = "8.8.8.8", timeout: int = 5) -> bool:
    """Check if internet is reachable by pinging a reliable host."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error checking connectivity: {e}")
        return False


def get_current_wifi_connection() -> Optional[str]:
    """Get the current active WiFi connection name."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            capture_output=True,
            text=True
        )
        
        for line in result.stdout.strip().split('\n'):
            if ':802-11-wireless' in line:
                return line.split(':')[0]
        return None
    except Exception as e:
        logger.error(f"Error getting current WiFi: {e}")
        return None


def restart_network_manager() -> bool:
    """Restart NetworkManager service."""
    try:
        logger.info("Restarting NetworkManager...")
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "NetworkManager"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("NetworkManager restarted successfully")
            time.sleep(3)  # Give it time to settle
            return True
        else:
            logger.error(f"Failed to restart NetworkManager: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error restarting NetworkManager: {e}")
        return False


def connect_to_wifi(ssid: str, password: Optional[str] = None) -> bool:
    """Connect to a specific WiFi network."""
    try:
        logger.info(f"Attempting to connect to WiFi: {ssid}")
        
        if password:
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid, "password", password],
                capture_output=True,
                text=True
            )
        else:
            # Try connecting with saved connection profile
            result = subprocess.run(
                ["nmcli", "connection", "up", ssid],
                capture_output=True,
                text=True
            )
        
        if result.returncode == 0:
            logger.info(f"Successfully connected to {ssid}")
            return True
        else:
            logger.error(f"Failed to connect to {ssid}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error connecting to WiFi: {e}")
        return False


def scan_wifi_networks() -> list:
    """Scan for available WiFi networks."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"],
            capture_output=True,
            text=True
        )
        
        networks = []
        for line in result.stdout.strip().split('\n'):
            if ':' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    ssid = parts[0]
                    signal = parts[1]
                    if ssid:  # Skip empty SSIDs
                        networks.append({'ssid': ssid, 'signal': signal})
        return networks
    except Exception as e:
        logger.error(f"Error scanning WiFi: {e}")
        return []


def main():
    """Main WiFi monitoring logic."""
    config = load_config()
    
    if not config:
        logger.error("No configuration found, using defaults")
        # Try default check
        if not check_internet_connectivity():
            logger.warning("No internet connectivity detected")
            restart_network_manager()
        return
    
    wifi_config = config.get('wifi', {})
    
    # Check if enabled
    if not wifi_config.get('enabled', True):
        logger.info("WiFi monitor is disabled in config")
        return
    
    # Get configuration
    check_host = wifi_config.get('check_host', '8.8.8.8')
    check_timeout = wifi_config.get('check_timeout', 5)
    primary_ssid = wifi_config.get('primary_ssid')
    primary_password = wifi_config.get('primary_password')
    fallback_ssid = wifi_config.get('fallback_ssid')
    fallback_password = wifi_config.get('fallback_password')
    restart_on_failure = wifi_config.get('restart_manager', True)
    
    logger.info("Starting WiFi connectivity check...")
    
    # Check current connectivity
    if check_internet_connectivity(check_host, check_timeout):
        current_wifi = get_current_wifi_connection()
        logger.info(f"Internet is reachable via WiFi: {current_wifi}")
        return
    
    logger.warning("Internet connectivity lost!")
    
    # Try to restart NetworkManager first
    if restart_on_failure:
        restart_network_manager()
        
        # Check if we're back online after restart
        if check_internet_connectivity(check_host, check_timeout):
            logger.info("Connectivity restored after restarting NetworkManager")
            return
    
    # If we have a primary SSID configured, try to connect to it
    if primary_ssid:
        logger.info(f"Attempting to connect to primary WiFi: {primary_ssid}")
        if connect_to_wifi(primary_ssid, primary_password):
            time.sleep(3)
            if check_internet_connectivity(check_host, check_timeout):
                logger.info("Connected to primary WiFi and internet is reachable")
                return
    
    # If primary fails and we have a fallback, try it
    if fallback_ssid:
        logger.info(f"Attempting to connect to fallback WiFi: {fallback_ssid}")
        if connect_to_wifi(fallback_ssid, fallback_password):
            time.sleep(3)
            if check_internet_connectivity(check_host, check_timeout):
                logger.info("Connected to fallback WiFi and internet is reachable")
                return
    
    # If both fail, scan for available networks and log them
    available_networks = scan_wifi_networks()
    if available_networks:
        logger.info("Available WiFi networks:")
        for net in available_networks[:5]:  # Show top 5 by signal strength
            logger.info(f"  - {net['ssid']} (signal: {net['signal']})")
    
    logger.error("Failed to restore WiFi connectivity after all attempts")
    sys.exit(1)


if __name__ == "__main__":
    main()
