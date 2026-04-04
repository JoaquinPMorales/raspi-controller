"""
Folder scanner module for discovering TV series in the downloads folder.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
import paramiko
import urllib.request
import urllib.parse
import urllib.error


class FolderScanner:
    """Scans folders on remote Raspberry Pi via SSH."""
    
    def __init__(self, config: dict, tmdb_api_key: Optional[str] = None):
        self.config = config
        self.ssh = None
        self.sftp = None
        self.tmdb_api_key = tmdb_api_key
        self._tmdb_cache: Dict[str, Optional[str]] = {}  # Cache for TMDB lookups
        self._tmdb_cache_time: Dict[str, float] = {}  # Cache timestamps
    
    def _get_year_from_tmdb(self, show_name: str, is_movie: bool = False) -> Optional[str]:
        """
        Fetch the year from TMDB API when not available in folder name.
        Uses caching to avoid repeated API calls.
        
        Args:
            show_name: The name of the show/movie
            is_movie: True if movie, False if TV show
            
        Returns:
            4-digit year string or None if not found
        """
        if not self.tmdb_api_key:
            return None
        
        # Check cache (valid for 30 days)
        cache_key = f"{'movie' if is_movie else 'tv'}:{show_name.lower()}"
        if cache_key in self._tmdb_cache:
            cache_time = self._tmdb_cache_time.get(cache_key, 0)
            if time.time() - cache_time < 2592000:  # 30 days
                return self._tmdb_cache[cache_key]
        
        try:
            # Search TMDB
            search_type = 'movie' if is_movie else 'tv'
            encoded_name = urllib.parse.quote(show_name)
            url = f"https://api.themoviedb.org/3/search/{search_type}?api_key={self.tmdb_api_key}&query={encoded_name}&language=en-US&page=1"
            
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                
                if data.get('results') and len(data['results']) > 0:
                    # Get the first (most popular) result
                    result = data['results'][0]
                    
                    if is_movie:
                        # For movies, use release_date
                        release_date = result.get('release_date', '')
                        if release_date and len(release_date) >= 4:
                            year = release_date[:4]
                            self._tmdb_cache[cache_key] = year
                            self._tmdb_cache_time[cache_key] = time.time()
                            return year
                    else:
                        # For TV shows, use first_air_date
                        first_air_date = result.get('first_air_date', '')
                        if first_air_date and len(first_air_date) >= 4:
                            year = first_air_date[:4]
                            self._tmdb_cache[cache_key] = year
                            self._tmdb_cache_time[cache_key] = time.time()
                            return year
            
            # No results found
            self._tmdb_cache[cache_key] = None
            self._tmdb_cache_time[cache_key] = time.time()
            return None
            
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print(f"TMDB API key invalid or expired")
            elif e.code == 429:
                print(f"TMDB API rate limit exceeded")
            self._tmdb_cache[cache_key] = None
            return None
        except Exception as e:
            # Fail silently and return None
            print(f"TMDB lookup failed for '{show_name}': {e}")
            self._tmdb_cache[cache_key] = None
            return None
    
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
    
    def get_disk_space(self, path: str) -> dict:
        """Get disk space information for a path (returns bytes)."""
        try:
            # Use df command to get disk space
            stdin, stdout, stderr = self.ssh.exec_command(f'df -B1 "{path}" 2>/dev/null | tail -1')
            output = stdout.read().decode().strip()
            
            if output:
                parts = output.split()
                if len(parts) >= 4:
                    return {
                        'total': int(parts[1]),
                        'used': int(parts[2]),
                        'free': int(parts[3]),
                        'available': int(parts[3])
                    }
            
            # Fallback to statvfs via Python on remote
            stdin, stdout, stderr = self.ssh.exec_command(
                f'python3 -c "import os; s=os.statvfs(\"{path}\"); print(s.f_frsize*s.f_blocks, s.f_frsize*s.f_bfree, s.f_frsize*s.f_bavail)" 2>/dev/null'
            )
            output = stdout.read().decode().strip()
            if output:
                parts = output.split()
                if len(parts) >= 3:
                    return {
                        'total': int(parts[0]),
                        'used': int(parts[0]) - int(parts[1]),
                        'free': int(parts[1]),
                        'available': int(parts[2])
                    }
        except Exception:
            pass
        
        return {'total': 0, 'used': 0, 'free': 0, 'available': 0}
    
    def get_item_size(self, path: str) -> int:
        """Get total size of a file or directory in bytes."""
        try:
            # Use du command for accurate directory size
            stdin, stdout, stderr = self.ssh.exec_command(f'du -sb "{path}" 2>/dev/null | cut -f1')
            output = stdout.read().decode().strip()
            if output and output.isdigit():
                return int(output)
        except Exception:
            pass
        return 0
    
    def calculate_items_size(self, items: list) -> int:
        """Calculate total size of multiple items."""
        total_size = 0
        for item in items:
            item_list = item.get('items', [item])
            for sub_item in item_list:
                total_size += self.get_item_size(sub_item['path'])
        return total_size
    
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
            
            # If folder name gave no TV pattern, peek inside to check filenames
            # e.g. "Rugrats - Aventuras en pañales" contains S01E01 files
            if is_dir and show_info['content_type'] == 'movie':
                show_info = self._reclassify_if_tv(full_path, show_info)
            
            # If year is missing, try to fetch from TMDB
            year = show_info.get('year')
            if not year and self.tmdb_api_key:
                is_movie = show_info['content_type'] == 'movie'
                year = self._get_year_from_tmdb(show_info['show'], is_movie=is_movie)
                if year:
                    print(f"Fetched year from TMDB: {show_info['show']} ({year})")
            
            
            item = {
                'name': entry.filename,
                'path': full_path,
                'show': show_info['show'],
                'year': year,
                'season': show_info['season'],
                'content_type': show_info['content_type'],
                'type': 'folder' if is_dir else 'file',
            }
            
            items.append(item)
        
        return items
    
    def _reclassify_if_tv(self, folder_path: str, show_info: Dict) -> Dict:
        """
        Peek inside a folder to check if it contains TV episode files.
        If S##E## or Season ## patterns are found in filenames, reclassify as TV.
        Returns updated show_info dict.
        """
        episode_pattern = re.compile(r'[Ss]\d{1,2}[Ee]\d{1,2}|[Ss]eason\s*\d{1,2}', re.IGNORECASE)
        try:
            entries = self.sftp.listdir(folder_path)
            for filename in entries[:20]:  # Check first 20 entries max
                if episode_pattern.search(filename):
                    # Extract season from the first matching file
                    season_match = re.search(r'[Ss](\d{1,2})[Ee]\d{1,2}', filename)
                    season = season_match.group(1).zfill(2) if season_match else None
                    return {
                        'show': show_info['show'],
                        'year': show_info['year'],
                        'season': season,
                        'content_type': 'tv',
                    }
        except Exception:
            pass
        return show_info

    def _parse_show_info(self, name: str) -> Dict[str, Optional[str]]:
        """
        Parse show/movie name, year, and season number from folder/file name.
        Detects whether it's a TV show or movie based on naming patterns.
        
        Supports patterns like:
        - Show Name (2008) S01, Show Name Season 1 (TV)
        - Show S01E01 (TV episode file)
        - Movie Name (2023) (Movie)
        - Movie.Name.2023 (Movie)
        - etc.
        
        Returns dict with:
        - show: parsed name (without year)
        - year: 4-digit year string or None
        - season: season number string or None
        - content_type: 'tv' or 'movie'
        """
        # Remove file extension if present
        name = Path(name).stem
        
        # Replace dots and underscores with spaces for easier parsing
        clean_name = name.replace('.', ' ').replace('_', ' ')
        
        # Check for TV show patterns first
        season = None
        year = None
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
        
        # Extract year from the show/movie name
        # Matches (2023), [2023], or bare 4-digit year between 1900–2099
        year_match = re.search(r'[\(\[]?(19|20)\d{2}[\)\]]?', show)
        if year_match:
            year = re.sub(r'[\(\[\]\)]', '', year_match.group(0))
            # Remove year from the display name
            show = show[:year_match.start()].strip()
            show = re.sub(r'[\s\-\.]+$', '', show)
        
        return {
            'show': show,
            'year': year,
            'season': season,
            'content_type': content_type
        }
