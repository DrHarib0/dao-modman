from __future__ import annotations

import hashlib
from pathlib import Path

import httpx2
import pytest

from dao_modman.core import nexus
from dao_modman.core.nexus import NexusClient, NexusError, NxmLink

PAYLOAD = b"archive-bytes" * 1000
FILE = {
    "file_id": 22,
    "file_name": "My Mod-7-1-0.zip",
    "uploaded_timestamp": 1262304000,
    "md5": hashlib.md5(PAYLOAD).hexdigest(),
}


def make_client(premium: bool = True, seen: list[httpx2.Request] | None = None) -> NexusClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx2.Response(200, content=PAYLOAD)
        if request.headers.get("apikey") != "secret":
            return httpx2.Response(401, json={"message": "bad key"})
        if path == "/v1/users/validate.json":
            return httpx2.Response(200, json={"name": "seb", "is_premium": premium})
        if path == "/v1/games/dragonage/mods/7.json":
            return httpx2.Response(200, json={"mod_id": 7, "name": "My Mod", "version": "1.0"})
        if path == "/v1/games/dragonage/mods/7/files.json":
            return httpx2.Response(200, json={"files": [FILE], "file_updates": []})
        if path == "/v1/games/dragonage/mods/7/files/22/download_link.json":
            if not premium and request.url.params.get("key") != "k":
                return httpx2.Response(403, json={"message": "premium only"})
            return httpx2.Response(200, json=[{"URI": "https://cdn.example/f.zip?token=t"}])
        if path == "/v1/games/dragonage/mods/8.json":
            return httpx2.Response(429, headers={"x-rl-daily-remaining": "0"})
        return httpx2.Response(404, json={"message": "not found"})

    return NexusClient("secret", transport=httpx2.MockTransport(handler))


def test_parse_nxm() -> None:
    link = nexus.parse_nxm("nxm://DragonAge/mods/7/files/22?key=k&expires=99&user_id=1")
    assert link == NxmLink("dragonage", 7, 22, "k", "99")
    assert nexus.parse_nxm("nxm://dragonage/mods/7/files/22").key is None


@pytest.mark.parametrize(
    "url", ["https://x/mods/7/files/22", "nxm://dragonage/mods/7", "nxm://dragonage/mods/a/files/b"]
)
def test_parse_nxm_invalid(url: str) -> None:
    with pytest.raises(NexusError):
        nexus.parse_nxm(url)


def test_parse_mod_ref() -> None:
    assert nexus.parse_mod_ref(" 577 ") == 577
    assert nexus.parse_mod_ref("https://www.nexusmods.com/dragonage/mods/577?tab=files") == 577
    with pytest.raises(NexusError):
        nexus.parse_mod_ref("https://www.nexusmods.com/skyrim/mods/577")
    with pytest.raises(NexusError):
        nexus.parse_mod_ref("hello")


def test_newest_file_id() -> None:
    files = {
        "file_updates": [
            {"old_file_id": 1, "new_file_id": 2},
            {"old_file_id": 2, "new_file_id": 3},
            {"old_file_id": 3, "new_file_id": 1},  # cycles must not hang
        ]
    }
    assert nexus.newest_file_id(files, 9) == 9
    assert nexus.newest_file_id({"file_updates": files["file_updates"][:2]}, 1) == 3
    assert nexus.newest_file_id(files, 1) == 3


def test_nexus_info() -> None:
    info = nexus.nexus_info(7, FILE)
    assert (info.game, info.mod_id, info.file_id) == ("dragonage", 7, 22)
    assert info.uploaded.year == 2010


async def test_api_calls() -> None:
    client = make_client()
    assert (await client.validate())["name"] == "seb"
    assert (await client.mod(7))["name"] == "My Mod"
    assert (await client.files(7))["files"][0]["file_id"] == 22
    await client.aclose()


async def test_errors() -> None:
    client = make_client()
    with pytest.raises(NexusError, match="404"):
        await client.mod(1)
    with pytest.raises(NexusError, match="rate limit"):
        await client.mod(8)
    bad = NexusClient("wrong", transport=client._api._transport)
    with pytest.raises(NexusError, match="token"):
        await bad.validate()


