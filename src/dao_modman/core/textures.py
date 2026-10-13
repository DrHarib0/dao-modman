"""Limit the size of deployed textures by leaving out their largest mipmap levels.

The game is a 32-bit program; texture packs with levels of 8192 pixels and more make it run
out of memory while loading. A DDS file stores each smaller level after the larger one, so a
texture is reduced by cutting the top levels off and correcting the header: nothing is
decoded or compressed again.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import deploy

HEADER_SIZE = 128
LIMITS = {0: "No limit", 1024: "1K (1024 px)", 2048: "2K (2048 px)", 4096: "4K (4096 px)"}
SUFFIX = ".dds"
_BLOCK_BYTES = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16}  # per 4x4 block of pixels
_FOURCC = 0x4  # pixel format flag: the format is named by a four character code
_SPECIAL = 0x200 | 0xFC00 | 0x200000  # caps2: cube map, its faces, volume texture
_HEIGHT, _WIDTH, _LINEAR_SIZE, _MIPS = 12, 16, 20, 28
_PF_FLAGS, _PF_FOURCC, _CAPS2 = 80, 84, 112


@dataclass(frozen=True)
class TextureInfo:
    width: int
    height: int
    mips: int
    block_bytes: int

    def level_size(self, level: int) -> int:
        width, height = max(1, self.width >> level), max(1, self.height >> level)
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * self.block_bytes


@dataclass(frozen=True)
class Reduction:
    """How to write a smaller copy: this header, then the source from `offset` on."""

    header: bytes
    offset: int
    width: int
    height: int

    @property
    def saved(self) -> int:
        return self.offset - HEADER_SIZE


@dataclass
class Summary:
    limit: int
    reduced: int = 0
    saved: int = 0  # bytes
    kept: list[str] = field(default_factory=list)  # over the limit, but not reducible

    def note(self) -> str | None:
        if not self.reduced:
            return None
        return (
            f"Textures: {self.reduced} reduced to {self.limit} px, {self.saved / 1e9:.1f} GB less"
        )

    def warning(self) -> str | None:
        if not self.kept:
            return None
        names = ", ".join(name.rpartition("/")[2] for name in self.kept[:3])
        more = f" and {len(self.kept) - 3} more" if len(self.kept) > 3 else ""
        return (
            f"{len(self.kept)} texture(s) over {self.limit} px deployed as they are: {names}{more}"
        )


def read_size(header: bytes) -> tuple[int, int] | None:
    """Width and height of a DDS file, from its first bytes."""
    if len(header) < HEADER_SIZE or header[:4] != b"DDS ":
        return None
    height, width = struct.unpack_from("<2I", header, _HEIGHT)
    return width, height


def read_info(header: bytes) -> TextureInfo | None:
    """What is needed to reduce a texture; None if it is not of a kind we can reduce."""
    size = read_size(header)
    if size is None:
        return None
    (flags,) = struct.unpack_from("<I", header, _PF_FLAGS)
    fourcc = header[_PF_FOURCC : _PF_FOURCC + 4]
    (caps2,) = struct.unpack_from("<I", header, _CAPS2)
    if not flags & _FOURCC or fourcc not in _BLOCK_BYTES or caps2 & _SPECIAL:
        return None
    (mips,) = struct.unpack_from("<I", header, _MIPS)
    return TextureInfo(size[0], size[1], max(1, mips), _BLOCK_BYTES[fourcc])


def plan_reduction(header: bytes, file_size: int, limit: int) -> Reduction | None:
    """The reduction that brings a texture's longest side down to `limit`, if it can be done."""
    info = read_info(header)
    if info is None or max(info.width, info.height) <= limit:
        return None
    if HEADER_SIZE + sum(info.level_size(level) for level in range(info.mips)) != file_size:
        return None  # not laid out as expected: better left alone
    drop = 0
    while max(info.width, info.height) >> drop > limit:
        drop += 1
    if drop >= info.mips:
        return None  # it has no level that small
    width, height = max(1, info.width >> drop), max(1, info.height >> drop)
    new = bytearray(header[:HEADER_SIZE])
    struct.pack_into("<2I", new, _HEIGHT, height, width)
    struct.pack_into("<I", new, _LINEAR_SIZE, info.level_size(drop))
    struct.pack_into("<I", new, _MIPS, info.mips - drop)
    offset = HEADER_SIZE + sum(info.level_size(level) for level in range(drop))
    return Reduction(bytes(new), offset, width, height)


def limit(plan: deploy.Plan, max_size: int, progress: deploy.Progress | None = None) -> Summary:
    """Give the plan's textures over `max_size` pixels their reduction. 0 means no limit."""
    if max_size < 0 or max_size & (max_size - 1):
        raise ValueError(f"max_texture_size must be 0 or a power of two, not {max_size}")
    summary = Summary(max_size)
    if not max_size:
        return summary
    textures = [
        item
        for item in plan.files
        if item.content is None and item.target.casefold().endswith(SUFFIX)
    ]
    for index, item in enumerate(textures):
        item.reduction = None
        try:
            with item.source.open("rb") as fh:
                header = fh.read(HEADER_SIZE)
            size = read_size(header)
            if size is not None and max(size) > max_size:
                item.reduction = plan_reduction(header, item.source.stat().st_size, max_size)
                if item.reduction is None:
                    summary.kept.append(item.target)
                else:
                    summary.reduced += 1
                    summary.saved += item.reduction.saved
        except OSError:
            pass  # apply itself reports a file it cannot read
        if progress:
            progress("Checking textures", index + 1, len(textures))
    return summary
