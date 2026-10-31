from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import laa, paths
from dao_modman.core.config import Config
from dao_modman.core.paths import Dirs

CHECKSUM_AT = 0x80 + 24 + 64  # DOS header, PE signature and COFF header, then optional header


def make_exe(
    path: Path,
    *,
    large_address_aware: bool,
    marker: bytes = b"",
    sections: tuple[str, ...] = (".text", ".rdata"),
) -> Path:
    """A minimal file with valid DOS and PE headers and a section table."""
    characteristics = 0x0102 | (0x20 if large_address_aware else 0)
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x80)
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C, len(sections), 0, 0, 0, 0xE0, characteristics)
    optional = bytearray(0xE0)
    struct.pack_into("<HI", optional, 0, 0x10B, 0)  # PE32
    struct.pack_into("<I", optional, 64, 0x1234)  # a checksum that will not match
    table = b"".join(name.encode().ljust(40, b"\0") for name in sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dos.ljust(0x80, b"\0") + coff + bytes(optional) + table + marker)
    return path


def checksum(data: bytes) -> int:
    """The PE checksum, computed the long way to check laa.pe_checksum."""
    data = data[:CHECKSUM_AT] + bytes(4) + data[CHECKSUM_AT + 4 :]
    total = 0
    for i in range(0, len(data), 2):
        total += int.from_bytes(data[i : i + 2], "little")
        total = (total & 0xFFFF) + (total >> 16)
    return (total & 0xFFFF) + (total >> 16) + len(data)


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

    backup, was_flagged = laa.patch(install_dir, patched)
    assert not was_flagged
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


def test_patch_flags_a_drm_free_exe(tmp_path: Path, install_dir: Path) -> None:
    exe = install_dir / "bin_ship/DAOrigins.exe"
    source = make_exe(tmp_path / "drm-free.exe", large_address_aware=False, marker=b"x" * 101)
    before = source.read_bytes()

    backup, was_flagged = laa.patch(install_dir, source)
    assert was_flagged and backup.read_bytes().endswith(b"original")
    assert source.read_bytes() == before  # the user's file is left alone
    installed = exe.read_bytes()
    assert laa.is_laa(exe)
    assert struct.unpack_from("<I", installed, CHECKSUM_AT)[0] == checksum(installed)
    differing = [i for i, (a, b) in enumerate(zip(before, installed, strict=True)) if a != b]
    assert all(0x80 + 22 <= i < 0x80 + 24 or CHECKSUM_AT <= i < CHECKSUM_AT + 4 for i in differing)


def test_patch_rejects_unsuitable_sources(tmp_path: Path, install_dir: Path) -> None:
    exe = install_dir / "bin_ship/DAOrigins.exe"
    before = exe.read_bytes()
    steam = make_exe(
        tmp_path / "steam.exe", large_address_aware=False, sections=(".text", ".rsrc", ".bind")
    )
    with pytest.raises(laa.LaaError, match="Steam's DRM"):
        laa.patch(install_dir, steam)
    truncated = tmp_path / "truncated.exe"
    truncated.write_bytes(steam.read_bytes()[:0x100])
    with pytest.raises(laa.LaaError, match="not a Windows executable"):
        laa.patch(install_dir, truncated)
    assert exe.read_bytes() == before
    assert not (install_dir / "bin_ship/DAOrigins.exe.pre-laa").exists()
    with pytest.raises(laa.LaaError, match="not found"):
        laa.patch(tmp_path / "nowhere", steam)


def test_patch_with_missing_exe(tmp_path: Path, install_dir: Path) -> None:
    with pytest.raises(laa.LaaError, match="not found"):
        laa.patch(install_dir, tmp_path / "missing.exe")
    assert laa.status(install_dir) == laa.UNPATCHED


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
    assert cli.main(["laa", "patch"]) == 1
    assert laa.GUIDE_URL in capsys.readouterr().err
    assert cli.main(["laa", "patch", "--exe", str(patched)]) == 0
    assert laa.status(install_dir) == laa.PATCHED
    assert cli.main(["laa", "patch", "--exe", str(patched)]) == 1
    assert "already" in capsys.readouterr().err
    assert cli.main(["laa", "restore"]) == 0
    assert laa.status(install_dir) == laa.UNPATCHED
    drm_free = make_exe(tmp_path / "drm-free.exe", large_address_aware=False)
    capsys.readouterr()
    assert cli.main(["laa", "patch", "--exe", str(drm_free)]) == 0
    assert "set the LAA flag" in capsys.readouterr().out
    assert laa.status(install_dir) == laa.PATCHED

    config.game_install_dir = None
    assert cli.main(["laa", "status"]) == 1
