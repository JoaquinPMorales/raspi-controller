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
        year = item.get('year')
        season = item.get('season')
        
        if content_type == 'tv':
            dest_base = self.paths_config.get('jellyfin_shows') or self.paths_config.get('jellyfin_tv')
            # Jellyfin requires: Show Name (Year)/Season 01
            folder_name = f"{show_name} ({year})" if year else show_name
            if season:
                return f"{dest_base}/{folder_name}/Season {season}"
            else:
                return f"{dest_base}/{folder_name}"
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
            
            # Create destination directory and WAIT for it to complete
            _, mkdir_stdout, mkdir_stderr = ssh.exec_command(mkdir_cmd)
            mkdir_exit = mkdir_stdout.channel.recv_exit_status()
            if mkdir_exit != 0:
                err = mkdir_stderr.read().decode('utf-8', errors='ignore').strip()
                console.print(f"[red]Failed to create destination directory '{dest_path}': {err}[/red]")
                ssh.close()
                return False
            
            # Build and execute rsync command
            # Note: no get_pty so stderr is kept separate for error capture
            rsync_cmd = self._build_rsync_command(
                f"'{source_path}/'",
                f"'{dest_path}/'"
            )
            
            console.print(f"[dim]Executing: {rsync_cmd}[/dim]")
            
            stdin, stdout, stderr = ssh.exec_command(rsync_cmd)
            
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
                    progress.update(task_id, completed=percent)
            
            # Check exit status
            exit_status = stdout.channel.recv_exit_status()
            error_output = stderr.read().decode('utf-8', errors='ignore').strip()
            
            ssh.close()
            
            if self.cancelled:
                return False
            
            if exit_status == 0:
                progress.update(task_id, completed=100)
                return True
            else:
                # Include stderr in error message for diagnosability
                error_detail = error_output if error_output else "(no stderr output)"
                console.print(f"[red]rsync failed (exit {exit_status}): {error_detail}[/red]")
                return False
                
        except Exception as e:
            console.print(f"[red]Error copying {display_name}: {e}[/red]")
            return False
    
    def copy_items(self, items: List[Dict], console) -> bool:
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
        
        # Always use a real Rich Console for Progress internals.
        # TelegramConsole is not a Rich Console so must not be passed to Progress.
        from rich.console import Console as RichConsole
        _progress_console = console if isinstance(console, RichConsole) else RichConsole(file=open(os.devnull, 'w'))
        
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
            console=_progress_console,
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


