"""Nexus Mods API (v1) client, `nxm://` links and archive downloads."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from .models import NexusInfo

API_BASE = "https://api.nexusmods.com/v1"
GAME = "dragonage"
APP_NAME = "dao-modman"
APP_VERSION = "0.1.0"

_MOD_URL = re.compile(r"nexusmods\.com/(?P<game>[^/?#]+)/mods/(?P<mod_id>\d+)")

Progress = Callable[[int, int | None], None]


class NexusError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class NxmLink:
    game: str
    mod_id: int
    file_id: int
    key: str | None = None
    expires: str | None = None


def parse_nxm(url: str) -> NxmLink:
    """Parse `nxm://<game>/mods/<mod>/files/<file>?key=..&expires=..`."""
    parts = urlsplit(url.strip())
    segments = [segment for segment in parts.path.split("/") if segment]
    if (
        parts.scheme != "nxm"
        or len(segments) != 4
        or segments[0] != "mods"
        or segments[2] != "files"
        or not (segments[1].isdigit() and segments[3].isdigit())
    ):
        raise NexusError("not a valid nxm:// link")
    query = parse_qs(parts.query)
    return NxmLink(
        game=parts.netloc.lower(),
        mod_id=int(segments[1]),
        file_id=int(segments[3]),
        key=query.get("key", [None])[0],
        expires=query.get("expires", [None])[0],
    )


def parse_mod_ref(text: str) -> int:
    """Mod id from a bare number or a nexusmods.com mod URL."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    match = _MOD_URL.search(text)
    if not match:
        raise NexusError("expected a mod id, a nexusmods.com mod URL or an nxm:// link")
    if match["game"].lower() != GAME:
        raise NexusError(f"that mod is for {match['game']!r}, not {GAME!r}")
    return int(match["mod_id"])


def newest_file_id(files: dict, file_id: int) -> int:
    """Follow the `file_updates` chain of a files listing to the latest replacement."""
    successors = {
        update["old_file_id"]: update["new_file_id"] for update in files.get("file_updates", [])
    }
    seen = {file_id}
    while (successor := successors.get(file_id)) is not None and successor not in seen:
        seen.add(successor)
        file_id = successor
    return file_id


def nexus_info(mod_id: int, file: dict) -> NexusInfo:
    uploaded = file.get("uploaded_timestamp")
    return NexusInfo(
        game=GAME,
        mod_id=mod_id,
        file_id=int(file["file_id"]),
        file_name=str(file.get("file_name", "")),
        uploaded=datetime.fromtimestamp(uploaded, UTC) if uploaded else None,
    )


class NexusClient:
    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        user_agent = f"{APP_NAME}/{APP_VERSION}"
        self._api = httpx.AsyncClient(
            base_url=API_BASE,
            transport=transport,
            timeout=30,
            headers={
                "apikey": token,
                "Application-Name": APP_NAME,
                "Application-Version": APP_VERSION,
                "User-Agent": user_agent,
            },
        )
        # Downloads come from CDN hosts, which must never see the API key.
        self._cdn = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(30, read=120),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    async def aclose(self) -> None:
        await self._api.aclose()
        await self._cdn.aclose()

    async def _get(self, path: str, **params: str) -> dict | list:
        try:
            response = await self._api.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise NexusError(f"Nexus request failed: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            daily = response.headers.get("x-rl-daily-remaining", "?")
            hourly = response.headers.get("x-rl-hourly-remaining", "?")
            raise NexusError(f"Nexus rate limit hit (remaining: daily {daily}, hourly {hourly})")
        if response.status_code == 401:
            raise NexusError("Nexus rejected the API token")
        if response.is_error:
            message = ""
            with_json = response.headers.get("content-type", "").startswith("application/json")
            if with_json and isinstance(body := response.json(), dict):
                message = str(body.get("message", ""))
            raise NexusError(
                f"Nexus error {response.status_code}: {message or path}", response.status_code
            )
        return response.json()

    async def validate(self) -> dict:
        """Account info for the token (`name`, `is_premium`, ...)."""
        return await self._get("/users/validate.json")

    async def mod(self, mod_id: int) -> dict:
        return await self._get(f"/games/{GAME}/mods/{mod_id}.json")

    async def files(self, mod_id: int) -> dict:
        """`{"files": [...], "file_updates": [...]}`"""
        return await self._get(f"/games/{GAME}/mods/{mod_id}/files.json")

    async def file(self, mod_id: int, file_id: int) -> dict:
        return await self._get(f"/games/{GAME}/mods/{mod_id}/files/{file_id}.json")

    async def md5_search(self, md5: str) -> list:
        return await self._get(f"/games/{GAME}/mods/md5_search/{md5}.json")

    async def download_urls(self, mod_id: int, file_id: int, link: NxmLink | None = None) -> list:
        """CDN URLs for a file. Free accounts need the key/expires pair of an nxm link."""
        params = {}
        if link and link.key and link.expires:
            params = {"key": link.key, "expires": link.expires}
        path = f"/games/{GAME}/mods/{mod_id}/files/{file_id}/download_link.json"
        try:
            mirrors = await self._get(path, **params)
        except NexusError as exc:
            if exc.status == 403 and not params:
                raise NexusError(
                    "Free Nexus accounts cannot download through the API directly: use the "
                    '"Mod Manager Download" button on the website and paste the nxm:// link'
                ) from exc
            raise
        urls = [mirror["URI"] for mirror in mirrors if mirror.get("URI")]
        if not urls:
            raise NexusError("Nexus returned no download link")
        return urls

    async def download(
        self,
        url: str,
        dest_dir: Path,
        file_name: str,
        *,
        progress: Progress | None = None,
        expected_md5: str | None = None,
    ) -> Path:
        """Stream `url` to `dest_dir/file_name` (via a `.part` file)."""
        safe_name = Path(file_name.replace("\\", "/")).name
        if not safe_name or safe_name in {".", ".."}:
            raise NexusError(f"invalid file name {file_name!r}")
        if not url.startswith("https://"):
            raise NexusError("refusing non-https download link")
        dest = dest_dir / safe_name
        part = dest_dir / (safe_name + ".part")
        md5 = hashlib.md5(usedforsecurity=False)
        try:
            async with self._cdn.stream("GET", url) as response:
                if response.is_error:
                    raise NexusError(f"download failed with HTTP {response.status_code}")
                total = int(response.headers.get("content-length", 0)) or None
                done = 0
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(1024 * 256):
                        out.write(chunk)
                        md5.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            if expected_md5 and md5.hexdigest() != expected_md5.lower():
                raise NexusError(f"checksum mismatch for {safe_name}")
            os.replace(part, dest)
        except httpx.HTTPError as exc:
            raise NexusError(f"download failed: {exc.__class__.__name__}") from exc
        finally:
            part.unlink(missing_ok=True)
        return dest


async def fetch_archive(
    client: NexusClient,
    dest_dir: Path,
    mod_id: int,
    file: dict,
    *,
    link: NxmLink | None = None,
    progress: Progress | None = None,
) -> Path:
    """Download one file of a mod (as described by the files API) into `dest_dir`."""
    file_id = int(file["file_id"])
    urls = await client.download_urls(mod_id, file_id, link)
    return await client.download(
        urls[0],
        dest_dir,
        str(file.get("file_name") or f"{mod_id}-{file_id}"),
        progress=progress,
        expected_md5=file.get("md5"),
    )
