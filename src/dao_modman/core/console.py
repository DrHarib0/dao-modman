"""Enable the game's developer console.

The Steam launcher starts the game through `data/DAOriginsLauncher.xml`, so the
`-enabledeveloperconsole` switch has to be added to the launch entry there. The console is
opened with the key bound to `OpenConsole_0` in the user dir's `Settings/KeyBindings.ini`.
What is typed stays invisible unless the game has a `packages/core/data/fonts.erf`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .library import Library
from .metadata import write_atomic
from .models import OP_RENAME, Mod, PathOp
from .paths import resolve_case

LAUNCHER_REL = "data/DAOriginsLauncher.xml"
KEYBINDINGS_REL = "Settings/KeyBindings.ini"
FONTS_REL = "packages/core/data/fonts.erf"
BACKUP_SUFFIX = ".pre-console"
SWITCH = "-enabledeveloperconsole"
DEFAULT_KEY = "Keyboard::Button_Grave"
UNASSIGNED = "(UNASSIGNED)"

# "Make CONSOLE commands visible": ships the fonts.erf that makes console input readable
FONTS_MOD_ID = 1110
FONTS_MOD_URL = f"https://www.nexusmods.com/dragonage/mods/{FONTS_MOD_ID}?tab=files"

# The element that starts the game: <true action="execute" file="...\DAOrigins.exe" ... />
_LAUNCH = re.compile(rb'<true\s+action="execute"\s+file="[^"]*\\DAOrigins\.exe"[^>]*?/>', re.I)
_ARGUMENTS = re.compile(rb'\s+arguments="([^"]*)"')
_BINDING = re.compile(rb"^(OpenConsole_0=)(.*?)(\r?)$", re.M)


class ConsoleError(Exception):
    pass


def _launcher(install_dir: Path) -> tuple[Path, bytes, re.Match[bytes]]:
    path = resolve_case(install_dir, LAUNCHER_REL)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConsoleError(f"cannot read {path}: {exc}") from exc
    match = _LAUNCH.search(data)
    if match is None:
        raise ConsoleError(f"{path}: entry that launches DAOrigins.exe not found")
    return path, data, match


def _switches(element: bytes) -> list[bytes]:
    arguments = _ARGUMENTS.search(element)
    return arguments.group(1).split() if arguments else []


def is_enabled(install_dir: Path) -> bool:
    _path, _data, match = _launcher(install_dir)
    return SWITCH.encode() in _switches(match.group(0))


def _set_switch(install_dir: Path, enabled: bool) -> bool:
    """Add or remove the switch, touching nothing else in the file. True if it changed."""
    path, data, match = _launcher(install_dir)
    element = match.group(0)
    switches = [switch for switch in _switches(element) if switch != SWITCH.encode()]
    if enabled:
        switches.append(SWITCH.encode())
    bare = _ARGUMENTS.sub(b"", element)
    if switches:
        attribute = b' arguments="' + b" ".join(switches) + b'"'
        bare = re.sub(rb"\s*/>$", lambda _: attribute + b" />", bare)
    if bare == element:
        return False
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        if not backup.exists():
            shutil.copy2(path, backup)
        write_atomic(path, data[: match.start()] + bare + data[match.end() :])
    except OSError as exc:
        raise ConsoleError(f"cannot write {path}: {exc}") from exc
    return True


def enable(install_dir: Path) -> bool:
    return _set_switch(install_dir, True)


def disable(install_dir: Path) -> bool:
    return _set_switch(install_dir, False)


def ensure_key(user_dir: Path) -> str | None:
    """The key that opens the console, binding the default one if none is assigned.

    Returns None when the game has not written its key bindings yet.
    """
    path = resolve_case(user_dir, KEYBINDINGS_REL)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    match = _BINDING.search(data)
    if match is None:
        return None
    key = match.group(2).decode("ascii", "replace").strip()
    if key and key != UNASSIGNED:
        return key
    replacement = match.group(1) + DEFAULT_KEY.encode() + match.group(3)
    try:
        write_atomic(path, data[: match.start()] + replacement + data[match.end() :])
    except OSError as exc:
        raise ConsoleError(f"cannot write {path}: {exc}") from exc
    return DEFAULT_KEY


def key_label(key: str | None) -> str:
    return key.rpartition("Button_")[2] if key else "the key set in the game's key bindings"


def fonts_installed(user_dir: Path | None, library: Library) -> bool:
    """True if console text will be visible: fonts.erf is in place or its mod is installed."""
    if any(mod.nexus and mod.nexus.mod_id == FONTS_MOD_ID for mod in library.mods.values()):
        return True
    return user_dir is not None and resolve_case(user_dir, FONTS_REL).is_file()


def place_fonts(library: Library, mod: Mod) -> bool:
    """Route the fonts mod's fonts.erf to `packages/core/data/`, where the game needs it."""
    if not mod.nexus or mod.nexus.mod_id != FONTS_MOD_ID:
        return False
    for target in library.targets(mod):
        if target.casefold().endswith("/fonts.erf") and target.casefold() != FONTS_REL:
            mod.ops.append(PathOp(OP_RENAME, target, FONTS_REL))
            library.save_mod(mod)
            return True
    return False