async def test_download_premium(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    client = make_client(seen=seen)
    progress: list[tuple[int, int | None]] = []
    path = await nexus.fetch_archive(
        client, tmp_path, 7, FILE, progress=lambda done, total: progress.append((done, total))
    )
    assert path == tmp_path / "My Mod-7-1-0.zip"
    assert path.read_bytes() == PAYLOAD
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert list(tmp_path.iterdir()) == [path]  # no .part left behind
    cdn_request = next(request for request in seen if request.url.host == "cdn.example")
    assert "apikey" not in cdn_request.headers


async def test_download_free_account_needs_nxm_key(tmp_path: Path) -> None:
    client = make_client(premium=False)
    with pytest.raises(NexusError, match="Mod Manager Download"):
        await nexus.fetch_archive(client, tmp_path, 7, FILE)
    link = NxmLink("dragonage", 7, 22, "k", "99")
    path = await nexus.fetch_archive(client, tmp_path, 7, FILE, link=link)
    assert path.read_bytes() == PAYLOAD


async def test_download_checksum_mismatch(tmp_path: Path) -> None:
    client = make_client()
    with pytest.raises(NexusError, match="checksum"):
        await nexus.fetch_archive(client, tmp_path, 7, {**FILE, "md5": "0" * 32})
    assert list(tmp_path.iterdir()) == []


async def test_download_sanitizes_file_name(tmp_path: Path) -> None:
    client = make_client()
    dest = tmp_path / "downloads"
    dest.mkdir()
    path = await client.download("https://cdn.example/x", dest, "../../evil.zip")
    assert path == dest / "evil.zip"
    with pytest.raises(NexusError):
        await client.download("http://cdn.example/x", dest, "a.zip")


async def test_install_from_nexus_and_update_in_place(tmp_path: Path) -> None:
    import zipfile

    from dao_modman.core.install import install_from_nexus
    from dao_modman.core.library import Library
    from dao_modman.core.models import PathOp
    from dao_modman.core.paths import Dirs

    def archive(content: str) -> bytes:
        path = tmp_path / "tmp.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.erf", content)
        return path.read_bytes()

    payloads = {"/v1.zip": archive("one"), "/v2.zip": archive("two")}
    listing = {
        "files": [
            {"file_id": 1, "file_name": "v1.zip", "name": "Main", "category_name": "MAIN"},
            {"file_id": 2, "file_name": "v2.zip", "name": "Main", "category_name": "MAIN"},
        ],
        "file_updates": [{"old_file_id": 1, "new_file_id": 2}],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx2.Response(200, content=payloads[path])
        if path.endswith("/mods/7.json"):
            return httpx2.Response(200, json={"mod_id": 7, "name": "My Mod", "version": "9"})
        if path.endswith("/files.json"):
            return httpx2.Response(200, json=listing)
        file_id = path.split("/")[-2]
        return httpx2.Response(200, json=[{"URI": f"https://cdn.example/v{file_id}.zip"}])

    client = NexusClient("secret", transport=httpx2.MockTransport(handler))
    library = Library(Dirs(tmp_path / "data", tmp_path / "data/downloads"))

    mod, updated = await install_from_nexus(client, library, 7, 1)
    assert (mod.id, mod.name, mod.version, updated) == ("my-mod", "My Mod", "9", False)
    assert (mod.nexus.mod_id, mod.nexus.file_id) == (7, 1)
    assert (library.dirs.downloads / "v1.zip").is_file()
    mod.ops = [PathOp("rename", "packages/core/override/my-mod/a.erf", "x/a.erf")]
    library.save_mod(mod)

    mod, updated = await install_from_nexus(client, library, 7, 2)
    assert updated
    assert list(library.mods) == ["my-mod"]
    assert mod.nexus.file_id == 2
    assert library.targets(mod) == {"x/a.erf": "a.erf"}
    assert (library.files_dir("my-mod") / "a.erf").read_text() == "two"
    assert sorted(p.name for p in library.dirs.downloads.iterdir()) == ["v1.zip", "v2.zip"]

    with pytest.raises(NexusError, match="not found"):
        await install_from_nexus(client, library, 7, 3)


async def test_link_to_nexus_by_md5(tmp_path: Path) -> None:
    import zipfile

    from dao_modman.core.install import link_to_nexus
    from dao_modman.core.library import Library
    from dao_modman.core.paths import Dirs

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith(f"/md5_search/{known.archive.md5}.json"):
            match = {"mod": {"mod_id": 7}, "file_details": {"file_id": 22, "version": "3"}}
            return httpx2.Response(200, json=[match])
        return httpx2.Response(404, json={"message": "not found"})

    library = Library(Dirs(tmp_path / "data", tmp_path / "data/downloads"))
    for name in ("known", "unknown"):
        with zipfile.ZipFile(tmp_path / f"{name}.zip", "w") as zf:
            zf.writestr("a.erf", name)
    known = library.import_archive(tmp_path / "known.zip")
    unknown = library.import_archive(tmp_path / "unknown.zip")
    client = NexusClient("secret", transport=httpx2.MockTransport(handler))

    assert await link_to_nexus(client, library, known)
    assert not await link_to_nexus(client, library, unknown)
    reloaded = Library(library.dirs).mods
    assert (reloaded["known"].nexus.mod_id, reloaded["known"].nexus.file_id) == (7, 22)
    assert reloaded["known"].version == "3"
    assert reloaded["unknown"].nexus is None


def test_default_files() -> None:
    def file(file_id: int, category: str, primary: bool = False) -> dict:
        return {"file_id": file_id, "category_name": category, "is_primary": primary}

    assert nexus.default_files([file(1, "MAIN"), file(2, "OPTIONAL")]) == [1]
    # several main files: only the author's primary one, if there is exactly one
    assert nexus.default_files([file(1, "MAIN"), file(2, "MAIN", primary=True)]) == [2]
    assert nexus.default_files([file(1, "MAIN"), file(2, "MAIN")]) == []
    assert nexus.default_files([file(1, "OPTIONAL", primary=True)]) == []
    assert nexus.default_files([]) == []


def test_download_page() -> None:
    assert nexus.download_page(5616, 1000008673) == (
        "https://www.nexusmods.com/dragonage/mods/5616?tab=files&file_id=1000008673&nmm=1"
    )
