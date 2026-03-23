"""
Rsync copier module for copying TV series and movies to Jellyfin with progress monitoring.
"""

import os
import re
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass

import paramiko
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn


@dataclass
class CopyStatus:
    """Status of a copy operation."""
    source: str
    destination: str
    current_file: str = ""
    bytes_transferred: int = 0
    total_bytes: int = 0
    speed: str = ""
    percent: float = 0.0
    completed: bool = False
    error: Optional[str] = None


class RsyncCopier:
    """Handles rsync copy operations over SSH with progress monitoring."""
    
    def __init__(self, pi_config: dict, paths_config: dict, options_config: dict):
        self.pi_config = pi_config
        self.paths_config = paths_config
        self.options_config = options_config
        self.cancelled = False
    
    def cancel(self):
        """Signal cancellation of ongoing operations."""
        self.cancelled = True
    
    def _build_rsync_command(self, source: str, destination: str) -> str:
        """Build rsync command with appropriate options."""
        dry_run = self.options_config.get('dry_run', False)
        bwlimit = self.options_config.get('bwlimit')
        preserve = self.options_config.get('preserve_permissions', True)
        
        flags = ['-avh', '--progress', '--stats']
        
        if dry_run:
            flags.append('--dry-run')
        
        if preserve:
            flags.append('-p')
        
        if bwlimit:
            flags.append(f'--bwlimit={bwlimit}')
        
        # Use rsync's partial transfer support for resuming
        flags.append('--partial')
        
        cmd = ['rsync'] + flags + [source, destination]
        return ' '.join(cmd)
    
    def _parse_rsync_progress(self, line: str) -> Optional[Dict]:
        """
        Parse rsync progress output.
        
        Returns dict with progress info or None if not a progress line.
        """
        # Pattern for file progress: "    123.45K  45%   12.34MB/s    0:00:05"
        progress_pattern = r'^\s*(\S+)\s+(\d+)%\s+(\S+)\s+(\S+)'
        match = re.match(progress_pattern, line)
        
        if match:
            return {
                'bytes': match.group(1),
                'percent': int(match.group(2)),
                'speed': match.group(3),
                'eta': match.group(4)
            }
        
        return None
    
    def _get_destination_path(self, item: Dict) -> str:
        """Determine the correct destination path based on content type."""
        content_type = item.get('content_type', 'movie')
        show_name = item['show']
        season = item.get('season')
        
        if content_type == 'tv':
            dest_base = self.paths_config.get('jellyfin_shows') or self.paths_config.get('jellyfin_tv')
            if season:
                return f"{dest_base}/{show_name}/Season {season}"
            else:
                return f"{dest_base}/{show_name}"
        else:
            dest_base = self.paths_config.get('jellyfin_movies')
            return f"{dest_base}/{show_name}"
    
    def _copy_single_item(self, item: Dict, console: Console, progress: Progress, task_id: int) -> bool:
        """
        Copy a single item (TV season or movie) to Jellyfin with progress monitoring.
        
        Returns True on success, False on failure.
        """
        source_path = item['path']
        show_name = item['show']
        season = item.get('season')
        content_type = item.get('content_type', 'movie')
        
        # Build destination path based on content type
        dest_path = self._get_destination_path(item)
        
        # Build display name for progress
        if content_type == 'tv' and season:
            display_name = f"{show_name} S{season}"
        else:
            display_name = show_name
        
        # Ensure destination directory exists
        mkdir_cmd = f"mkdir -p '{dest_path}'"
        
        try:
            # Connect to execute rsync
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                'hostname': self.pi_config['host'],
                'port': self.pi_config.get('port', 22),
                'username': self.pi_config['user'],
                'timeout': 10,
            }
            
            key_path = self.pi_config.get('key_path')
            if key_path:
                key_path = os.path.expanduser(key_path)
                if os.path.exists(key_path):
                    connect_kwargs['key_filename'] = key_path
            
            password = self.pi_config.get('password')
            if password:
                connect_kwargs['password'] = password
            
            ssh.connect(**connect_kwargs)
            
            # Create destination directory
            ssh.exec_command(mkdir_cmd)
            
            # Build and execute rsync command
            rsync_cmd = self._build_rsync_command(
                f"'{source_path}/'",
                f"'{dest_path}/'"
            )
            
            console.print(f"[dim]Executing: {rsync_cmd}[/dim]")
            
            stdin, stdout, stderr = ssh.exec_command(rsync_cmd, get_pty=True)
            
            current_file = ""
            
            # Read output line by line for progress updates
            while not self.cancelled:
                line = stdout.readline()
                if not line:
                    break
                
                line = line.strip()
                
                # Check for file name lines (they don't start with spaces)
                if line and not line[0].isspace() and not line.startswith('rsync'):
                    # This might be a filename being transferred
                    if not line.startswith('sending') and not line.startswith('receiving'):
                        current_file = Path(line).name
                        progress.update(task_id, description=f"[cyan]{display_name}[/cyan] - {current_file[:40]}")
                
                # Parse progress information
                progress_info = self._parse_rsync_progress(line)
                if progress_info:
                    percent = progress_info['percent']
                    speed = progress_info['speed']
                    progress.update(task_id, completed=percent)
            
            # Check exit status
            exit_status = stdout.channel.recv_exit_status()
            
            ssh.close()
            
            if self.cancelled:
                return False
            
            if exit_status == 0:
                progress.update(task_id, completed=100)
                return True
            else:
                error = stderr.read().decode('utf-8', errors='ignore').strip()
                console.print(f"[red]rsync failed with exit code {exit_status}: {error}[/red]")
                return False
                
        except Exception as e:
            console.print(f"[red]Error copying {display_name}: {e}[/red]")
            return False
    
    def copy_items(self, items: List[Dict], console: Console) -> bool:
        """
        Copy all selected items to Jellyfin with progress display.
        
        Returns True if all items copied successfully, False otherwise.
        """
        if not items:
            console.print("[yellow]No items to copy.[/yellow]")
            return True
        
        dry_run = self.options_config.get('dry_run', False)
        mode_str = "DRY RUN" if dry_run else "COPY"
        
        console.print(f"\n[bold blue]Starting {mode_str} operations...[/bold blue]")
        console.print(f"[dim]Total items: {len(items)}[/dim]\n")
        
        all_success = True
        
        # Use Rich's Progress for beautiful output
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            "•",
            TimeElapsedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            
            # Add overall progress task
            overall_task = progress.add_task(
                f"[bold green]Overall Progress",
                total=len(items)
            )
            
            for i, item in enumerate(items, 1):
                if self.cancelled:
                    console.print("\n[yellow]Operation cancelled by user.[/yellow]")
                    return False
                
                show_name = item['show']
                season = item.get('season')
                content_type = item.get('content_type', 'movie')
                
                # Build display name for progress
                if content_type == 'tv' and season:
                    display_name = f"{show_name} S{season}"
                else:
                    display_name = show_name
                
                # Add task for this item
                task_id = progress.add_task(
                    f"[cyan]{display_name}[/cyan]",
                    total=100,
                    visible=True
                )
                
                console.print(f"\n[bold]{i}/{len(items)}: {display_name}[/bold]")
                
                success = self._copy_single_item(item, console, progress, task_id)
                
                if success:
                    progress.update(overall_task, advance=1)
                    if dry_run:
                        console.print(f"[yellow]  [DRY RUN] Would copy {display_name}[/yellow]")
                    else:
                        console.print(f"[green]  ✓ Completed {display_name}[/green]")
                else:
                    console.print(f"[red]  ✗ Failed {display_name}[/red]")
                    all_success = False
                
                # Remove the individual task
                progress.remove_task(task_id)
        
        return all_success
