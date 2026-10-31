"""Windows only: where programs are installed, read from the registry.

On other systems the lookups find nothing (None, an empty list), so callers need no checks.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def registry_value(subkey: str, name: str = "") -> str | None:
    """A string value from HKEY_CURRENT_USER, else HKEY_LOCAL_MACHINE; "" is the default value."""
    if os.name != "nt":
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and value:
            return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value
    return None


def app_folder(exe: str) -> Path | None:
    """The folder of a program registered under App Paths, e.g. WinRAR.exe."""
    if folder := registry_value(rf"{APP_PATHS}\{exe}", "Path"):
        return Path(folder.rstrip(";"))
    if path := registry_value(rf"{APP_PATHS}\{exe}"):
        return Path(path.strip('"')).parent
    return None


def program_files() -> list[Path]:
    """The Program Files folders, 64-bit first."""
    names = ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
    folders = [Path(value) for name in names if (value := os.environ.get(name))]
    return list(dict.fromkeys(folders))


def find_program(exe: str, folders: Iterable[Path | str | None]) -> Path | None:
    """The first `<folder>/<exe>` that exists."""
    for folder in folders:
        if folder and (path := Path(folder) / exe).is_file():
            return path
    return None
