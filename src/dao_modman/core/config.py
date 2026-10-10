"""Runtime configuration: optional config.toml plus the Nexus API token."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from . import ipc, paths
from .paths import Dirs

TOKEN_VAR = "NEXUS_API_TOKEN"


@dataclass
class Config:
    dirs: Dirs
    game_user_dir: Path | None = None
    game_install_dir: Path | None = None
    game_app_id: str | None = None  # Steam app id, for launching the game
    nexus_token: str | None = None
    terminal: str | None = None  # terminal emulator for windows opened by the nxm handler
    runtime_dir: Path | None = None  # lock and socket of the running instance; None: no IPC


def load_token(config_dir: Path, cwd: Path | None = None) -> str | None:
    """Environment first, then `./.env`, then `<config dir>/.env`."""
    token = os.environ.get(TOKEN_VAR)
    if token:
        return token.strip()
    for env_file in ((cwd or Path.cwd()) / ".env", config_dir / ".env"):
        if env_file.is_file():
            token = dotenv_values(env_file).get(TOKEN_VAR)
            if token:
                return token.strip()
    return None


def load_config() -> Config:
    config_dir = paths.config_dir()
    raw: dict = {}
    config_file = config_dir / "config.toml"
    if config_file.is_file():
        with config_file.open("rb") as fh:
            raw = tomllib.load(fh)

    def path_option(key: str) -> Path | None:
        value = raw.get(key)
        return Path(value).expanduser() if value else None

    data = path_option("data_dir") or paths.data_dir()
    dirs = Dirs(data=data, downloads=path_option("downloads_dir") or data / "downloads")
    return Config(
        dirs=dirs,
        game_user_dir=path_option("game_user_dir") or paths.find_game_user_dir(),
        game_install_dir=path_option("game_install_dir") or paths.find_game_install_dir(),
        game_app_id=str(raw.get("game_app_id") or "") or paths.find_game_app_id(),
        nexus_token=load_token(config_dir),
        terminal=str(raw["terminal"]) if raw.get("terminal") else None,
        runtime_dir=ipc.runtime_dir(),
    )
