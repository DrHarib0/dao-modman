from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from dao_modman.core import deploy, textures
from dao_modman.core.library import Library
from dao_modman.core.paths import Dirs

from .conftest import make_zip

OVERRIDE = "packages/core/override"


def make_dds(
    width: int,
    height: int,
    *,
    fourcc: bytes = b"DXT5",
    mips: int | None = None,
    caps2: int = 0,
    block: int | None = None,
) -> bytes:
    """A DDS file whose mipmap level `n` is filled with the byte `n`."""
    block = block or (8 if fourcc == b"DXT1" else 16)
    full = max(width, height).bit_length()
    mips = full if mips is None else mips
    size = lambda level: (  # noqa: E731
        max(1, ((width >> level or 1) + 3) // 4) * max(1, ((height >> level or 1) + 3) // 4) * block
    )
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x000A1007, height, width, size(0), 0, mips)
    struct.pack_into("<2I4s", header, 76, 32, 0x4, fourcc)
    struct.pack_into("<2I", header, 108, 0x401008, caps2)
    return bytes(header) + b"".join(bytes([level]) * size(level) for level in range(mips))


def fields(data: bytes) -> tuple[int, int, int, int]:
    height, width, linear, _depth, mips = struct.unpack_from("<5I", data, 12)
    return width, height, mips, linear


def reduce(data: bytes, limit: int) -> bytes | None:
    reduction = textures.plan_reduction(data[:128], len(data), limit)
    return None if reduction is None else reduction.header + data[reduction.offset :]


def test_reduction_drops_the_largest_levels() -> None:
    big = make_dds(256, 256)
    small = reduce(big, 64)
    assert small == make_dds(256, 256)[:12] + small[12:]  # same magic and header size
    assert fields(small) == (64, 64, 7, 16 * 16 * 16)
    # exactly the original's levels 2 and up, renumbered by nothing but the header
    assert small[128:] == big[128 + 64 * 64 * 16 + 32 * 32 * 16 :]
    assert small[128] == 2 and len(small) == len(make_dds(64, 64))
    assert small[32:108] == big[32:108] and small[108:][:20] == big[108:128]  # the rest is kept

    wide = reduce(make_dds(1024, 64, fourcc=b"DXT1"), 256)  # the longest side counts
    assert fields(wide) == (256, 16, 9, 64 * 4 * 8)
    assert len(wide) == len(make_dds(256, 16, fourcc=b"DXT1", mips=9))
    tall = reduce(make_dds(64, 1024), 512)
    assert fields(tall)[:3] == (32, 512, 10)


def test_what_is_left_alone() -> None:
    assert reduce(make_dds(256, 256), 256) is None  # at the limit
    assert reduce(make_dds(64, 64), 256) is None
    assert reduce(make_dds(256, 256, mips=1), 64) is None  # no smaller level to fall back on
    assert reduce(make_dds(256, 256, mips=2), 64) is None  # its smallest is still too large
    assert reduce(make_dds(256, 256, fourcc=b"DX10"), 64) is None
    assert reduce(make_dds(256, 256, fourcc=b"ATI2"), 64) is None
    assert reduce(make_dds(256, 256, caps2=0x200), 64) is None  # cube map
    assert reduce(make_dds(256, 256) + b"extra", 64) is None  # not laid out as expected
    assert reduce(b"not a texture".ljust(200, b"."), 64) is None
    assert textures.read_size(make_dds(512, 128)[:128]) == (512, 128)
    assert textures.read_size(b"short") is None


def test_limit_and_apply(tmp_path: Path, dirs: Dirs, library: Library, user_dir: Path) -> None:
    files = {
        "huge_d.dds": make_dds(1024, 1024),
        "wide_d.DDS": make_dds(2048, 128, fourcc=b"DXT1"),
        "fine_d.dds": make_dds(256, 256),
        "flat_d.dds": make_dds(1024, 1024, mips=1),
        "model.mmh": b"m" * 300,
    }
    mod = library.import_archive(make_zip(tmp_path / "Pack.zip", files))
    plan = deploy.build_plan(library)

    with pytest.raises(ValueError, match="power of two"):
        textures.limit(plan, 3000)
    assert textures.limit(plan, 0).note() is None
    assert all(item.reduction is None for item in plan.files)

    seen: list[tuple[int, int]] = []
    summary = textures.limit(plan, 256, lambda _phase, done, total: seen.append((done, total)))
    assert seen[-1] == (4, 4)
    reduced = {i.target.rpartition("/")[2] for i in plan.files if i.reduction is not None}
    assert reduced == {"huge_d.dds", "wide_d.DDS"}
    saved = len(files["huge_d.dds"]) - len(make_dds(256, 256))
    saved += len(files["wide_d.DDS"]) - len(make_dds(256, 16, fourcc=b"DXT1", mips=9))
    assert (summary.reduced, summary.saved) == (2, saved)
    assert summary.note() == "Textures: 2 reduced to 256 px, 0.0 GB less"
    assert summary.kept == [f"{OVERRIDE}/pack/flat_d.dds"]
    assert "1 texture(s) over 256 px deployed as they are: flat_d.dds" in summary.warning()

    deploy.apply(plan, user_dir, dirs.deployed_file)
    out = user_dir / OVERRIDE / "pack"
    written = (out / "huge_d.dds").read_bytes()
    assert fields(written)[:3] == (256, 256, 9) and written[128] == 2
    assert written == reduce(files["huge_d.dds"], 256)
    assert fields((out / "wide_d.DDS").read_bytes())[:2] == (256, 16)
    assert (out / "fine_d.dds").read_bytes() == files["fine_d.dds"]
    assert (out / "flat_d.dds").read_bytes() == files["flat_d.dds"]
    assert (out / "model.mmh").read_bytes() == files["model.mmh"]
    # the library keeps the original; the record knows what was really written
    assert (library.files_dir(mod.id) / "huge_d.dds").read_bytes() == files["huge_d.dds"]
    record = {f["path"]: f["sha256"] for f in json.loads(dirs.deployed_file.read_text())["files"]}
    assert record[f"{OVERRIDE}/pack/huge_d.dds"] == hashlib.sha256(written).hexdigest()
    assert deploy.scan(user_dir, dirs.deployed_file).modified == []

    # another limit on the next apply: the plan is new, the files follow
    plan = deploy.build_plan(library)
    assert textures.limit(plan, 1024).reduced == 1  # only the 2048 wide one is over it now
    deploy.apply(plan, user_dir, dirs.deployed_file)
    assert (out / "huge_d.dds").read_bytes() == files["huge_d.dds"]
    assert fields((out / "wide_d.DDS").read_bytes())[:2] == (1024, 64)
    deploy.unapply(dirs.deployed_file)
    assert not out.exists()
