import pytest
import sys
import os
import subprocess

# Ensure project root is on sys.path so tests can import project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from async_helpers import async_paramiko_exec, async_run_cmd


class FakeChannel:
    def __init__(self, rc=0):
        self._rc = rc

    def recv_exit_status(self):
        return self._rc


class FakeStdout:
    def __init__(self, data: bytes, rc: int = 0):
        self._data = data
        self.channel = FakeChannel(rc)

    def read(self):
        return self._data


class FakeStderr:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeSSH:
    def exec_command(self, command, get_pty=True):
        stdin = None
        stdout = FakeStdout(b'hello', rc=0)
        stderr = FakeStderr(b'')
        return stdin, stdout, stderr


@pytest.mark.asyncio
async def test_async_paramiko_exec_success():
    ssh = FakeSSH()
    rc, out, err = await async_paramiko_exec(ssh, 'ls -la')
    assert rc == 0
    assert out == 'hello'
    assert err == ''


@pytest.mark.asyncio
async def test_async_paramiko_exec_exception():
    class BadSSH:
        def exec_command(self, command, get_pty=True):
            raise RuntimeError('boom')

    with pytest.raises(RuntimeError):
        await async_paramiko_exec(BadSSH(), 'bad')


@pytest.mark.asyncio
async def test_async_run_cmd_timeout():
    # Use a short sleep command and an extremely small timeout to trigger TimeoutExpired
    with pytest.raises(subprocess.TimeoutExpired):
        await async_run_cmd('sh -c "sleep 0.05"', timeout=0.001)
