"""Hand `nxm://` links to the running dao-modman instance over a Unix socket."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

from . import paths

LOCK_NAME = "dao-modman.lock"
SOCKET_NAME = "dao-modman.sock"

# Receives a link; returns an error message, or None if the link was accepted.
LinkHandler = Callable[[str], str | None]


class IpcError(Exception):
    pass


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else paths.data_dir()


class InstanceLock:
    """Held by the one instance that listens for links."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing drops the lock
            self._fd = None


def is_running(directory: Path) -> bool:
    """True if an instance holds the lock, i.e. is (or is about to be) listening."""
    lock = InstanceLock(directory)
    if lock.acquire():
        lock.release()
        return False
    return True


async def serve(directory: Path, on_link: LinkHandler) -> asyncio.Server:
    """Listen for links. Only call while holding the InstanceLock."""
    path = directory / SOCKET_NAME
    path.unlink(missing_ok=True)  # we hold the lock, so an existing socket is stale

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            url = json.loads(line)["nxm"]
            if not isinstance(url, str):
                raise TypeError("nxm must be a string")
            error = on_link(url)
        except (TimeoutError, ValueError, KeyError, TypeError):
            error = "malformed request"
        writer.write(f"error: {error}\n".encode() if error else b"ok\n")
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(path))
    path.chmod(0o600)
    return server


async def stop(server: asyncio.Server, directory: Path) -> None:
    server.close()
    await server.wait_closed()
    (directory / SOCKET_NAME).unlink(missing_ok=True)


def send_link(directory: Path, url: str, timeout: float = 5.0) -> None:
    """Send a link to the running instance, waiting briefly for one that is still starting."""
    path = str(directory / SOCKET_NAME)
    deadline = time.monotonic() + timeout
    while True:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(path)
            break
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            client.close()
            if time.monotonic() >= deadline:
                raise IpcError("the running dao-modman instance does not answer") from exc
            time.sleep(0.1)
    try:
        client.settimeout(timeout)
        client.sendall(json.dumps({"nxm": url}).encode() + b"\n")
        reply = client.makefile("r", encoding="utf-8").readline().strip()
    except OSError as exc:
        raise IpcError(f"cannot talk to the running dao-modman instance: {exc}") from exc
    finally:
        client.close()
    if reply != "ok":
        raise IpcError(reply.removeprefix("error: ") or "no reply")
