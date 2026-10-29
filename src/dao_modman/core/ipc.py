"""Hand `nxm://` links to the running dao-modman instance.

The instance listens on a localhost TCP port, the same on every OS. The port and a random
token are in a file only the user can read; a request without the token is refused, so other
users on the machine cannot send links. Only the instance lock differs between OSes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socket
import time
from collections.abc import Callable
from pathlib import Path

from . import paths

LOCK_NAME = "dao-modman.lock"
PORT_NAME = "dao-modman.port"  # {"port": ..., "token": ...} of the listening instance
HOST = "127.0.0.1"

# Receives a link; returns an error message, or None if the link was accepted.
LinkHandler = Callable[[str], str | None]


class IpcError(Exception):
    pass


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else paths.data_dir()


def _lock(fd: int) -> None:
    """Lock the file without waiting; OSError if another process holds it."""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":  # Windows does not promise to drop the lock on close
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    # elsewhere closing the file drops the lock


class InstanceLock:
    """Held by the one instance that listens for links."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None


def is_running(directory: Path) -> bool:
    """True if an instance holds the lock, i.e. is (or is about to be) listening."""
    lock = InstanceLock(directory)
    if lock.acquire():
        lock.release()
        return False
    return True


def _write_private(path: Path, data: bytes) -> None:
    """Write a file only the user can read, replacing it in one step."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)  # O_CREAT keeps the mode of an existing file
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


async def serve(directory: Path, on_link: LinkHandler) -> asyncio.Server:
    """Listen for links. Only call while holding the InstanceLock."""
    token = secrets.token_hex(32)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            request = json.loads(line)
            given = request.get("token") if isinstance(request, dict) else None
            if not isinstance(given, str) or not hmac.compare_digest(given, token):
                error = "not authorized"
            else:
                url = request["nxm"]
                if not isinstance(url, str):
                    raise TypeError("nxm must be a string")
                error = on_link(url)
        except (TimeoutError, ValueError, KeyError, TypeError):
            error = "malformed request"
        writer.write(f"error: {error}\n".encode() if error else b"ok\n")
        with contextlib.suppress(OSError):
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, host=HOST, port=0)
    port = server.sockets[0].getsockname()[1]
    try:
        # we hold the lock, so an existing port file is stale and can be replaced
        _write_private(directory / PORT_NAME, json.dumps({"port": port, "token": token}).encode())
    except OSError:
        server.close()
        raise
    return server


async def stop(server: asyncio.Server, directory: Path) -> None:
    server.close()
    await server.wait_closed()
    (directory / PORT_NAME).unlink(missing_ok=True)


def _read_port_file(path: Path) -> tuple[int, str] | None:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None  # not written yet, or being replaced


def send_link(directory: Path, url: str, timeout: float = 5.0) -> None:
    """Send a link to the running instance, waiting briefly for one that is still starting."""
    deadline = time.monotonic() + timeout
    while True:
        info = _read_port_file(directory / PORT_NAME)
        if info is not None:
            try:
                client = socket.create_connection((HOST, info[0]), timeout=timeout)
                break
            except OSError:
                pass  # left over from an instance that is gone, or not listening yet
        if time.monotonic() >= deadline:
            raise IpcError("the running dao-modman instance does not answer")
        time.sleep(0.1)
    try:
        client.sendall(json.dumps({"token": info[1], "nxm": url}).encode() + b"\n")
        reply = client.makefile("r", encoding="utf-8").readline().strip()
    except OSError as exc:
        raise IpcError(f"cannot talk to the running dao-modman instance: {exc}") from exc
    finally:
        client.close()
    if reply != "ok":
        raise IpcError(reply.removeprefix("error: ") or "no reply")
