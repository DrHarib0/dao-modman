from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import laa, paths
from dao_modman.core.config import Config
from dao_modman.core.paths import Dirs


def make_exe(path: Path, *, large_address_aware: bool, marker: bytes = b"") -> Path:
    """A minimal file with valid DOS and PE headers."""
    characteristics = 0x0102 | (0x20 if large_address_aware else 0)
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x80)
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, characteristics)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dos.ljust(0x80, b"\0") + coff + marker)
    return path


@pytest.fixture
def install_dir(tmp_path: Path) -> Path:
    path = tmp_path / "Dragon Age Ultimate Edition"
    make_exe(path / "bin_ship/DAOrigins.exe", large_address_aware=False, marker=b"original")
    return path


@pytest.fixture
def patched(tmp_path: Path) -> Path:
    return make_exe(tmp_path / "patched.exe", large_address_aware=True, marker=b"patched")


def test_is_laa(tmp_path: Path, install_dir: Path, patched: Path) -> None:
    assert laa.is_laa(patched)
    assert not laa.is_laa(install_dir / "bin_ship/DAOrigins.exe")
    (tmp_path / "text.exe").write_text("hello")
    with pytest.raises(laa.LaaError):
        laa.is_laa(tmp_path / "text.exe")
    with pytest.raises(laa.LaaError):
        laa.is_laa(tmp_path / "missing.exe")


def test_patch_and_restore(install_dir: Path, patched: Path) -> None:
    exe = install_dir / "bin_ship/DAOrigins.exe"
    assert laa.status(install_dir) == laa.UNPATCHED

    backup = laa.patch(install_dir, patched)
    assert laa.status(install_dir) == laa.PATCHED
    assert exe.read_bytes() == patched.read_bytes()
    assert backup.name == "DAOrigins.exe.pre-laa"
    assert backup.read_bytes().endswith(b"original")
    with pytest.raises(laa.LaaError, match="already"):
        laa.patch(install_dir, patched)

    assert laa.restore(install_dir) == exe
    assert exe.read_bytes().endswith(b"original")
    assert not backup.exists()
    with pytest.raises(laa.LaaError, match="no backup"):
        laa.restore(install_dir)


def test_patch_never_overwrites_an_existing_backup(install_dir: Path, patched: Path) -> None:
    backup = install_dir / "bin_ship/DAOrigins.exe.pre-laa"
    backup.write_bytes(b"older backup")
    laa.patch(install_dir, patched)
    assert backup.read_bytes() == b"older backup"


def test_patch_rejects_unsuitable_sources(tmp_path: Path, install_dir: Path) -> None:
    exe = install_dir / "bin_ship/DAOrigins.exe"
    before = exe.read_bytes()
    not_laa = make_exe(tmp_path / "plain.exe", large_address_aware=False)
    with pytest.raises(laa.LaaError, match="not Large Address Aware"):
        laa.patch(install_dir, not_laa)
    assert exe.read_bytes() == before
    assert not (install_dir / "bin_ship/DAOrigins.exe.pre-laa").exists()
    with pytest.raises(laa.LaaError, match="not found"):
        laa.patch(tmp_path / "nowhere", not_laa)


def test_patch_without_bundled_exe(install_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(laa, "bundled_exe", lambda: None)
    with pytest.raises(laa.LaaError, match="no LAA executable bundled"):
        laa.patch(install_dir)


def test_find_game_install_dir(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    install_dir = root / "steamapps/common/Dragon Age Ultimate Edition"
    install_dir.mkdir(parents=True)
    assert paths.find_game_install_dir([root]) is None
    (root / "steamapps/appmanifest_47810.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"47810"\n\t"installdir"\t\t"Dragon Age Ultimate Edition"\n}\n'
    )
    assert paths.find_game_install_dir([root]) == install_dir


def test_cli(
    tmp_path: Path,
    install_dir: Path,
    patched: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Config(dirs=Dirs(tmp_path / "d", tmp_path / "d/dl"), game_install_dir=install_dir)
    monkeypatch.setattr(cli, "load_config", lambda: config)

    assert cli.main(["laa", "status"]) == 0
    assert "unpatched" in capsys.readouterr().out
    assert cli.main(["laa", "patch", "--exe", str(patched)]) == 0
    assert laa.status(install_dir) == laa.PATCHED
    assert cli.main(["laa", "patch", "--exe", str(patched)]) == 1
    assert "already" in capsys.readouterr().err
    assert cli.main(["laa", "restore"]) == 0
    assert laa.status(install_dir) == laa.UNPATCHED

    config.game_install_dir = None
    assert cli.main(["laa", "status"]) == 1
