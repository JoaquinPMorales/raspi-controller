import asyncio
import os
import sys

import pytest

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


@pytest.mark.asyncio
async def test_reboot_callback_rejects_unauthorized_user(monkeypatch):
    class FakeQuery:
        data = 'reboot_confirm'

        def __init__(self):
            self.answers = []

        async def answer(self, text=None, show_alert=False):
            self.answers.append((text, show_alert))

    class User:
        id = 99

    class Update:
        effective_user = User()

        def __init__(self):
            self.callback_query = FakeQuery()

    class Context:
        bot_data = {'config': {'telegram': {'allowed_users': [1]}, 'pi': {}}}

    monkeypatch.setattr(telegram_bot, '_reboot_pi', lambda config: (_ for _ in ()).throw(AssertionError()))
    update = Update()

    await telegram_bot.reboot_callback(update, Context())

    assert update.callback_query.answers == [('Unauthorized.', True)]


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


def test_run_speed_test_installs_with_sudo_password(monkeypatch):
    tracker = {}

    class FakeChannel:
        def recv_exit_status(self):
            return 0

    class FakeStdout:
        def __init__(self, text, with_channel=False):
            self._text = text
            self.channel = FakeChannel() if with_channel else None

        def read(self):
            return self._text.encode()

    class FakeStdin:
        def write(self, text):
            tracker['written'] = text

        def flush(self):
            tracker['flushed'] = True

    class FakeSSH:
        def exec_command(self, command, timeout=None, get_pty=False):
            tracker.setdefault('commands', []).append(command)
            if 'which speedtest-cli' in command:
                return None, FakeStdout('not_installed'), None
            if 'apt install -y speedtest-cli' in command:
                return FakeStdin(), FakeStdout('', with_channel=True), None
            return None, FakeStdout('Ping: 1 ms\nDownload: 2 Mbit/s\nUpload: 3 Mbit/s'), None

        def close(self):
            tracker['closed'] = True

    monkeypatch.setattr(telegram_bot, '_open_ssh_client', lambda config: FakeSSH())

    success, output = telegram_bot._run_speed_test({'host': 'pi', 'sudo_password': 'secret'})

    assert success is True
    assert tracker['written'] == 'secret\n'
    assert tracker['flushed'] is True
    assert any('apt install -y speedtest-cli' in command for command in tracker['commands'])
    assert 'Upload:' in output


@pytest.mark.asyncio
async def test_long_running_operation_gate_rejects_second_operation():
    assert await telegram_bot._begin_long_running_operation("user:1", "copy") is True
    try:
        assert telegram_bot._long_running_operation_busy_text() == (
            "⚠️ Another copy operation is already running. Please wait for it to finish."
        )
        assert await telegram_bot._begin_long_running_operation("user:2", "backup") is False
    finally:
        telegram_bot._finish_long_running_operation("user:1")


@pytest.mark.asyncio
async def test_cancel_requests_active_operation(monkeypatch):
    class FakeOperation:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class FakeQuery:
        def __init__(self):
            self.texts = []
            self.message = None

        async def answer(self):
            return None

        async def edit_message_text(self, text):
            self.texts.append(text)

    class FakeUser:
        id = 7

    class FakeUpdate:
        effective_user = FakeUser()

        def __init__(self, query):
            self.callback_query = query
            self.message = None

    query = FakeQuery()
    update = FakeUpdate(query)
    context = object()
    telegram_bot.user_data[7] = {}

    operation = FakeOperation()
    assert await telegram_bot._begin_long_running_operation("user:7", "copy") is True
    telegram_bot._set_long_running_operation_resource("user:7", operation)

    try:
        result = await telegram_bot.cancel(update, context)
        assert result == telegram_bot.ConversationHandler.END
        assert operation.cancelled is True
        assert query.texts[-1] == "❌ Cancellation requested. Stopping the active operation..."
    finally:
        telegram_bot._finish_long_running_operation("user:7")


@pytest.mark.asyncio
async def test_run_copy_process_uses_run_blocking(monkeypatch):
    tracker = {}

    class FakeMessage:
        def __init__(self):
            self.texts = []

        async def edit_text(self, text, parse_mode=None):
            self.texts.append((text, parse_mode))

    class FakeQuery:
        def __init__(self):
            self.message = FakeMessage()

    class FakeUser:
        id = 11

    class FakeUpdate:
        effective_user = FakeUser()
        callback_query = FakeQuery()

    class FakeCopier:
        def __init__(self, pi_config, paths_config, options_config):
            tracker['copier_init'] = (pi_config, paths_config, options_config)

        def cancel(self):
            tracker['cancel_called'] = True

        def copy_items(self, selected, console, progress_callback=None):
            tracker['copy_items_called'] = True
            tracker['selected'] = selected
            progress_callback(2, 1, 100, 'episode.mkv')
            return True

    async def fake_run_blocking(func, *args, **kwargs):
        tracker['run_blocking_used'] = True
        return func()

    monkeypatch.setattr(telegram_bot, 'RsyncCopier', FakeCopier)
    monkeypatch.setattr(telegram_bot, '_run_blocking', fake_run_blocking)
    monkeypatch.setattr(telegram_bot, '_refresh_jellyfin_for_bot_async', lambda config: asyncio.sleep(0, result=True))

    telegram_bot.user_data[11] = {'dry_run': False}
    assert await telegram_bot._begin_long_running_operation("user:11", "copy") is True

    try:
        await telegram_bot.run_copy_process(
            FakeUpdate(),
            object(),
            [{'show': 'My Show', 'season': '01', 'content_type': 'tv'}],
            'internal',
            {'pi': {}, 'paths': {'jellyfin_shows': '/shows', 'jellyfin_movies': '/movies'}, 'options': {}},
        )
    finally:
        telegram_bot._finish_long_running_operation("user:11")

    assert tracker['run_blocking_used'] is True
    assert tracker['copy_items_called'] is True
    assert tracker['selected'][0]['show'] == 'My Show'
    assert not any('Error during copy' in text for text, _ in FakeUpdate.callback_query.message.texts)
    assert telegram_bot._active_bot_operation is None


@pytest.mark.asyncio
async def test_backup_command_uses_run_blocking(monkeypatch):
    tracker = {}

    class FakeMessage:
        def __init__(self):
            self.replies = []
            self.edits = []

        async def reply_text(self, text, parse_mode=None):
            self.replies.append((text, parse_mode))
            return self

        async def edit_text(self, text):
            self.edits.append(text)

    class FakeUser:
        id = 21

    class FakeUpdate:
        effective_user = FakeUser()

        def __init__(self):
            self.message = FakeMessage()

    class FakeContext:
        bot_data = {
            'config': {
                'telegram': {'allowed_users': []},
                'backup': {'enabled': True},
            }
        }

    class FakeBackup:
        def __init__(self, config):
            tracker['backup_init'] = config

        def create_backup(self, progress_callback=None):
            tracker['create_backup_called'] = True
            if progress_callback:
                progress_callback('halfway')
            return True, 'backup ok'

    async def fake_run_blocking(func, *args, **kwargs):
        tracker['run_blocking_used'] = True
        return func()

    import backup

    monkeypatch.setattr(backup, 'SystemBackup', FakeBackup)
    monkeypatch.setattr(telegram_bot, '_run_blocking', fake_run_blocking)

    update = FakeUpdate()
    await telegram_bot.backup_command(update, FakeContext())

    assert tracker['run_blocking_used'] is True
    assert tracker['create_backup_called'] is True
    assert update.message.edits[-1] == "✅ backup ok\n\nNext backup due in 30 days."
    assert telegram_bot._active_bot_operation is None
