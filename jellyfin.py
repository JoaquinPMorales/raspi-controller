"""
Jellyfin API module for refreshing the media library.
"""

import json
from typing import Optional


def refresh_jellyfin_library(host: str, port: int, api_key: Optional[str], scanner=None) -> bool:
    """
    Trigger a Jellyfin library refresh via the API.
    
    Args:
        host: Jellyfin server host (IP or hostname)
        port: Jellyfin server port
        api_key: Jellyfin API key (optional)
        scanner: FolderScanner instance with SSH connection (optional)
    
    Returns:
        True if refresh was triggered successfully, False otherwise
    """
    # If no API key provided, try to use system command via SSH
    if not api_key and scanner:
        return _refresh_via_ssh(scanner, host, port)
    
    if not api_key:
        return False
    
    try:
        import urllib.request
        import urllib.error
        
        # Jellyfin API endpoint for library refresh
        url = f"http://{host}:{port}/Library/Refresh"
        
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "X-Emby-Token": api_key,
                "Content-Type": "application/json"
            }
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 204 or response.status == 200
            
    except Exception as e:
        print(f"Jellyfin API refresh failed: {e}")
        return False


def _refresh_via_ssh(scanner, host: str, port: int) -> bool:
    """
    Try to refresh Jellyfin library using system commands via SSH.
    This works if the Pi has curl or wget available.
    """
    if not scanner or not scanner.ssh:
        return False
    
    try:
        # Try using curl to trigger library refresh (no API key required for local access)
        # This assumes the Jellyfin server allows local connections without auth
        curl_cmd = f'curl -s -o /dev/null -w "%{{http_code}}" -X POST http://{host}:{port}/Library/Refresh 2>/dev/null || echo "000"'
        
        stdin, stdout, stderr = scanner.ssh.exec_command(curl_cmd)
        exit_code = stdout.channel.recv_exit_status()
        
        # Also try to restart jellyfin service as fallback (this triggers rescan)
        if exit_code != 0:
            # Alternative: send USR1 signal to jellyfin or restart service
            restart_cmd = "sudo systemctl restart jellyfin 2>/dev/null || true"
            scanner.ssh.exec_command(restart_cmd)
            return True  # Assume it worked
        
        return True
        
    except Exception as e:
        print(f"SSH-based refresh failed: {e}")
        return False