class ExternalCopier:
    """Handles copying files from Pi to local laptop via SFTP."""
    
    def __init__(self, pi_config: dict, paths_config: dict, options_config: dict):
        self.pi_config = pi_config
        self.paths_config = paths_config
        self.options_config = options_config
        self.cancelled = False
        self.ssh = None
        self.sftp = None
    
    def cancel(self):
        """Signal cancellation of ongoing operations."""
        self.cancelled = True
    
    def _connect(self) -> bool:
        """Establish SSH connection."""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
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
            
            self.ssh.connect(**connect_kwargs)
            self.sftp = self.ssh.open_sftp()
            return True
            
        except Exception as e:
            print(f"SSH connection failed: {e}")
            return False
    
    def _get_local_destination(self, item: Dict) -> str:
        """Determine the local destination path."""
        content_type = item.get('content_type', 'movie')
        show_name = item['show']
        season = item.get('season')
        dest_base = self.paths_config.get('local_destination', './downloads')
        
        if content_type == 'tv':
            if season:
                return os.path.join(dest_base, 'TV', show_name, f"Season {season}")
            else:
                return os.path.join(dest_base, 'TV', show_name)
        else:
            return os.path.join(dest_base, 'Movies', show_name)
    
    def _copy_file_with_progress(self, remote_path: str, local_path: str, console: Console, progress: Progress, task_id: int, display_name: str, filename: str) -> bool:
        """Copy a single file with progress updates using chunked reads."""
        try:
            # Get remote file size
            remote_stat = self.sftp.stat(remote_path)
            total_size = remote_stat.st_size
            
            if total_size == 0:
                # Empty file, just create it
                open(local_path, 'w').close()
                progress.update(task_id, completed=100)
                return True
            
            # Open remote and local files
            with self.sftp.file(remote_path, 'rb') as remote_file:
                # Create temp file first, rename on success
                temp_path = local_path + '.part'
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                copied_size = 0
                chunk_size = 256 * 1024  # 256 KB chunks for good throughput
                
                with open(temp_path, 'wb') as local_file:
                    while not self.cancelled:
                        data = remote_file.read(chunk_size)
                        if not data:
                            break
                        
                        local_file.write(data)
                        copied_size += len(data)
                        
                        # Update progress
                        percent = min(int((copied_size / total_size) * 100), 99)
                        progress.update(
                            task_id, 
                            completed=percent,
                            description=f"[cyan]{display_name}[/cyan] - {filename[:30]} ({percent}%)"
                        )
                
                if self.cancelled:
                    # Clean up partial file
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    return False
                
                # Rename temp file to final name
                os.replace(temp_path, local_path)
                progress.update(task_id, completed=100)
                return True
                
        except Exception as e:
            console.print(f"[red]Error copying file {filename}: {e}[/red]")
            # Clean up partial file on error
            try:
                temp_path = local_path + '.part'
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            return False
    
    def _copy_directory(self, remote_path: str, local_path: str, console: Console, progress: Progress, task_id: int, display_name: str) -> bool:
        """Recursively copy a directory from remote to local."""
        try:
            # Create local directory
            os.makedirs(local_path, exist_ok=True)
            
            # List remote directory
            entries = self.sftp.listdir_attr(remote_path)
            
            # Calculate total size for progress
            total_size = 0
            file_list = []
            
            def scan_directory(rpath, lpath, entries_list):
                nonlocal total_size
                for entry in entries_list:
                    rfile = f"{rpath}/{entry.filename}"
                    lfile = os.path.join(lpath, entry.filename)
                    
                    if entry.st_mode & 0o40000:  # Directory
                        try:
                            sub_entries = self.sftp.listdir_attr(rfile)
                            os.makedirs(lfile, exist_ok=True)
                            scan_directory(rfile, lfile, sub_entries)
                        except:
                            pass
                    else:
                        file_list.append((rfile, lfile, entry.filename, entry.st_size))
                        total_size += entry.st_size
            
            scan_directory(remote_path, local_path, entries)
            
            if total_size == 0:
                progress.update(task_id, completed=100)
                return True
            
            # Copy files with byte-based progress
            copied_total = 0
            for rfile, lfile, filename, fsize in file_list:
                if self.cancelled:
                    return False
                
                # Copy single file
                try:
                    remote_stat = self.sftp.stat(rfile)
                    temp_path = lfile + '.part'
                    
                    with self.sftp.file(rfile, 'rb') as remote_f:
                        copied_file = 0
                        chunk_size = 256 * 1024
                        
                        with open(temp_path, 'wb') as local_f:
                            while not self.cancelled:
                                data = remote_f.read(chunk_size)
                                if not data:
                                    break
                                local_f.write(data)
                                copied_file += len(data)
                                copied_total += len(data)
                                
                                # Overall progress
                                percent = min(int((copied_total / total_size) * 100), 99)
                                progress.update(
                                    task_id,
                                    completed=percent,
                                    description=f"[cyan]{display_name}[/cyan] - {filename[:30]}"
                                )
                        
                        if self.cancelled:
                            try:
                                os.remove(temp_path)
                            except:
                                pass
                            return False
                        
                        os.replace(temp_path, lfile)
                        
                except Exception as e:
                    console.print(f"[red]Error copying {filename}: {e}[/red]")
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    return False
            
            progress.update(task_id, completed=100)
            return True
            
        except Exception as e:
            console.print(f"[red]Error copying directory: {e}[/red]")
            return False
    
    def _copy_single_item(self, item: Dict, console: Console, progress: Progress, task_id: int) -> bool:
        """Copy a single item from Pi to local laptop."""
        source_path = item['path']
        dest_path = self._get_local_destination(item)
        
        show_name = item['show']
        season = item.get('season')
        content_type = item.get('content_type', 'movie')
        
        if content_type == 'tv' and season:
            display_name = f"{show_name} S{season}"
        else:
            display_name = show_name
        
        try:
            # Check if source is directory or file
            try:
                entries = self.sftp.listdir_attr(source_path)
                is_dir = True
            except:
                is_dir = False
            
            if is_dir:
                return self._copy_directory(source_path, dest_path, console, progress, task_id, display_name)
            else:
                # Single file - use chunked copy with progress
                filename = os.path.basename(source_path)
                return self._copy_file_with_progress(
                    source_path, dest_path, console, progress, task_id, display_name, filename
                )
                
        except Exception as e:
            console.print(f"[red]Error copying {display_name}: {e}[/red]")
            return False
    
    def copy_items(self, items: List[Dict], console) -> bool:
        """Copy all selected items from Pi to local laptop."""
        if not items:
            console.print("[yellow]No items to copy.[/yellow]")
            return True
        
        dry_run = self.options_config.get('dry_run', False)
        mode_str = "DRY RUN" if dry_run else "COPY"
        
        console.print(f"\n[bold blue]Starting EXTERNAL {mode_str} (Pi → Laptop)...[/bold blue]")
        console.print(f"[dim]Total items: {len(items)}[/dim]\n")
        
        if dry_run:
            # Just show what would be copied
            for item in items:
                dest = self._get_local_destination(item)
                console.print(f"[yellow][DRY RUN] Would copy:[/yellow]")
                console.print(f"  From: {item['path']}")
                console.print(f"  To:   {dest}")
            return True
        
        # Connect to Pi
        if not self._connect():
            console.print("[red]Failed to connect to Pi for external copy.[/red]")
            return False
        
        all_success = True
        
        # Always use a real Rich Console for Progress internals.
        from rich.console import Console as RichConsole
        _progress_console = console if isinstance(console, RichConsole) else RichConsole(file=open(os.devnull, 'w'))
        
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=40),
                TaskProgressColumn(),
                "•",
                TimeElapsedColumn(),
                "•",
                TimeRemainingColumn(),
                console=_progress_console,
                transient=False,
            ) as progress:
                
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
                    
                    if content_type == 'tv' and season:
                        display_name = f"{show_name} S{season}"
                    else:
                        display_name = show_name
                    
                    task_id = progress.add_task(
                        f"[cyan]{display_name}[/cyan]",
                        total=100,
                        visible=True
                    )
                    
                    console.print(f"\n[bold]{i}/{len(items)}: {display_name}[/bold]")
                    
                    success = self._copy_single_item(item, console, progress, task_id)
                    
                    if success:
                        progress.update(overall_task, advance=1)
                        console.print(f"[green]  ✓ Completed {display_name}[/green]")
                    else:
                        console.print(f"[red]  ✗ Failed {display_name}[/red]")
                        all_success = False
                    
                    progress.remove_task(task_id)
        
        finally:
            if self.sftp:
                self.sftp.close()
            if self.ssh:
                self.ssh.close()
        
        return all_success
