from __future__ import annotations

from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import console
from dao_modman.core.config import Config
from dao_modman.core.library import Library
from dao_modman.core.models import NexusInfo, PathOp
from dao_modman.core.paths import Dirs

from .conftest import make_zip

# The relevant part of the Steam release's launcher file, CRLF line endings included.
LAUNCHER = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n<launcher>\r\n'
    '        <condition name="CheckInstall1" type="FileExist" '
    'file="${BINARIES_DIR}\\DAOrigins.exe">\r\n'
    '            <true action="execute" file="${BINARIES_DIR}\\DAOrigins.exe" '
    'path="${BINARIES_DIR}" autoquit="true" />\r\n'
    '            <false action="execute" file="${BINARIES_DIR}\\DAOriginsConfig.exe" '
    'arguments="-p -c &quot;${BINARIES_DIR}\\DAOrigins.exe&quot;" autoquit="true" />\r\n'
    "        </condition>\r\n</launcher>\r\n"
)
KEYBINDINGS = "[Key Bindings]\r\nQuickSave_0=Keyboard::Button_F5\r\nOpenConsole_0={key}\r\n"


def make_game(tmp_path: Path, user_dir: Path, key: str = "Keyboard::Button_Grave") -> Path:
    install_dir = tmp_path / "game"
    (install_dir / "data").mkdir(parents=True, exist_ok=True)
    (install_dir / "data/DAOriginsLauncher.xml").write_bytes(LAUNCHER.encode())
    (user_dir / "Settings").mkdir(parents=True, exist_ok=True)
    (user_dir / "Settings/KeyBindings.ini").write_bytes(KEYBINDINGS.format(key=key).encode())
    return install_dir


def launcher_text(install_dir: Path) -> str:
    return (install_dir / "data/DAOriginsLauncher.xml").read_bytes().decode()


def test_enable_and_disable_change_only_the_launch_entry(tmp_path: Path, user_dir: Path) -> None:
    install_dir = make_game(tmp_path, user_dir)
    assert not console.is_enabled(install_dir)

    assert console.enable(install_dir)
    assert console.is_enabled(install_dir)
    expected = LAUNCHER.replace(
        'path="${BINARIES_DIR}" autoquit="true" />',
        'path="${BINARIES_DIR}" autoquit="true" arguments="-enabledeveloperconsole" />',
    )
    assert launcher_text(install_dir) == expected
    assert not console.enable(install_dir)  # already on: nothing to do
    backup = install_dir / "data/DAOriginsLauncher.xml.pre-console"
    assert backup.read_bytes() == LAUNCHER.encode()

    assert console.disable(install_dir)
    assert launcher_text(install_dir) == LAUNCHER
    assert not console.disable(install_dir)


def test_existing_arguments_are_kept(tmp_path: Path, user_dir: Path) -> None:
    install_dir = make_game(tmp_path, user_dir)
    path = install_dir / "data/DAOriginsLauncher.xml"
    path.write_bytes(
        LAUNCHER.replace('autoquit="true" />', 'arguments="-foo" autoquit="true" />', 1).encode()
    )
    console.enable(install_dir)
    assert 'arguments="-foo -enabledeveloperconsole"' in launcher_text(install_dir)
    console.disable(install_dir)
    assert 'arguments="-foo"' in launcher_text(install_dir)
    assert "-enabledeveloperconsole" not in launcher_text(install_dir)


def test_unusable_launcher(tmp_path: Path) -> None:
    with pytest.raises(console.ConsoleError, match="cannot read"):
        console.enable(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/DAOriginsLauncher.xml").write_text("<launcher />")
    with pytest.raises(console.ConsoleError, match="not found"):
        console.is_enabled(tmp_path)


def test_ensure_key(tmp_path: Path, user_dir: Path) -> None:
    make_game(tmp_path, user_dir, key="Keyboard::Button_F11")
    bindings = user_dir / "Settings/KeyBindings.ini"
    before = bindings.read_bytes()
    assert console.ensure_key(user_dir) == "Keyboard::Button_F11"
    assert bindings.read_bytes() == before  # an assigned key is left alone

    make_game(tmp_path, user_dir, key="(UNASSIGNED)")
    assert console.ensure_key(user_dir) == console.DEFAULT_KEY
    assert bindings.read_bytes() == KEYBINDINGS.format(key=console.DEFAULT_KEY).encode()
    assert console.key_label(console.DEFAULT_KEY) == "Grave"

    assert console.ensure_key(tmp_path / "missing") is None
    assert "key bindings" in console.key_label(None)


def test_fonts_installed_and_placement(tmp_path: Path, user_dir: Path, library: Library) -> None:
    assert not console.fonts_installed(user_dir, library)
    assert not console.fonts_installed(None, library)

    other = library.import_archive(make_zip(tmp_path / "other.zip", {"fonts.erf": "x"}))
    assert not console.place_fonts(library, other)  # not the fonts mod: left alone

    mod = library.import_archive(
        make_zip(tmp_path / "fix.zip", {"Angel/Fonts.erf": "erf", "readme.txt": "r"}),
        nexus=NexusInfo("dragonage", console.FONTS_MOD_ID, 5571),
    )
    assert console.fonts_installed(user_dir, library)
    assert console.place_fonts(library, mod)
    assert mod.ops == [
        PathOp("rename", "packages/core/override/fix/Angel/Fonts.erf", console.FONTS_REL)
    ]
    assert console.FONTS_REL in Library(library.dirs).targets(mod)

    library.remove(mod.id)
    assert not console.fonts_installed(user_dir, library)
    (user_dir / "Packages/Core/Data").mkdir(parents=True)
    (user_dir / "Packages/Core/Data/fonts.erf").write_bytes(b"erf")
    assert console.fonts_installed(user_dir, library)


def test_cli(
    tmp_path: Path,
    user_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_dir = make_game(tmp_path, user_dir)
    config = Config(
        dirs=Dirs(tmp_path / "d", tmp_path / "d/dl"),
        game_user_dir=user_dir,
        game_install_dir=install_dir,
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)

    assert cli.main(["console", "status"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert cli.main(["console", "enable"]) == 0
    out = capsys.readouterr().out
    assert "Grave" in out and console.FONTS_MOD_URL in out
    assert console.is_enabled(install_dir)
    assert cli.main(["console", "disable"]) == 0
    assert not console.is_enabled(install_dir)

    config.game_install_dir = None
    assert cli.main(["console", "status"]) == 1


def test_install_nxm_handler_warns_about_unreachable_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/xdg-mime")
    monkeypatch.setattr(cli.subprocess, "run", lambda command, **_: calls.append(command))

    assert cli.main(["install-nxm-handler"]) == 0
    desktop = tmp_path / ".local/share/applications/dao-modman-nxm.desktop"
    entry = desktop.read_text()
    assert "x-scheme-handler/nxm" in entry
    assert "Terminal=false" in entry  # the handler opens a terminal itself when needed
    assert entry.count("-m dao_modman nxm %u") == 1
    assert calls == [["xdg-mime", "default", "dao-modman-nxm.desktop", "x-scheme-handler/nxm"]]
    assert "will not find your API token" in capsys.readouterr().out

    config_dir = tmp_path / "config/dao-modman"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("NEXUS_API_TOKEN=abc\n")
    assert cli.main(["install-nxm-handler"]) == 0
    assert "will not find" not in capsys.readouterr().out
