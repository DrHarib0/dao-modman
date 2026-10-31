"""Start the game: with the command from config.toml, through Steam, or (Windows) directly.

Through Steam the game runs with its Proton setup and launch options; other installs
(Lutris, plain Wine) name their own command. On Windows a game that is not from Steam is
started by running its executable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .laa import EXE_REL
from .paths import resolve_case

WINDOWS = sys.platform == "win32"


class LaunchError(Exception):
    pass


def command(app_id: str) -> list[str]:
    if shutil.which("steam"):
        return ["steam", "-applaunch", app_id]
    if shutil.which("xdg-open"):
        return ["xdg-open", f"steam://rungameid/{app_id}"]
    raise LaunchError("neither steam nor xdg-open found")


def _open_url(url: str) -> None:
    """Hand a link to Windows, which passes steam:// links to Steam."""
    try:
        os.startfile(url)  # type: ignore[attr-defined]  # Windows only
    except OSError as exc:
        raise LaunchError(f"cannot open {url} (is Steam installed?): {exc}") from exc


def _spawn(argv: list[str], cwd: Path | None = None) -> None:
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise LaunchError(f"cannot start {argv[0]}: {exc}") from exc


def launch(
    app_id: str | None,
    configured: list[str] | None = None,
    install_dir: Path | None = None,
) -> None:
    if configured:
        _spawn(configured)
    elif app_id and WINDOWS:
        _open_url(f"steam://rungameid/{app_id}")
    elif app_id:
        _spawn(command(app_id))
    elif install_dir and WINDOWS:
        exe = resolve_case(install_dir, EXE_REL)
        if not exe.is_file():
            raise LaunchError(f"game executable not found: {exe}")
        _spawn([str(exe)], cwd=exe.parent)  # the game finds its files from bin_ship
    elif WINDOWS:
        raise LaunchError("game not found: set game_install_dir (or launch_command) in config.toml")
    else:
        raise LaunchError(
            "game not found in Steam: set launch_command (or game_app_id) in config.toml"
        )
