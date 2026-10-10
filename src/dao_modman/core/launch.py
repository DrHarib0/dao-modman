"""Start the game through Steam, so it runs with its Proton setup and launch options."""

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


def launch(app_id: str | None) -> None:
    if not app_id:
        raise LaunchError("game not found in Steam: set game_app_id in config.toml")
    try:
        subprocess.Popen(
            command(app_id),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise LaunchError(f"cannot start Steam: {exc}") from exc
