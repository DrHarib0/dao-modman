"""Open a file with the program the desktop associates with it.

This is the one place that differs per operating system: Windows has `os.startfile`, macOS
the `open` command, Linux and other Unix desktops `xdg-open`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# File types that are run rather than opened: asked about first
EXECUTABLE_SUFFIXES = frozenset(
    {
        ".appimage", ".bat", ".cmd", ".com", ".desktop", ".exe", ".jar", ".js", ".lnk",
        ".msi", ".ps1", ".py", ".reg", ".scr", ".sh", ".vbs", ".wsf",
    }
)  # fmt: skip


class OpenerError(Exception):
    pass


def is_executable(path: Path) -> bool:
    """Whether opening the file would most likely run it."""
    return path.suffix.casefold() in EXECUTABLE_SUFFIXES


def open_file(path: Path) -> None:
    """Hand the file to the desktop's default program for it; returns at once."""
    if not path.is_file():
        raise OpenerError(f"not a file: {path}")
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
        return
    command = "open" if sys.platform == "darwin" else "xdg-open"
    program = shutil.which(command)
    if program is None:
        raise OpenerError(f"cannot open files: {command} not found")
    try:
        subprocess.Popen(  # detached: the program lives on after dao-modman
            [program, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise OpenerError(f"cannot open {path.name}: {exc}") from exc
