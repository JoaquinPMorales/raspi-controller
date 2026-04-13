"""Simple alerting helpers (Telegram) used by background tasks.

Provides:
- send_telegram_alert(token, chat_id, text) -> (success: bool, message: str)
- notify_config(config, text) -> (success, message) - uses config.telegram.allowed_users or backup.alert_chat_id

This module intentionally implements a tiny HTTP client with urllib to avoid adding a new dependency.
"""
import json
import urllib.request
import urllib.error
from typing import Tuple, Optional, Dict, Any


def send_telegram_alert(token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> Tuple[bool, str]:
    """Send a message using the Bot API.

    Returns (success, message_or_error)
    """
    if not token or not chat_id:
        return False, "Missing token or chat_id"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            decoded = json.loads(body)
            if decoded.get("ok"):
                return True, str(decoded.get("result", {}))
            else:
                return False, decoded.get("description", "telegram api error")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return False, f"HTTPError {e.code}: {body}"
        except Exception:
            return False, f"HTTPError {e.code}"
    except Exception as e:
        return False, str(e)


def notify_config(config: Optional[Dict[str, Any]], text: str) -> Tuple[bool, str]:
    """Helper that reads telegram config from the project config and sends an alert.

    It prefers a backup.alert_chat_id if present, otherwise uses the first entry in telegram.allowed_users.
    """
    if not config:
        return False, "No config provided"
    tg = config.get("telegram", {}) or {}
    token = tg.get("token")
    allowed = tg.get("allowed_users", []) or []

    # allow override in backup config
    backup_cfg = config.get("backup", {}) or {}
    chat_id = backup_cfg.get("alert_chat_id")
    if not chat_id:
        if allowed:
            chat_id = allowed[0]
    if not chat_id:
        return False, "No chat_id available in config (set backup.alert_chat_id or telegram.allowed_users)"

    return send_telegram_alert(token, str(chat_id), text)


__all__ = ["send_telegram_alert", "notify_config"]
