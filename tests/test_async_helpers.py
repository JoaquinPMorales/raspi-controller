import pytest
import sys
import os
# Ensure project root is on sys.path so tests can import project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from async_helpers import async_call, async_run_cmd


def blocking_add(a, b):
    import time
    time.sleep(0.05)
    return a + b


@pytest.mark.asyncio
async def test_async_call():
    res = await async_call(blocking_add, 2, 3)
    assert res == 5


@pytest.mark.asyncio
async def test_async_run_cmd_echo():
    rc, out, err = await async_run_cmd("echo hello")
    assert rc == 0
    assert out.strip() == "hello"
