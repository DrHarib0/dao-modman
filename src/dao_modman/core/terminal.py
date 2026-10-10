"""Find a terminal emulator and build the command line that runs a program in it.

There is no single standard for "the default terminal", so several conventions are tried.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

# What goes between the terminal and the command to run; `-e` unless listed here.
SEPARATORS: dict[str, list[str]] = {
    "kitty": [],
    "foot": [],
    "xdg-terminal-exec": [],
    "gnome-terminal": ["--"],
    "ptyxis": ["--"],
    "wezterm": ["start", "--"],
}
KNOWN = ("konsole", "gnome-terminal", "kitty", "alacritty", "foot", "wezterm", "xterm")


class TerminalError(Exception):
    pass


def _kde_terminal() -> str | None:
    if not shutil.which("kreadconfig6"):
        return None
    command = ["kreadconfig6", "--file", "kdeglobals", "--group", "General"]
    try:
        result = subprocess.run(
            [*command, "--key", "TerminalApplication"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def resolve(configured: str | None) -> list[str]:
    """The terminal to use, as argv: config, $TERMINAL, xdg-terminal-exec, KDE, known ones."""
    candidates = [configured, os.environ.get("TERMINAL"), "xdg-terminal-exec", _kde_terminal()]
    for candidate in [*candidates, *KNOWN]:
        if not candidate:
            continue
        try:
            parts = shlex.split(candidate)
        except ValueError:
            continue
        if parts and shutil.which(parts[0]):
            return parts
    raise TerminalError("no terminal emulator found: set `terminal` in config.toml")


def command(configured: str | None, argv: list[str]) -> list[str]:
    """Command line that runs `argv` in a new terminal window."""
    parts = resolve(configured)
    return [*parts, *SEPARATORS.get(Path(parts[0]).name, ["-e"]), *argv]
