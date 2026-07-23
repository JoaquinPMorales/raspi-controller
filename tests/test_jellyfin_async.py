import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jellyfin
import telegram_bot


@pytest.mark.asyncio
async def test_async_refresh_jellyfin_library_uses_httpx(monkeypatch):
    class FakeResponse:
        status_code = 204

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 10

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers):
            assert url == 'http://pi:8096/Library/Refresh'
            assert headers['X-Emby-Token'] == 'token'
            return FakeResponse()

    monkeypatch.setattr(jellyfin.httpx, 'AsyncClient', FakeClient)

    ok = await jellyfin.async_refresh_jellyfin_library('pi', 8096, 'token')

    assert ok is True


@pytest.mark.asyncio
async def test_async_refresh_jellyfin_library_uses_ssh_fallback(monkeypatch):
    class FakeScanner:
        ssh = object()

    monkeypatch.setattr(jellyfin, '_refresh_via_ssh', lambda scanner, host, port: True)

    ok = await jellyfin.async_refresh_jellyfin_library('pi', 8096, None, scanner=FakeScanner())

    assert ok is True


def test_refresh_via_ssh_rejects_failed_http_status():
    class Channel:
        def recv_exit_status(self):
            return 0

    class Stdout:
        channel = Channel()

        def read(self):
            return b'500'

    class SSH:
        def exec_command(self, command):
            return None, Stdout(), None

    class Scanner:
        ssh = SSH()

    assert jellyfin._refresh_via_ssh(Scanner(), 'pi', 8096) is False


@pytest.mark.asyncio
async def test_refresh_jellyfin_for_bot_async_prefers_async_api(monkeypatch):
    tracker = {}

    async def fake_async_refresh(host, port, api_key, scanner=None):
        tracker['args'] = (host, port, api_key, scanner)
        return True

    monkeypatch.setattr(telegram_bot, 'async_refresh_jellyfin_library', fake_async_refresh)

    ok = await telegram_bot._refresh_jellyfin_for_bot_async({
        'pi': {'host': 'pi'},
        'jellyfin': {'host': 'jf', 'port': 8097, 'api_key': 'token'},
    })

    assert ok is True
    assert tracker['args'] == ('jf', 8097, 'token', None)
