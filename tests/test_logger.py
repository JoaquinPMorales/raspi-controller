import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
import json
from logger import JSONFormatter, get_logger


def test_json_formatter_outputs_json():
    fmt = JSONFormatter()
    rec = logging.LogRecord("test", logging.INFO, __file__, 10, "hello", (), None)
    out = fmt.format(rec)
    data = json.loads(out)
    assert data["message"] == "hello"
    assert data["level"] == "INFO"


def test_get_logger_writes_file(tmp_path):
    log_file = tmp_path / "test.log"
    logger = get_logger("test_obs_file", level=logging.DEBUG, json_format=True, log_file=str(log_file))
    logger.info("it works")
    with open(log_file) as f:
        content = f.read()
    assert "it works" in content
