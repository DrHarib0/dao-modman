"""Runtime configuration: optional config.toml plus the Nexus API token."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import ipc, paths
from .ignore import DEFAULT_PATTERNS
from .metadata import write_atomic
from .paths import Dirs

TOKEN_VAR = "NEXUS_API_TOKEN"
TOKEN_FILE = ".nexus-token"  # in the config dir: the token alone


class ConfigError(Exception):
    pass


@dataclass
class Config:
    dirs: Dirs
    game_user_dir: Path | None = None
    game_install_dir: Path | None = None
    game_app_id: str | None = None  # Steam app id, for launching the game
    launch_command: list[str] | None = None  # starts the game instead of Steam, e.g. Lutris
    nexus_token: str | None = None
    token_warning: str | None = None  # the token file is readable by other users
    terminal: str | None = None  # terminal emulator for windows opened by the nxm handler
    runtime_dir: Path | None = None  # lock and socket of the running instance; None: no IPC
    # .gitignore patterns for files inside mods that are never deployed
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_PATTERNS))
    language: str = "en-us"  # which talk tables item names are read from
    max_texture_size: int = 4096  # longest side of a deployed texture in pixels; 0: no limit
    config_file: Path | None = None  # where settings changed in the app are written


def load_token(config_dir: Path, *, environment: bool = True) -> str | None:
    """The Nexus API token: from the environment, else from `<config dir>/.nexus-token`.

    Never from the current directory: what is found must not depend on where dao-modman is
    started (the nxm handler starts it wherever the browser runs).
    """
    if environment and (token := os.environ.get(TOKEN_VAR, "").strip()):
        return token
    try:
        token = (config_dir / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token or None


def token_warning(config_dir: Path) -> str | None:
    """A warning if the token file can be read by other users (POSIX permissions only)."""
    path = config_dir / TOKEN_FILE
    if os.name == "nt":
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if mode & 0o077:
        return f"{path} can be read by other users: chmod 600 '{path}'"
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
            try:
                raw = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{config_file}: {exc}") from exc

    def path_option(key: str) -> Path | None:
        value = raw.get(key)
        return Path(value).expanduser() if value else None

    command = raw.get("launch_command")
    if command is not None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ConfigError(
            f"{config_file}: launch_command must be a list of arguments, "
            'e.g. ["lutris", "lutris:rungame/dragon-age-origins"]'
        )

    data = path_option("data_dir") or paths.data_dir()
    dirs = Dirs(data=data, downloads=path_option("downloads_dir") or data / "downloads")
    return Config(
        dirs=dirs,
        game_user_dir=path_option("game_user_dir") or paths.find_game_user_dir(),
        game_install_dir=path_option("game_install_dir") or paths.find_game_install_dir(),
        game_app_id=str(raw.get("game_app_id") or "") or paths.find_game_app_id(),
        launch_command=command,
        nexus_token=load_token(config_dir),
        token_warning=token_warning(config_dir),
        terminal=str(raw["terminal"]) if raw.get("terminal") else None,
        runtime_dir=ipc.runtime_dir(),
        ignore=[str(p) for p in raw["ignore"]] if "ignore" in raw else list(DEFAULT_PATTERNS),
        language=str(raw.get("language") or "en-us"),
        max_texture_size=int(raw.get("max_texture_size", 4096)),
        config_file=config_file,
    )
