"""Runtime configuration: optional config.toml plus the Nexus API token."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from . import ipc, paths
from .ignore import DEFAULT_PATTERNS
from .metadata import write_atomic
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
    # .gitignore patterns for files inside mods that are never deployed
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_PATTERNS))
    language: str = "en-us"  # which talk tables item names are read from
    max_texture_size: int = 4096  # longest side of a deployed texture in pixels; 0: no limit
    config_file: Path | None = None  # where settings changed in the app are written


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


def set_option(config_file: Path, key: str, value: int) -> None:
    """Set a top-level number in config.toml, leaving the rest of the file as it is.

    The file is the user's, comments included, so it is edited as text: the key's line is
    replaced, or added above the first table if there is none yet.
    """
    text = config_file.read_text(encoding="utf-8") if config_file.is_file() else ""
    lines = text.splitlines()
    tables = [index for index, line in enumerate(lines) if line.startswith("[")]
    top = lines[: tables[0]] if tables else lines
    own = [index for index, line in enumerate(top) if re.match(rf"{re.escape(key)}\s*=", line)]
    if own:
        lines[own[0]] = f"{key} = {value}"
    else:
        lines.insert(len(top), f"{key} = {value}")
    new = "\n".join(lines) + "\n"
    if tomllib.loads(new).get(key) != value:  # e.g. the key sits inside a multi-line value
        raise ValueError(f"cannot set {key} in {config_file}: edit the file by hand")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(config_file, new.encode())


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
        ignore=[str(p) for p in raw["ignore"]] if "ignore" in raw else list(DEFAULT_PATTERNS),
        language=str(raw.get("language") or "en-us"),
        max_texture_size=int(raw.get("max_texture_size", 4096)),
        config_file=config_file,
    )
