"""Async helpers for running blocking calls in a ThreadPoolExecutor.

Provides small async wrappers around blocking subprocess/paramiko calls so
they can be awaited from async handlers without blocking the event loop.

Functions:
- async_run_cmd(command, timeout=None) -> (returncode, stdout, stderr)
- async_call(func, *args, **kwargs) -> result
- async_paramiko_exec(ssh, command) -> (exit_status, stdout, stderr)

Designed for conservative, incremental async migration (Phase 1).
"""

import asyncio
import functools
import subprocess
from typing import Any, Callable, Tuple


def _run_cmd_sync(command: str, timeout: int = None) -> Tuple[int, str, str]:
    """Run a shell command synchronously and return (rc, stdout, stderr)."""
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


async def async_run_cmd(command: str, timeout: int = None) -> Tuple[int, str, str]:
    """Run a shell command in a thread and return (rc, stdout, stderr)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(_run_cmd_sync, command, timeout))


async def async_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    """Run a blocking function in the default ThreadPoolExecutor and return its result."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


def _paramiko_exec_sync(ssh, command: str) -> Tuple[int, str, str]:
    """Execute a command over a paramiko SSHClient synchronously and collect output."""
    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    # Read all output (blocking) and return
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    try:
        exit_status = stdout.channel.recv_exit_status()
    except Exception:
        exit_status = 0
    return exit_status, out, err


async def async_paramiko_exec(ssh, command: str, timeout: int = None) -> Tuple[int, str, str]:
    """Run a paramiko exec_command in a thread and return (exit_status, stdout, stderr)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(_paramiko_exec_sync, ssh, command))


__all__ = ["async_run_cmd", "async_call", "async_paramiko_exec"]
