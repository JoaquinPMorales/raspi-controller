import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
import json
from logger import JSONFormatter


def test_json_formatter_includes_op_id():
    fmt = JSONFormatter()
    rec = logging.LogRecord("test", logging.INFO, __file__, 10, "hello", (), None)
    rec.__dict__["op_id"] = "abc123"
    out = fmt.format(rec)
    data = json.loads(out)
    assert data["op_id"] == "abc123"
