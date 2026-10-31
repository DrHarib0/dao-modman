"""Runtime configuration: optional config.toml plus the Nexus API token."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import ipc, paths, textures
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
    unrar_command: list[str] | None = None  # extracts .rar when no known tool is found
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


def save_token(config_dir: Path, token: str) -> Path:
    """Store the Nexus API token in `<config dir>/.nexus-token`; an empty one deletes the file.

    Created readable by the user only, on POSIX; Windows keeps a user's AppData private.
    """
    path = config_dir / TOKEN_FILE
    token = token.strip()
    if not token:
        path.unlink(missing_ok=True)
        return path
    config_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    os.replace(tmp, path)
    return path


Value = int | str | list[str]


def _toml_value(value: Value) -> str:
    """`value` written as TOML; strings as literal strings, so Windows paths stay readable."""
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, str):
        if "'" in value or "\n" in value:
            return json.dumps(value)  # a JSON string is a valid TOML basic string
        return f"'{value}'"
    return str(value)


def load_raw(config_file: Path | None) -> dict:
    """What config.toml itself sets: missing or broken files count as empty."""
    if config_file is None:
        return {}
    try:
        with config_file.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def set_option(config_file: Path, key: str, value: Value | None) -> None:
    """Set a top-level key in config.toml, or remove it (None), leaving the rest as it is.

    The file is the user's, comments included, so it is edited as text: the key's lines are
    replaced (a value may span several, like an `ignore` list), or a line is added above the
    first table.
    """
    text = config_file.read_text(encoding="utf-8") if config_file.is_file() else ""
    lines = text.splitlines()
    tables = [index for index, line in enumerate(lines) if line.startswith("[")]
    top = tables[0] if tables else len(lines)  # top-level keys come before the first table
    pattern = re.compile(rf"{re.escape(key)}\s*=")
    start = next((i for i in range(top) if pattern.match(lines[i])), None)
    new_lines = [] if value is None else [f"{key} = {_toml_value(value)}"]
    if start is None:
        lines[top:top] = new_lines
    else:
        end = start + 1
        while not _parses_with(lines[start:end], key):
            if end >= top:
                raise ValueError(f"cannot change {key} in {config_file}: edit the file by hand")
            end += 1
        lines[start:end] = new_lines
    new = "\n".join(lines) + "\n" if lines else ""
    if tomllib.loads(new).get(key) != value:  # e.g. the key sits inside a multi-line value
        raise ValueError(f"cannot change {key} in {config_file}: edit the file by hand")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(config_file, new.encode())


def _parses_with(lines: list[str], key: str) -> bool:
    try:
        return key in tomllib.loads("\n".join(lines))
    except tomllib.TOMLDecodeError:
        return False


@dataclass(frozen=True)
class Setting:
    """A config.toml key as the settings screen offers it."""

    key: str
    label: str
    kind: str  # one of the KIND_ names below
    help: str
    choices: tuple[tuple[Value, str], ...] = ()  # for KIND_CHOICE: (value, label)


KIND_INSTALL_DIR = "install_dir"  # the game's install folder
KIND_USER_DIR = "user_dir"  # the game's user folder
KIND_PATH = "path"
KIND_TEXT = "text"
KIND_COMMAND = "command"  # a program and its arguments: a list in config.toml
KIND_CHOICE = "choice"
KIND_PATTERNS = "patterns"  # a list of .gitignore patterns

LANGUAGES = (
    ("en-us", "English"),
    ("fr-fr", "French"),
    ("de-de", "German"),
    ("it-it", "Italian"),
    ("es-es", "Spanish"),
    ("pl-pl", "Polish"),
    ("ru-ru", "Russian"),
    ("cs-cz", "Czech"),
    ("hu-hu", "Hungarian"),
)

SETTINGS = (
    Setting(
        "game_install_dir",
        "Game install folder",
        KIND_INSTALL_DIR,
        "The folder with bin_ship/DAOrigins.exe; Steam installs are found automatically.",
    ),
    Setting(
        "game_user_dir",
        "Game user folder",
        KIND_USER_DIR,
        "Documents/BioWare/Dragon Age: saves, settings and mods; the game creates it.",
    ),
    Setting(
        "launch_command",
        "Launch command",
        KIND_COMMAND,
        "What `l` runs instead of Steam, e.g. lutris lutris:rungame/dragon-age-origins.",
    ),
    Setting(
        "game_app_id",
        "Steam app id",
        KIND_TEXT,
        "17450 (Origins) or 47810 (Ultimate Edition); found automatically for Steam installs.",
    ),
    Setting(
        "max_texture_size",
        "Texture size limit",
        KIND_CHOICE,
        "Larger textures are reduced when applying, so a modded game fits into memory.",
        tuple(textures.LIMITS.items()),
    ),
    Setting(
        "language",
        "Language of item names",
        KIND_CHOICE,
        "Which talk tables the item lists read names from.",
        tuple((code, f"{name} ({code})") for code, name in LANGUAGES),
    ),
    Setting(
        "ignore",
        "Ignored files",
        KIND_PATTERNS,
        "Files inside mods that are never deployed, as .gitignore patterns.",
    ),
    Setting(
        "unrar_command",
        "Command for .rar archives",
        KIND_COMMAND,
        "Only needed if no unrar, 7-Zip or WinRAR is found; {archive} and {dest} are filled in.",
    ),
    Setting(
        "terminal",
        "Terminal for nxm:// links",
        KIND_TEXT,
        "Opens dao-modman when a link comes in while it is not running, e.g. kitty or wt.",
    ),
    Setting(
        "data_dir",
        "Library folder",
        KIND_PATH,
        "Where the mods are kept; takes effect after a restart.",
    ),
    Setting(
        "downloads_dir",
        "Downloads folder",
        KIND_PATH,
        "Where downloaded archives are kept; takes effect after a restart.",
    ),
)


def validate(setting: Setting, value: Value) -> str | None:
    """Why `value` cannot be used for the setting, or None if it can."""
    if setting.kind == KIND_INSTALL_DIR:
        exe = paths.resolve_case(Path(str(value)).expanduser(), "bin_ship/DAOrigins.exe")
        if not exe.is_file():
            return f"{value} is not the game's install folder: there is no {exe}"
    elif setting.kind == KIND_USER_DIR:
        if not Path(str(value)).expanduser().is_dir():
            return f"{value} does not exist: start the game once, it creates the folder"
    elif setting.kind == KIND_PATH:
        if Path(str(value)).expanduser().is_file():
            return f"{value} is a file, not a folder"
    elif setting.kind == KIND_COMMAND:
        if not isinstance(value, list) or not value or not all(value):
            return "give the program and its arguments"
        if setting.key == "unrar_command" and not any("{archive}" in part for part in value):
            return "the command needs {archive} where the archive's path goes"
    elif setting.key == "game_app_id" and not str(value).isdigit():
        return "a Steam app id is a number, e.g. 47810"
    elif setting.kind == KIND_CHOICE and value not in [choice for choice, _ in setting.choices]:
        return f"{value} is not one of the choices"
    return None


def load_config(config_file: Path | None = None) -> Config:
    """The configuration from config.toml (default: the one in the config dir) and the token."""
    config_file = config_file or paths.config_dir() / "config.toml"
    config_dir = config_file.parent
    raw: dict = {}
    if config_file.is_file():
        with config_file.open("rb") as fh:
            try:
                raw = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{config_file}: {exc}") from exc

    def path_option(key: str) -> Path | None:
        value = raw.get(key)
        return Path(value).expanduser() if value else None

    def command_option(key: str, example: str) -> list[str] | None:
        command = raw.get(key)
        if command is not None and (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ConfigError(f"{config_file}: {key} must be a list of arguments, e.g. {example}")
        return command

    command = command_option("launch_command", '["lutris", "lutris:rungame/dragon-age-origins"]')
    unrar_example = """['C:/Tools/unrar.exe', 'x', '-y', '{archive}', '{dest}/']"""
    unrar_command = command_option("unrar_command", unrar_example)
    if unrar_command and not any("{archive}" in part for part in unrar_command):
        raise ConfigError(f"{config_file}: unrar_command needs {{archive}}, e.g. {unrar_example}")

    data = path_option("data_dir") or paths.data_dir()
    dirs = Dirs(data=data, downloads=path_option("downloads_dir") or data / "downloads")
    return Config(
        dirs=dirs,
        game_user_dir=path_option("game_user_dir") or paths.find_game_user_dir(),
        game_install_dir=path_option("game_install_dir") or paths.find_game_install_dir(),
        game_app_id=str(raw.get("game_app_id") or "") or paths.find_game_app_id(),
        launch_command=command,
        unrar_command=unrar_command,
        nexus_token=load_token(config_dir),
        token_warning=token_warning(config_dir),
        terminal=str(raw["terminal"]) if raw.get("terminal") else None,
        runtime_dir=ipc.runtime_dir(),
        ignore=[str(p) for p in raw["ignore"]] if "ignore" in raw else list(DEFAULT_PATTERNS),
        language=str(raw.get("language") or "en-us"),
        max_texture_size=int(raw.get("max_texture_size", 4096)),
        config_file=config_file,
    )
