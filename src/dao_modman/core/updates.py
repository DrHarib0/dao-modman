"""Is there a newer dao-modman on PyPI, and how is this installation updated?

The update itself cannot run inside dao-modman: the running program holds its own files open
(Windows does not let them be replaced), so it runs in a new window after dao-modman has quit.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx2

from . import terminal
from .nexus import APP_NAME, APP_VERSION

PYPI_URL = f"https://pypi.org/pypi/{APP_NAME}/json"
_RELEASE = re.compile(r"\d+(\.\d+)*")  # 1.2.3; not 1.2.3.dev4+g5678 or 1.2.3rc1
WINDOWS = sys.platform == "win32"

PYAPP = "exe"  # the Windows download: dao-modman.exe made with PyApp
UV_TOOL = "uv tool"
PIPX = "pipx"
OTHER = "other"


@dataclass(frozen=True)
class Install:
    kind: str  # one of the names above
    command: list[str] | None  # updates this installation; None: the user has to do it

    @property
    def how(self) -> str:
        """The update command as the user would type it."""
        if self.command is None:
            return f"pip install --upgrade {APP_NAME}"
        return subprocess.list2cmdline(self.command) if WINDOWS else shlex.join(self.command)


def installed() -> str:
    return APP_VERSION


def is_release(version: str) -> bool:
    """A released version; running from the source tree (x.y.z.devN+g...) is never checked."""
    return bool(_RELEASE.fullmatch(version)) and version != "0"


def newer(latest: str, current: str) -> bool:
    """Whether `latest` is a newer release than `current` (both like 1.2.3)."""
    if not (is_release(latest) and is_release(current)):
        return False

    def parts(version: str) -> tuple[int, ...]:
        numbers = [int(part) for part in version.split(".")]
        while numbers and numbers[-1] == 0:  # 1.0 == 1.0.0
            numbers.pop()
        return tuple(numbers)

    return parts(latest) > parts(current)


async def latest(transport: httpx2.AsyncBaseTransport | None = None) -> str | None:
    """The newest release on PyPI; None if PyPI cannot be asked."""
    try:
        async with httpx2.AsyncClient(
            transport=transport, timeout=10, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        ) as client:
            response = await client.get(PYPI_URL)
            response.raise_for_status()
            version = str(response.json()["info"]["version"])
    except (httpx2.HTTPError, ValueError, KeyError, TypeError):
        return None
    return version if is_release(version) else None


def install_kind() -> Install:
    """How this copy of dao-modman was installed, and so how it is updated."""
    exe = os.environ.get("PYAPP", "")
    if exe not in ("", "1") and Path(exe).is_file():  # PyApp passes its own location
        return Install(PYAPP, [exe, "self", "update"])
    parts = [part.casefold() for part in Path(sys.prefix).parts]
    if _follows(parts, "uv", "tools"):
        return Install(UV_TOOL, ["uv", "tool", "upgrade", APP_NAME])
    if _follows(parts, "pipx", "venvs"):
        return Install(PIPX, ["pipx", "upgrade", APP_NAME])
    return Install(OTHER, None)


def _follows(parts: list[str], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in zip(parts, parts[1:], strict=False))


def start_update(command: list[str]) -> None:
    """Run the update in a new window, once dao-modman has had time to quit."""
    if WINDOWS:
        line = f"timeout /t 2 >nul & {subprocess.list2cmdline(command)} & pause"
        # /s: cmd drops only the outer quotes, so quoted paths inside the line survive
        subprocess.Popen(f'cmd /s /c "{line}"', creationflags=terminal.CREATE_NEW_CONSOLE)
        return
    script = f"sleep 1; {shlex.join(command)}; echo; echo 'Press enter to close.'; read -r _"
    terminal.open_window(None, ["sh", "-c", script])
