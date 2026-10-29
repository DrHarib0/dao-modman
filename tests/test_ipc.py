from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import ipc
from dao_modman.core.config import Config
from dao_modman.core.paths import Dirs

LINK = "nxm://dragonage/mods/7/files/22?key=k&expires=99"


def test_lock_tells_whether_an_instance_runs(tmp_path: Path) -> None:
    assert not ipc.is_running(tmp_path)
    lock = ipc.InstanceLock(tmp_path)
    assert lock.acquire()
    assert ipc.is_running(tmp_path)
    assert not ipc.InstanceLock(tmp_path).acquire()
    lock.release()
    assert not ipc.is_running(tmp_path)


def test_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ipc.runtime_dir() == tmp_path
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert ipc.runtime_dir() == tmp_path / "share/dao-modman"


async def test_links_are_delivered_and_errors_reported(tmp_path: Path) -> None:
    received: list[str] = []

    def on_link(url: str) -> str | None:
        received.append(url)
        return "not for this game" if "skyrim" in url else None

    port_file = tmp_path / ipc.PORT_NAME
    port_file.write_text("stale")  # left over from a crashed instance
    server = await ipc.serve(tmp_path, on_link)
    if os.name != "nt":
        assert port_file.stat().st_mode & 0o777 == 0o600
    info = json.loads(port_file.read_text())

    await asyncio.to_thread(ipc.send_link, tmp_path, LINK)
    with pytest.raises(ipc.IpcError, match="not for this game"):
        await asyncio.to_thread(ipc.send_link, tmp_path, "nxm://skyrim/mods/1/files/2")
    assert received == [LINK, "nxm://skyrim/mods/1/files/2"]

    async def raw(request: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(ipc.HOST, info["port"])
        writer.write(request)
        reply = await reader.readline()
        writer.close()
        return reply

    # without the token nothing is delivered; garbage is answered, not acted on
    assert await raw(json.dumps({"nxm": LINK}).encode() + b"\n") == b"error: not authorized\n"
    wrong = {"token": "0" * 64, "nxm": LINK}
    assert await raw(json.dumps(wrong).encode() + b"\n") == b"error: not authorized\n"
    assert await raw(b"[1, 2]\n") == b"error: not authorized\n"
    bad = {"token": info["token"], "nxm": 5}
    assert await raw(json.dumps(bad).encode() + b"\n") == b"error: malformed request\n"
    assert await raw(b"not json\n") == b"error: malformed request\n"
    assert len(received) == 2

    await ipc.stop(server, tmp_path)
    assert not port_file.exists()
    with pytest.raises(ipc.IpcError, match="does not answer"):
        await asyncio.to_thread(ipc.send_link, tmp_path, LINK, 0.2)


def test_stale_port_file_does_not_answer(tmp_path: Path) -> None:
    with socket.socket() as sock:  # a port that nothing listens on any more
        sock.bind((ipc.HOST, 0))
        port = sock.getsockname()[1]
    (tmp_path / ipc.PORT_NAME).write_text(json.dumps({"port": port, "token": "t"}))
    with pytest.raises(ipc.IpcError, match="does not answer"):
        ipc.send_link(tmp_path, LINK, 0.3)


@pytest.fixture
def handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The nxm handler with its outside world recorded instead of executed."""
    config = Config(dirs=Dirs(tmp_path / "d", tmp_path / "d/dl"), runtime_dir=tmp_path / "run")
    config.runtime_dir.mkdir()
    seen: dict = {"config": config, "spawned": [], "sent": [], "reported": [], "apps": []}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "_report", seen["reported"].append)
    monkeypatch.setattr(cli.terminal, "command", lambda configured, argv: [configured, *argv])
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **_: seen["spawned"].append(argv))
    monkeypatch.setattr(cli.ipc, "send_link", lambda directory, url: seen["sent"].append(url))

    class FakeApp:
        def __init__(self, config: Config, links: list[str]) -> None:
            seen["apps"].append(links)

        def run(self) -> None:
            pass

    import dao_modman.tui.app as tui_app

    monkeypatch.setattr(tui_app, "ModManApp", FakeApp)
    return seen


def test_handler_starts_a_terminal_when_nothing_runs(handler: dict) -> None:
    handler["config"].terminal = "kitty"
    assert cli.main(["nxm", LINK]) == 0
    assert handler["spawned"] == [["kitty", cli.sys.executable, "-m", "dao_modman", "--nxm", LINK]]
    assert handler["sent"] == []


def test_handler_forwards_to_the_running_instance(handler: dict) -> None:
    lock = ipc.InstanceLock(handler["config"].runtime_dir)
    assert lock.acquire()
    try:
        assert cli.main(["nxm", LINK]) == 0
        assert handler["sent"] == [LINK]
        assert handler["spawned"] == []
        # an instance started for a link, finding another one running, hands its link over
        assert cli.main(["--nxm", LINK]) == 0
        assert handler["sent"] == [LINK, LINK]
        assert handler["apps"] == []
    finally:
        lock.release()


def test_instance_started_with_a_link_downloads_it_itself(handler: dict) -> None:
    assert cli.main(["--nxm", LINK]) == 0
    assert handler["apps"] == [[LINK]]
    assert handler["sent"] == []
    assert cli.main([]) == 0
    assert handler["apps"] == [[LINK], []]


def test_handler_reports_problems_to_the_desktop(
    handler: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli.main(["nxm", "https://example.com/not-a-link"]) == 1
    assert handler["spawned"] == []

    def no_terminal(configured: str | None, argv: list[str]) -> list[str]:
        raise cli.terminal.TerminalError("no terminal emulator found")

    monkeypatch.setattr(cli.terminal, "command", no_terminal)
    assert cli.main(["nxm", LINK]) == 1
    assert handler["reported"] == ["not a valid nxm:// link", "no terminal emulator found"]
