"""Start the game: with the command from config.toml, or through Steam.

Through Steam the game runs with its Proton setup and launch options; other installs
(Lutris, plain Wine) name their own command.
"""

from __future__ import annotations

import shutil
import subprocess


class LaunchError(Exception):
    pass


def command(app_id: str) -> list[str]:
    if shutil.which("steam"):
        return ["steam", "-applaunch", app_id]
    if shutil.which("xdg-open"):
        return ["xdg-open", f"steam://rungameid/{app_id}"]
    raise LaunchError("neither steam nor xdg-open found")


def launch(app_id: str | None, configured: list[str] | None = None) -> None:
    if configured:
        argv = configured
    elif app_id:
        argv = command(app_id)
    else:
        raise LaunchError(
            "game not found in Steam: set launch_command (or game_app_id) in config.toml"
        )
    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise LaunchError(f"cannot start {argv[0]}: {exc}") from exc
