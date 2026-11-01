"""Run a program in a new terminal window.

On Linux there is no single standard for "the default terminal", so several conventions are
tried; Windows opens its default terminal by itself.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)  # defined on Windows only

# What goes between the terminal and the command to run; `-e` unless listed here.
SEPARATORS: dict[str, list[str]] = {
    "kitty": [],
    "foot": [],
    "xdg-terminal-exec": [],
    "gnome-terminal": ["--"],
    "ptyxis": ["--"],
    "wezterm": ["start", "--"],
}
# Tried in this order when nothing else names a terminal: those that take `-e`, then the rest.
KNOWN = ("konsole", "alacritty", "xterm", *SEPARATORS)


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


def open_window(configured: str | None, argv: list[str]) -> None:
    """Run `argv` in a new terminal window, detached from this process.

    On Windows a program started with a console of its own gets the user's default terminal
    (Windows Terminal on Windows 11); `terminal` in config.toml, e.g. "wt", goes first.
    """
    options: dict = {}
    if WINDOWS:  # the new console keeps its own input and output: the TUI runs in it
        if configured:
            argv = [*shlex.split(configured, posix=False), *argv]
        else:
            options["creationflags"] = CREATE_NEW_CONSOLE
    else:
        argv = command(configured, argv)
        quiet = subprocess.DEVNULL
        options.update(start_new_session=True, stdin=quiet, stdout=quiet, stderr=quiet)
    try:
        subprocess.Popen(argv, **options)
    except OSError as exc:
        raise TerminalError(f"cannot start {argv[0]}: {exc}") from exc
