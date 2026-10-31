"""Windows only: the registry, known folders, the Python executables, message boxes.

On other systems the lookups find nothing (None, an empty list) and the changes do nothing,
so callers need no checks.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def registry_value(subkey: str, name: str = "", *, machine: bool = True) -> str | None:
    """A string value from HKEY_CURRENT_USER, else HKEY_LOCAL_MACHINE; "" is the default value.

    `machine=False` reads HKEY_CURRENT_USER only.
    """
    if os.name != "nt":
        return None
    import winreg

    roots = [winreg.HKEY_CURRENT_USER] + ([winreg.HKEY_LOCAL_MACHINE] if machine else [])
    for root in roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and value:
            return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value
    return None


def set_user_registry(subkey: str, values: dict[str, str]) -> bool:
    """Write string values ("" is the default value) to a key under HKEY_CURRENT_USER."""
    if os.name != "nt":
        return False
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_WRITE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    return True


def delete_user_registry(subkey: str) -> bool:
    """Delete a key under HKEY_CURRENT_USER with everything below it; False if it is not there."""
    if os.name != "nt":
        return False
    import winreg

    def delete(path: str) -> None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:  # no subkeys left
                    break
                delete(rf"{path}\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)

    try:
        delete(subkey)
    except FileNotFoundError:
        return False
    return True


def console_python() -> str:
    """python.exe: runs with a console window, as the TUI needs."""
    return _python("python.exe")


def windowless_python() -> str:
    """pythonw.exe: runs without a console window, for the nxm handler the browser starts."""
    return _python("pythonw.exe")


def _python(name: str) -> str:
    sibling = Path(sys.executable).with_name(name)
    return str(sibling) if os.name == "nt" and sibling.is_file() else sys.executable


def message_box(title: str, text: str) -> None:
    """Show a message where no console is, e.g. from the nxm handler; waits for OK."""
    if os.name != "nt":
        return
    import ctypes

    icon_error = 0x10  # MB_ICONERROR
    ctypes.windll.user32.MessageBoxW(None, text, title, icon_error)  # type: ignore[attr-defined]


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


def app_data() -> Path | None:
    """%APPDATA%: settings that roam with the user, like config.toml and the token."""
    return _env_folder("APPDATA")


def local_app_data() -> Path | None:
    """%LOCALAPPDATA%: large data that stays on this machine, like the mod library."""
    return _env_folder("LOCALAPPDATA")


def _env_folder(name: str) -> Path | None:
    value = os.environ.get(name) if os.name == "nt" else None
    return Path(value) if value else None


def steam_folders() -> list[Path]:
    """Where Steam is installed: per user, machine-wide (as 32-bit program), the default."""
    folders = [
        registry_value(r"Software\Valve\Steam", "SteamPath"),
        registry_value(r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        registry_value(r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    paths = [Path(folder) for folder in folders if folder]
    if os.name == "nt" and (x86 := os.environ.get("PROGRAMFILES(X86)")):
        paths.append(Path(x86) / "Steam")
    return paths


# FOLDERID_Documents, see KNOWNFOLDERID in the Windows API documentation
_DOCUMENTS = (0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))


def documents_dir() -> Path | None:
    """The user's Documents folder, wherever it is (OneDrive or the user may have moved it)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    data1, data2, data3, data4 = _DOCUMENTS
    folder_id = GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
    path = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32  # type: ignore[attr-defined]
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path)):
            return None  # an error code: no such folder
        return Path(path.value) if path.value else None
    finally:
        ole32.CoTaskMemFree(path)


def find_program(exe: str, folders: Iterable[Path | str | None]) -> Path | None:
    """The first `<folder>/<exe>` that exists."""
    for folder in folders:
        if folder and (path := Path(folder) / exe).is_file():
            return path
    return None
