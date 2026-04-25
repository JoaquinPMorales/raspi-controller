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


def test_get_temperature_report_formats_sensor_output(monkeypatch):
    class FakeStdout:
        def read(self):
            return b'54000'

    class FakeSSH:
        def exec_command(self, command):
            return None, FakeStdout(), None

        def close(self):
            pass

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    result = telegram_bot._get_temperature_report({'host': 'pi'})

    assert '54.0' in result
    assert 'Normal' in result


def test_get_service_status_lines_marks_installed_and_missing_services(monkeypatch):
    class FakeStdout:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode()

    class FakeSSH:
        def exec_command(self, command):
            if 'jellyfin' in command:
                return None, FakeStdout('active'), None
            if 'qbittorrent-nox' in command:
                return None, FakeStdout('inactive'), None
            return None, FakeStdout(''), None

        def close(self):
            pass

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    result = telegram_bot._get_service_status_lines({'host': 'pi'})

    assert 'Jellyfin: ✅ Running' in result
    assert 'qBittorrent: ❌ Stopped' in result
    assert 'Plex: ⚫ Not installed' in result


def test_get_memory_report_formats_usage(monkeypatch):
    outputs = iter([
        'Mem:           8.0G        2.0G        1.0G        0.1G        5.0G        5.5G',
        '25',
    ])

    class FakeStdout:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode()

    class FakeSSH:
        def exec_command(self, command):
            return None, FakeStdout(next(outputs)), None

        def close(self):
            pass

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    result = telegram_bot._get_memory_report({'host': 'pi'})

    assert '25%' in result
    assert '*Total:* 8.0G' in result
    assert '*Available:* 5.5G' in result


def test_reboot_pi_writes_sudo_password(monkeypatch):
    tracker = {}

    class FakeStdin:
        def write(self, text):
            tracker['written'] = text

        def flush(self):
            tracker['flushed'] = True

    class FakeSSH:
        def exec_command(self, command, get_pty=True):
            tracker['command'] = command
            return FakeStdin(), None, None

        def close(self):
            tracker['closed'] = True

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    telegram_bot._reboot_pi({'host': 'pi', 'sudo_password': 'secret'})

    assert tracker['command'] == 'sudo -S reboot 2>&1'
    assert tracker['written'] == 'secret\n'
    assert tracker['flushed'] is True
    assert tracker['closed'] is True


def test_search_downloads_returns_results(monkeypatch):
    outputs = iter([
        'ok',
        '/downloads/Show.Name.S01\n/downloads/Movie.Name.2024.mkv',
    ])

    class FakeStdout:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode()

    class FakeSSH:
        def exec_command(self, command):
            return None, FakeStdout(next(outputs)), None

        def close(self):
            pass

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    path_ok, results, path = telegram_bot._search_downloads({'host': 'pi'}, '/downloads', 'show')

    assert path_ok is True
    assert path == '/downloads'
    assert any(line.startswith('🎬') for line in results)


def test_run_speed_test_returns_output(monkeypatch):
    outputs = iter([
        '/usr/bin/speedtest-cli',
        'Ping: 20.0 ms\nDownload: 50.0 Mbit/s\nUpload: 10.0 Mbit/s',
    ])

    class FakeStdout:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode()

    class FakeSSH:
        def exec_command(self, command, timeout=None):
            return None, FakeStdout(next(outputs)), None

        def close(self):
            pass

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    success, output = telegram_bot._run_speed_test({'host': 'pi'})

    assert success is True
    assert 'Download:' in output
