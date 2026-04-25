import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import telegram_bot


def test_scan_download_items_returns_none_when_connect_fails(monkeypatch):
    class FakeScanner:
        def __init__(self, config, tmdb_api_key=None):
            self.closed = False

        def connect(self):
            return False

        def close(self):
            self.closed = True

    monkeypatch.setattr(telegram_bot, 'FolderScanner', FakeScanner)

    assert telegram_bot._scan_download_items({'host': 'pi'}, '/downloads', None) is None


def test_calculate_selection_stats_internal_uses_min_available_and_closes(monkeypatch):
    tracker = {}

    class FakeScanner:
        def __init__(self, config):
            tracker['scanner'] = self

        def connect(self):
            return True

        def calculate_items_size(self, selected):
            return 300

        def get_disk_space(self, path):
            if path.endswith('Shows'):
                return {'available': 500}
            return {'available': 700}

        def close(self):
            tracker['closed'] = True

    monkeypatch.setattr(telegram_bot, 'FolderScanner', FakeScanner)

    total_size, dest_free, dest_path = telegram_bot._calculate_selection_stats(
        {'host': 'pi'},
        {'jellyfin_shows': '/mnt/media/Shows', 'jellyfin_movies': '/mnt/media/Movies'},
        [{'path': '/downloads/item'}],
        'internal',
    )

    assert total_size == 300
    assert dest_free == 500
    assert dest_path == '/mnt/media'
    assert tracker['closed'] is True


def test_refresh_jellyfin_for_bot_uses_short_lived_scanner_without_api_key(monkeypatch):
    tracker = {}

    class FakeScanner:
        def __init__(self, config):
            tracker['scanner'] = self

        def connect(self):
            tracker['connected'] = True
            return True

        def close(self):
            tracker['closed'] = True

    def fake_refresh_jellyfin_library(host, port, api_key, scanner=None):
        tracker['refresh_args'] = (host, port, api_key, scanner)
        return True

    monkeypatch.setattr(telegram_bot, 'FolderScanner', FakeScanner)
    monkeypatch.setattr(telegram_bot, 'refresh_jellyfin_library', fake_refresh_jellyfin_library)

    assert telegram_bot._refresh_jellyfin_for_bot({'pi': {'host': 'pi'}, 'jellyfin': {}}) is True
    assert tracker['connected'] is True
    assert tracker['refresh_args'] == ('pi', 8096, None, tracker['scanner'])
    assert tracker['closed'] is True


def test_run_auto_backup_cycle_only_runs_backup_when_due(monkeypatch):
    class FakeBackup:
        def __init__(self, config):
            self.created = False

        def needs_backup(self):
            return True

        def create_backup(self):
            return True, 'backup ok'

    monkeypatch.setattr('backup.SystemBackup', FakeBackup)

    due, success, message = telegram_bot._run_auto_backup_cycle({'backup': {'enabled': True}})

    assert (due, success, message) == (True, True, 'backup ok')
