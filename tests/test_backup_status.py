import json
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backup import SystemBackup


def make_backup(tmp_path, backup_config=None):
    return SystemBackup({'backup': {'local_path': str(tmp_path), **(backup_config or {})}})


def test_load_status_invalid_json_returns_default_and_logs_warning(tmp_path, caplog):
    backup = make_backup(tmp_path)
    os.makedirs(tmp_path, exist_ok=True)
    with open(backup.status_file, 'w') as fh:
        fh.write('{invalid json')

    with caplog.at_level(logging.WARNING):
        status = backup.load_status()

    assert status == {'last_backup': None, 'last_success': None, 'cloud_sync': False}
    assert any('Failed to load backup status' in message for message in caplog.messages)


def test_needs_backup_with_invalid_timestamp_returns_true(tmp_path, caplog):
    backup = make_backup(tmp_path)
    with open(backup.status_file, 'w') as fh:
        json.dump({'last_backup': 'not-a-date'}, fh)

    with caplog.at_level(logging.WARNING):
        assert backup.needs_backup() is True

    assert any('Invalid last_backup timestamp' in message for message in caplog.messages)


def test_get_status_text_with_invalid_timestamp_returns_explicit_message(tmp_path):
    backup = make_backup(tmp_path)
    with open(backup.status_file, 'w') as fh:
        json.dump({'last_backup': 'not-a-date'}, fh)

    assert backup.get_status_text() == "⚠️ Backup status file is invalid. Run a new backup to refresh it."


def test_upload_to_cloud_reports_missing_rclone(tmp_path, monkeypatch):
    backup = make_backup(tmp_path, {'cloud_enabled': True})
    monkeypatch.setattr('backup.shutil.which', lambda name: None)

    ok, message = backup.upload_to_cloud(str(tmp_path / 'backup.img.gz'))

    assert ok is False
    assert message == "rclone not installed. Run: sudo apt install rclone"


def test_remove_from_cloud_logs_failure(tmp_path, monkeypatch, caplog):
    backup = make_backup(tmp_path, {'cloud_enabled': True})
    monkeypatch.setattr('backup.shutil.which', lambda name: '/usr/bin/rclone')

    class Result:
        returncode = 1
        stderr = 'permission denied'

    monkeypatch.setattr('backup.subprocess.run', lambda *args, **kwargs: Result())

    with caplog.at_level(logging.WARNING):
        backup.remove_from_cloud('old-backup.img.gz')

    assert any('permission denied' in message for message in caplog.messages)
