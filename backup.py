"""
System backup module for Raspberry Pi.
Creates SD card images and uploads to cloud storage.
"""

import os
import subprocess
import json
import logging
import shlex
import shutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)
try:
    from alerts import notify_config
except ImportError:
    def notify_config(cfg, text):
        return False, "alerts not available"


DEFAULT_STATUS = {'last_backup': None, 'last_success': None, 'cloud_sync': False}


class SystemBackup:
    """Handle system backups with cloud upload and rotation."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.backup_config = config.get('backup', {})
        self.source_device = self.backup_config.get('source_device', '/dev/mmcblk0')
        self.local_path = self.backup_config.get('local_path', '/mnt/storage/backups')
        self.cloud_enabled = self.backup_config.get('cloud_enabled', False)
        self.cloud_remote = self.backup_config.get('cloud_remote', 'gdrive:Backups')
        self.keep_local = self.backup_config.get('keep_local', True)
        self.status_file = os.path.join(self.local_path, '.backup_status.json')

    def _default_status(self) -> Dict[str, Any]:
        """Return a fresh default backup status payload."""
        return dict(DEFAULT_STATUS)

    def _notify(self, text: str) -> None:
        """Send a best-effort alert and log delivery failures."""
        ok, message = notify_config(self.config, text)
        if not ok:
            logger.warning("Backup alert not sent: %s", message)

    def _cleanup_file(self, path: str) -> None:
        """Remove a partially created file and log cleanup failures."""
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Failed to remove file %s: %s", path, exc)

    def _remove_previous_backup(self, path: str) -> None:
        """Delete an older local backup and its cloud copy when enabled."""
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", path, exc)
            return

        if self.cloud_enabled:
            self.remove_from_cloud(os.path.basename(path))

    def _parse_last_backup(self, status: Dict[str, Any]) -> Optional[datetime]:
        """Parse the last-backup timestamp and log malformed values."""
        last_backup = status.get('last_backup')
        if not last_backup:
            return None

        try:
            return datetime.fromisoformat(last_backup)
        except ValueError:
            logger.warning("Invalid last_backup timestamp in %s: %r", self.status_file, last_backup)
            return None
    
    def get_backup_filename(self) -> str:
        """Generate backup filename with timestamp."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"raspi-backup-{timestamp}.img.gz"
    
    def get_latest_backup(self) -> Optional[str]:
        """Find the most recent backup file."""
        if not os.path.exists(self.local_path):
            return None
        
        backups = []
        for f in os.listdir(self.local_path):
            if f.startswith('raspi-backup-') and (f.endswith('.img.gz') or f.endswith('.tar.gz')):
                backups.append(f)
        
        if not backups:
            return None
        
        # Sort by filename (timestamp is in the name)
        backups.sort(reverse=True)
        return os.path.join(self.local_path, backups[0])
    
    def load_status(self) -> Dict:
        """Load backup status from JSON file."""
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, 'r') as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to load backup status from %s: %s", self.status_file, exc)
        return self._default_status()
    
    def save_status(self, status: Dict):
        """Save backup status to JSON file."""
        os.makedirs(self.local_path, exist_ok=True)
        with open(self.status_file, 'w') as f:
            json.dump(status, f, indent=2)
    
    def needs_backup(self) -> bool:
        """Check if a monthly backup is due."""
        status = self.load_status()
        last = self._parse_last_backup(status)
        if last is None:
            return True
        next_due = last + timedelta(days=30)
        return datetime.now() >= next_due
    
    def create_backup(self, progress_callback=None) -> tuple[bool, str]:
        """
        Create a new system backup.
        Returns (success, message)

        Supports multiple modes configured via backup.mode: 'full' (dd image),
        'rsync' (snapshot via rsync + tar archive), 'restic'. Default is 'full'.
        """
        os.makedirs(self.local_path, exist_ok=True)
        mode = self.backup_config.get('mode', 'full')

        if progress_callback:
            progress_callback(f"Selected backup mode: {mode}")

        if mode == 'full':
            return self._create_full_image(progress_callback)
        elif mode == 'rsync':
            return self._create_rsync_snapshot(progress_callback)
        elif mode == 'restic':
            return self._create_restic_snapshot(progress_callback)
        else:
            return False, f"Unknown backup mode: {mode}"

    def _create_full_image(self, progress_callback=None) -> tuple[bool, str]:
        """Create a full dd+gzip image (legacy behaviour)."""
        backup_file = os.path.join(self.local_path, self.get_backup_filename())
        old_backup = self.get_latest_backup()

        try:
            cmd = (
                f"sudo dd if={shlex.quote(self.source_device)} bs=4M status=progress "
                f"| gzip > {shlex.quote(backup_file)}"
            )
            if progress_callback:
                progress_callback("Starting full-image backup (dd + gzip)...")

            logger.info(f"Creating backup: {backup_file}")
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=7200  # 2 hour timeout
            )

            if result.returncode != 0:
                self._cleanup_file(backup_file)
                self._notify(f"Backup failed: {result.stderr}")
                return False, f"Backup failed: {result.stderr}"

            size = os.path.getsize(backup_file)
            size_mb = size / (1024 * 1024)

            if progress_callback:
                progress_callback(f"Backup created: {size_mb:.1f} MB")

            if self.cloud_enabled:
                if progress_callback:
                    progress_callback("Uploading to cloud storage...")

                cloud_success, cloud_msg = self.upload_to_cloud(backup_file, progress_callback)
                if not cloud_success:
                    logger.warning(f"Cloud upload failed: {cloud_msg}")

            if old_backup and old_backup != backup_file:
                if progress_callback:
                    progress_callback("Removing old backup...")
                self._remove_previous_backup(old_backup)

            status = self.load_status()
            status['last_backup'] = datetime.now().isoformat()
            status['last_success'] = datetime.now().isoformat()
            status['latest_file'] = os.path.basename(backup_file)
            status['latest_size'] = size
            status['cloud_sync'] = self.cloud_enabled
            self.save_status(status)

            self._notify(f"Backup completed: {os.path.basename(backup_file)} ({size_mb:.1f} MB)")

            return True, f"Backup completed: {os.path.basename(backup_file)} ({size_mb:.1f} MB)"

        except subprocess.TimeoutExpired:
            self._cleanup_file(backup_file)
            return False, "Backup timed out after 2 hours"
        except OSError as e:
            self._cleanup_file(backup_file)
            return False, f"Backup error: {str(e)}"

    def _create_rsync_snapshot(self, progress_callback=None) -> tuple[bool, str]:
        """
        Create an rsync-based snapshot of a configured source_path.
        Requires backup.source_path in config.
        Creates a tar.gz of the snapshot for upload.
        """
        source_path = self.backup_config.get('source_path')
        if not source_path:
            return False, "rsync mode requires 'source_path' in backup config"
        if not os.path.exists(source_path):
            return False, f"rsync source_path does not exist: {source_path}"

        snapshots_root = os.path.join(self.local_path, 'snapshots')
        os.makedirs(snapshots_root, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        snapshot_name = f"snapshot-{timestamp}"
        snapshot_path = os.path.join(snapshots_root, snapshot_name)

        # Find latest snapshot for link-dest
        latest = None
        try:
            entries = [d for d in os.listdir(snapshots_root) if d.startswith('snapshot-')]
            entries.sort(reverse=True)
            if entries:
                latest = os.path.join(snapshots_root, entries[0])
        except OSError as exc:
            logger.warning("Failed to inspect snapshots in %s: %s", snapshots_root, exc)
            latest = None

        rsync_cmd = ['rsync', '-a', '--delete']
        if latest:
            rsync_cmd += ['--link-dest', latest]
        rsync_cmd += [source_path.rstrip('/') + '/', snapshot_path]

        try:
            if progress_callback:
                progress_callback("Creating rsync snapshot...")

            result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=7200)
            if result.returncode != 0:
                self._notify(f"rsync failed: {result.stderr}")
                return False, f"rsync failed: {result.stderr}"

            # Tar the snapshot for upload
            backup_file = os.path.join(self.local_path, f"raspi-backup-{timestamp}-rsync.tar.gz")
            tar_cmd = ['tar', '-C', snapshots_root, '-czf', backup_file, snapshot_name]
            if progress_callback:
                progress_callback("Archiving snapshot...")
            result = subprocess.run(tar_cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode != 0:
                self._cleanup_file(backup_file)
                self._notify(f"tar failed: {result.stderr}")
                return False, f"tar failed: {result.stderr}"

            size = os.path.getsize(backup_file)

            if self.cloud_enabled:
                if progress_callback:
                    progress_callback("Uploading to cloud storage...")
                cloud_success, cloud_msg = self.upload_to_cloud(backup_file, progress_callback)
                if not cloud_success:
                    logger.warning(f"Cloud upload failed: {cloud_msg}")

            # Clean up old tar backup files
            old_backup = self.get_latest_backup()
            if old_backup and os.path.basename(old_backup) != os.path.basename(backup_file):
                self._remove_previous_backup(old_backup)

            status = self.load_status()
            status['last_backup'] = datetime.now().isoformat()
            status['last_success'] = datetime.now().isoformat()
            status['latest_file'] = os.path.basename(backup_file)
            status['latest_size'] = size
            status['cloud_sync'] = self.cloud_enabled
            self.save_status(status)

            self._notify(f"Snapshot completed: {os.path.basename(backup_file)} ({size/1024/1024:.1f} MB)")

            return True, f"Snapshot completed: {os.path.basename(backup_file)} ({size/1024/1024:.1f} MB)"

        except subprocess.TimeoutExpired:
            self._cleanup_file(os.path.join(self.local_path, f"raspi-backup-{timestamp}-rsync.tar.gz"))
            return False, "rsync/tar timed out"
        except OSError as e:
            self._cleanup_file(os.path.join(self.local_path, f"raspi-backup-{timestamp}-rsync.tar.gz"))
            return False, f"Snapshot error: {e}"

    def _create_restic_snapshot(self, progress_callback=None) -> tuple[bool, str]:
        """
        Create a restic snapshot. Requires restic in PATH and RESTIC_PASSWORD env set or config.
        """
        restic_repo = self.backup_config.get('restic_repo')
        source_path = self.backup_config.get('source_path')
        if not restic_repo or not source_path:
            return False, "restic mode requires 'restic_repo' and 'source_path' in backup config"
        if not os.path.exists(source_path):
            return False, f"restic source_path does not exist: {source_path}"

        try:
            if progress_callback:
                progress_callback("Running restic backup...")
            cmd = ['restic', '-r', restic_repo, 'backup', source_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            if result.returncode != 0:
                self._notify(f"restic failed: {result.stderr}")
                return False, f"restic failed: {result.stderr}"

            status = self.load_status()
            status['last_backup'] = datetime.now().isoformat()
            status['last_success'] = datetime.now().isoformat()
            status['latest_file'] = f"restic:{source_path}"
            status['latest_size'] = 0
            status['cloud_sync'] = True
            self.save_status(status)

            self._notify(f"Restic backup completed for {source_path}")

            return True, "Restic backup completed"
        except subprocess.TimeoutExpired:
            return False, "restic timed out"
        except OSError as e:
            return False, f"restic error: {e}"
    
    def upload_to_cloud(self, local_file: str, progress_callback=None) -> tuple[bool, str]:
        """
        Upload backup to cloud storage using rclone.
        Returns (success, message)
        """
        try:
            if not shutil.which('rclone'):
                return False, "rclone not installed. Run: sudo apt install rclone"
            
            filename = os.path.basename(local_file)
            remote_path = f"{self.cloud_remote}/{filename}"
            
            if progress_callback:
                progress_callback(f"Uploading {filename} to cloud...")
            
            # Use rclone to copy with progress
            cmd = ['rclone', 'copy', local_file, self.cloud_remote, '--progress', '--transfers', '1']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            
            if result.returncode != 0:
                return False, f"rclone error: {result.stderr}"
            
            return True, f"Uploaded to {remote_path}"
            
        except subprocess.TimeoutExpired:
            return False, "Cloud upload timed out after 1 hour"
        except OSError as e:
            return False, f"Upload error: {str(e)}"
    
    def remove_from_cloud(self, filename: str):
        """Remove a file from cloud storage."""
        try:
            if not shutil.which('rclone'):
                logger.warning("Cannot remove %s from cloud because rclone is not installed", filename)
                return
            remote_path = f"{self.cloud_remote}/{filename}"
            result = subprocess.run(
                ['rclone', 'delete', remote_path],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                logger.warning("Failed to remove %s from cloud: %s", filename, result.stderr.strip() or f"exit {result.returncode}")
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to remove from cloud: {e}")
    
    def get_status_text(self) -> str:
        """Get human-readable backup status."""
        status = self.load_status()
        last = self._parse_last_backup(status)

        if last is None:
            if status.get('last_backup'):
                return "⚠️ Backup status file is invalid. Run a new backup to refresh it."
            return "⚠️ No backup has been created yet"

        next_due = last + timedelta(days=30)
        days_until = (next_due - datetime.now()).days
        
        lines = [
            f"📦 *Backup Status*",
            f"",
            f"*Last backup:* {last.strftime('%Y-%m-%d %H:%M')}",
        ]
        
        if 'latest_file' in status:
            size_mb = status.get('latest_size', 0) / (1024 * 1024)
            lines.append(f"*File:* `{status['latest_file']}`")
            lines.append(f"*Size:* {size_mb:.1f} MB")
        
        if days_until > 0:
            lines.append(f"*Next backup due:* In {days_until} days")
        else:
            lines.append(f"*⚠️ Backup overdue by:* {abs(days_until)} days")
        
        if self.cloud_enabled and status.get('cloud_sync'):
            lines.append(f"*Cloud:* ✅ Synced to {self.cloud_remote}")
        elif self.cloud_enabled:
            lines.append(f"*Cloud:* ⚠️ Not synced")
        
        return '\n'.join(lines)


def setup_rclone_instructions() -> str:
    """Return instructions for setting up rclone with Google Drive."""
    return """
To set up Google Drive backup:

1. Install rclone:
   sudo apt update && sudo apt install rclone

2. Configure Google Drive:
   rclone config
   
   - Select 'n' for new remote
   - Name it 'gdrive'
   - Select 'drive' for Google Drive
   - Follow OAuth setup (requires browser)
   
3. Test the connection:
   rclone listremotes
   rclone lsd gdrive:

4. Create a 'Backups' folder in your Google Drive

5. Update config.yaml:
   backup:
     enabled: true
     cloud_enabled: true
     cloud_remote: "gdrive:Backups"
     local_path: "/mnt/storage/backups"
     source_device: "/dev/mmcblk0"

For more details: https://rclone.org/drive/
"""
