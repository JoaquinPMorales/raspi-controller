import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import urllib.request
from alerts import send_telegram_alert


class FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def fake_urlopen_success(req, timeout=10):
    return FakeResp(json.dumps({"ok": True, "result": {"message_id": 123}}).encode())


def fake_urlopen_failure(req, timeout=10):
    return FakeResp(json.dumps({"ok": False, "description": "Bad token"}).encode())


def test_send_telegram_alert_success(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_success)
    ok, msg = send_telegram_alert("token", "123", "hello")
    assert ok is True


def test_send_telegram_alert_failure(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_failure)
    ok, msg = send_telegram_alert("token", "123", "hello")
    assert ok is False
