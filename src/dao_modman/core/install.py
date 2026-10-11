"""Download a file from Nexus and add it to the library (shared by the TUI and the CLI)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from . import console
from .library import Library
from .models import Mod
from .nexus import (
    NexusClient,
    NexusError,
    NxmLink,
    Progress,
    fetch_archive,
    newest_file_id,
    nexus_info,
)


def display_name(mod_info: dict, file_info: dict) -> str:
    """Main files are named after the mod, everything else gets the file name appended."""
    mod_name = str(mod_info.get("name") or f"Nexus mod {mod_info.get('mod_id', '')}").strip()
    file_name = str(file_info.get("name") or "").strip()
    if file_info.get("category_name") == "MAIN" or not file_name or file_name == mod_name:
        return mod_name
    return f"{mod_name} - {file_name}"


def superseded_mod(library: Library, listing: dict, mod_id: int, file_id: int) -> Mod | None:
    """An installed mod whose Nexus file was replaced by `file_id` according to Nexus."""
    for mod in library.mods.values():
        if not mod.nexus or mod.nexus.mod_id != mod_id or mod.nexus.file_id == file_id:
            continue
        if newest_file_id(listing, mod.nexus.file_id) == file_id:
            return mod
    return None


async def install_from_nexus(
    client: NexusClient,
    library: Library,
    mod_id: int,
    file_id: int,
    *,
    link: NxmLink | None = None,
    progress: Progress | None = None,
    started: Callable[[str], None] | None = None,
) -> tuple[Mod, bool]:
    """Download and import a Nexus file. Returns `(mod, updated_in_place)`.

    If the file supersedes one that is already installed, that mod is updated in place so
    its load order position and path operations carry over.
    """
    mod_info = await client.mod(mod_id)
    listing = await client.files(mod_id)
    file_info = next((f for f in listing.get("files", []) if f.get("file_id") == file_id), None)
    if file_info is None:
        raise NexusError(f"file {file_id} not found for mod {mod_id}")
    if started:
        started(display_name(mod_info, file_info))
    archive = await fetch_archive(
        client, library.dirs.downloads, mod_id, file_info, link=link, progress=progress
    )
    info = nexus_info(mod_id, file_info)
    version = str(file_info.get("version") or mod_info.get("version") or "")
    if old := superseded_mod(library, listing, mod_id, file_id):
        mod = await asyncio.to_thread(
            library.replace_archive, old, archive, version=version, nexus=info
        )
        return mod, True
    mod = await asyncio.to_thread(
        library.import_archive,
        archive,
        name=display_name(mod_info, file_info),
        version=version,
        nexus=info,
    )
    console.place_fonts(library, mod)
    return mod, False


async def link_to_nexus(client: NexusClient, library: Library, mod: Mod) -> bool:
    """Best effort: identify a manually imported archive on Nexus by its MD5."""
    if mod.nexus or not mod.archive:
        return False
    try:
        matches = await client.md5_search(mod.archive.md5)
    except NexusError:
        return False  # 404: unknown archive
    for match in matches if isinstance(matches, list) else []:
        mod_info, file_info = match.get("mod") or {}, match.get("file_details") or {}
        if mod_info.get("mod_id") and file_info.get("file_id"):
            mod.nexus = nexus_info(int(mod_info["mod_id"]), file_info)
            mod.version = mod.version or str(file_info.get("version") or "")
            library.save_mod(mod, f"{mod.id}: link to Nexus mod {mod.nexus.mod_id}")
            return True
    return False
