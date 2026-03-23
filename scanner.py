"""
Folder scanner module for discovering TV series in the downloads folder.
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Optional
import paramiko


class FolderScanner:
    """Scans folders on remote Raspberry Pi via SSH."""
    
    def __init__(self, config: dict):
        self.config = config
        self.ssh = None
        self.sftp = None
    
    def connect(self) -> bool:
        """Establish SSH connection to the Raspberry Pi."""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                'hostname': self.config['host'],
                'port': self.config.get('port', 22),
                'username': self.config['user'],
                'timeout': 10,
            }
            
            # Use key-based auth if key_path is provided
            key_path = self.config.get('key_path')
            if key_path:
                key_path = os.path.expanduser(key_path)
                if os.path.exists(key_path):
                    connect_kwargs['key_filename'] = key_path
                else:
                    print(f"Warning: SSH key not found at {key_path}")
            
            # Use password if provided and key auth failed or not configured
            password = self.config.get('password')
            if password:
                connect_kwargs['password'] = password
            
            self.ssh.connect(**connect_kwargs)
            self.sftp = self.ssh.open_sftp()
            return True
            
        except Exception as e:
            print(f"SSH connection failed: {e}")
            return False
    
    def close(self):
        """Close SSH and SFTP connections."""
        if self.sftp:
            self.sftp.close()
        if self.ssh:
            self.ssh.close()
    
    def scan_folder(self, path: str) -> List[Dict]:
        """
        Scan a folder and return list of items with metadata.
        
        Returns list of dicts with:
        - name: folder/file name
        - path: full path
        - show: parsed show name (for TV shows) or movie name
        - season: parsed season number (for TV shows)
        - content_type: 'tv' or 'movie'
        - type: 'folder' or 'file'
        """
        items = []
        
        try:
            entries = self.sftp.listdir_attr(path)
        except IOError as e:
            print(f"Error reading directory {path}: {e}")
            return items
        
        for entry in entries:
            full_path = f"{path}/{entry.filename}"
            is_dir = entry.st_mode & 0o40000 == 0o40000  # Check if directory
            
            # Parse show name and season, detect content type
            show_info = self._parse_show_info(entry.filename)
            
            item = {
                'name': entry.filename,
                'path': full_path,
                'show': show_info['show'],
                'season': show_info['season'],
                'content_type': show_info['content_type'],
                'type': 'folder' if is_dir else 'file',
            }
            
            items.append(item)
        
        return items
    
    def _parse_show_info(self, name: str) -> Dict[str, Optional[str]]:
        """
        Parse show/movie name and season number from folder/file name.
        Detects whether it's a TV show or movie based on naming patterns.
        
        Supports patterns like:
        - Show Name S01, Show Name Season 1 (TV)
        - Show S01E01 (TV episode file)
        - Movie Name (2023) (Movie)
        - Movie.Name.2023 (Movie)
        - etc.
        
        Returns dict with:
        - show: parsed name
        - season: season number or None
        - content_type: 'tv' or 'movie'
        """
        # Remove file extension if present
        name = Path(name).stem
        
        # Replace dots and underscores with spaces for easier parsing
        clean_name = name.replace('.', ' ').replace('_', ' ')
        
        # Check for TV show patterns first
        season = None
        show = name
        content_type = 'movie'  # Default to movie
        
        # TV Show patterns: S01, S1, Season 01, Season 1, S01E01
        tv_patterns = [
            (r'[Ss](\d{1,2})\s*$', 'season'),                           # Show S01
            (r'[Ss](\d{1,2})[Ee]\d{1,2}', 'episode'),                   # Show S01E01
            (r'[Ss]eason\s*(\d{1,2})', 'season'),                       # Show Season 01
            (r'[Ss]eason\s*(\d{1,2})\s*$', 'season'),                   # Show Season 01 (at end)
        ]
        
        for pattern, pattern_type in tv_patterns:
            match = re.search(pattern, clean_name, re.IGNORECASE)
            if match:
                season = match.group(1).zfill(2)  # Zero-pad to 2 digits
                content_type = 'tv'
                # Remove season info from show name
                show = clean_name[:match.start()].strip()
                # Clean up trailing separators
                show = re.sub(r'[\s\-\.]+$', '', show)
                break
        
        return {
            'show': show,
            'season': season,
            'content_type': content_type
        }
