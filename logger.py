"""Observability helpers: structured JSON logging and simple configuration helpers.

Provides:
- JSONFormatter: lightweight JSON formatter for structured logs
- get_logger(name, ...): returns a configured logger (console + optional rotating file)
- configure_from_config(config): convenience to configure root logger from a dict
"""

import logging
import json
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any


class JSONFormatter(logging.Formatter):
    """Format log records as JSON.

    Minimal fields: timestamp (UTC ISO), level, logger, message.
    Extra LogRecord attributes (if any) are copied into the top-level JSON object.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Copy non-standard attributes from the record (best-effort)
        standard_keys = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
            "process",
        }
        for k, v in record.__dict__.items():
            if k not in standard_keys:
                try:
                    json.dumps(v)  # ensure serializable
                    payload[k] = v
                except Exception:
                    # skip non-serializable extras
                    payload[k] = str(v)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(
    name: str,
    level: int = logging.INFO,
    json_format: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Return a configured logger.

    Multiple calls with the same logger name are idempotent (handlers added once).
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_obs_configured", False):
        return logger

    logger.setLevel(level)
    formatter = JSONFormatter() if json_format else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Rotating file handler (optional)
    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger._obs_configured = True
    return logger


def configure_from_config(config: Optional[Dict[str, Any]]):
    """Configure root logger from a config dict (e.g., loaded YAML config).

    Expected structure:
    logging:
      level: "INFO"
      json: false
      file: "/var/log/raspi-controller.log"
      max_bytes: 10485760
      backup_count: 5
    """
    if not config:
        return
    log_cfg = config.get("logging", {})
    level_name = str(log_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    json_format = bool(log_cfg.get("json", False))
    log_file = log_cfg.get("file")
    try:
        max_bytes = int(log_cfg.get("max_bytes", 10 * 1024 * 1024))
    except Exception:
        max_bytes = 10 * 1024 * 1024
    try:
        backup_count = int(log_cfg.get("backup_count", 5))
    except Exception:
        backup_count = 5

    # Configure a project-level named logger to force handler setup
    get_logger("raspi_controller", level=level, json_format=json_format, log_file=log_file, max_bytes=max_bytes, backup_count=backup_count)


def new_op_id() -> str:
    """Generate a short operation id for correlation (hex uuid4)."""
    import uuid
    return uuid.uuid4().hex


def get_logger_adapter(name: str, op_id: Optional[str] = None, **kwargs) -> logging.LoggerAdapter:
    """Return a LoggerAdapter that injects op_id into log records via the `extra` mapping."""
    base = get_logger(name, **kwargs)
    return logging.LoggerAdapter(base, {"op_id": op_id})


__all__ = ["get_logger", "JSONFormatter", "configure_from_config", "get_logger_adapter", "new_op_id"]
