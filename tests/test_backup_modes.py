import sys
import os

# Ensure project root is on sys.path so tests can import project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backup import SystemBackup


def test_create_backup_dispatch_full(monkeypatch, tmp_path):
    config = {'backup': {'mode': 'full'}}
    sb = SystemBackup(config)
    # Use a temporary local_path to avoid permission issues
    sb.local_path = str(tmp_path)

    called = {}

    def fake_full(self, progress_callback=None):
        called['full'] = True
        return True, 'full ok'

    monkeypatch.setattr(SystemBackup, '_create_full_image', fake_full)
    res = sb.create_backup()
    assert res == (True, 'full ok')
    assert called.get('full')


def test_create_backup_dispatch_rsync(monkeypatch, tmp_path):
    config = {'backup': {'mode': 'rsync'}}
    sb = SystemBackup(config)
    sb.local_path = str(tmp_path)

    called = {}

    def fake_rsync(self, progress_callback=None):
        called['rsync'] = True
        return True, 'rsync ok'

    monkeypatch.setattr(SystemBackup, '_create_rsync_snapshot', fake_rsync)
    res = sb.create_backup()
    assert res == (True, 'rsync ok')
    assert called.get('rsync')


def test_create_backup_dispatch_restic(monkeypatch, tmp_path):
    config = {'backup': {'mode': 'restic'}}
    sb = SystemBackup(config)
    sb.local_path = str(tmp_path)

    called = {}

    def fake_restic(self, progress_callback=None):
        called['restic'] = True
        return True, 'restic ok'

    monkeypatch.setattr(SystemBackup, '_create_restic_snapshot', fake_restic)
    res = sb.create_backup()
    assert res == (True, 'restic ok')
    assert called.get('restic')
