from __future__ import annotations

import contextlib
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from rich.style import Style
from textual.widgets import DataTable, Input, Label, OptionList, SelectionList, Static, Tree

from dao_modman.core import deploy, nexus, opener
from dao_modman.core.config import Config
from dao_modman.core.library import Library
from dao_modman.core.models import NexusInfo, PathOp
from dao_modman.core.paths import Dirs
from dao_modman.tui.app import ModManApp
from dao_modman.tui.screens import (
    ConfirmScreen,
    GroupScreen,
    ItemsScreen,
    NexusAddScreen,
    PathOpsScreen,
    PathTree,
    ProgressScreen,
)

from .conftest import make_dazip, make_zip
from .gamefiles import make_tlk, make_uti

OVERRIDE = "packages/core/override"


_run_test = ModManApp.run_test


@contextlib.asynccontextmanager
async def _started(app: ModManApp, *args, **kwargs):
    """Like `run_test`, but only hands over once the startup checks are done."""
    async with _run_test(app, *args, **kwargs) as pilot:
        await app.started.wait()
        await pilot.pause()
        yield pilot


@pytest.fixture(autouse=True)
def wait_for_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ModManApp, "run_test", _started)


def make_app(tmp_path: Path, dirs: Dirs, user_dir: Path, **kwargs) -> ModManApp:
    return ModManApp(Config(dirs=dirs, game_user_dir=user_dir), **kwargs)


async def until(pilot, condition) -> None:
    """Let the app work until `condition()` holds (background work decides when)."""
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition was not met in time")


def rows(app: ModManApp) -> list[list[str]]:
    table = app.query_one("#mods", DataTable)
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


def dimmed(app: ModManApp, row: int) -> bool:
    cells = app.query_one("#mods", DataTable).get_row_at(row)
    return all(str(cell.style) == "dim" for cell in cells)


async def test_toggle_move_and_group(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))
    library.import_archive(make_zip(tmp_path / "two.zip", {"b.erf": "2"}))

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert [row[2] for row in rows(app)] == ["one", "two"]
        assert "no API token" in str(app.query_one("#status", Static).render())

        assert rows(app)[0][1] == "✓" and not dimmed(app, 0)
        await pilot.press("space")
        assert rows(app)[0][1] == "" and dimmed(app, 0)
        assert not dimmed(app, 1)  # only the disabled row is dimmed
        await pilot.press("J")
        assert [row[2] for row in rows(app)] == ["two", "one"]
        assert app.current_entry().id == "one"  # cursor follows the moved mod
        await pilot.press("K")
        assert [row[2] for row in rows(app)] == ["one", "two"]

        await pilot.press("g")
        assert isinstance(app.screen, GroupScreen)
        assert not app.screen.query_one(OptionList).display  # no groups exist yet
        await pilot.press(*"Hair", "enter")
        assert rows(app)[0][4] == "Hair"

    profile = Library(dirs).profile
    assert [(e.id, e.enabled, e.group) for e in profile.mods] == [
        ("one", False, "Hair"),
        ("two", True, ""),
    ]


async def test_import_edit_paths_apply_unapply(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    files = {"a.erf": "a", "docs/extra.erf": "r", "readme.txt": "read me"}
    archive = make_zip(tmp_path / "mod.zip", files)
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("i")
        app.screen.query_one(Input).value = str(archive)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [row[2] for row in rows(app)] == ["mod"]
        assert rows(app)[0][7] == "1"  # the readme, by the ignore patterns

        # exclude the docs folder, include the readme
        await pilot.press("e")
        screen = app.screen
        assert isinstance(screen, PathOpsScreen)
        tree = screen.query_one(Tree)

        def select(path: str) -> None:
            def find(node):
                if node.data == path:
                    return node
                return next((hit for child in node.children if (hit := find(child))), None)

            tree.move_cursor(find(tree.root))

        select(f"{OVERRIDE}/mod/docs")
        await pilot.press("space")
        select(f"{OVERRIDE}/mod/readme.txt")
        await pilot.press("space")
        await pilot.pause()
        assert tree.ignored == {f"{OVERRIDE}/mod/docs", f"{OVERRIDE}/mod/docs/extra.erf"}
        await pilot.press("s")
        await pilot.pause()
        assert rows(app)[0][7] == "1"  # now docs/extra.erf

        await pilot.press("a")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert (user_dir / OVERRIDE / "mod/readme.txt").read_text() == "read me"
        assert (user_dir / OVERRIDE / "mod/a.erf").is_file()
        assert not (user_dir / OVERRIDE / "mod/docs").exists()

        assert rows(app)[0][-1] == ""  # applied: nothing to note
        await pilot.press("x")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert not (user_dir / "packages").exists()
        assert rows(app)[0][-1] == "not applied"
        assert "Applied: no" in str(app.query_one("#status", Static).render())

    assert Library(dirs).mods["mod"].ops == [
        PathOp("exclude", f"{OVERRIDE}/mod/docs"),
        PathOp("include", f"{OVERRIDE}/mod/readme.txt"),
    ]


async def test_path_ops_cancel_and_undo(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    Library(dirs).import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e", "down", "space")
        assert app.screen.ops == [PathOp("exclude", "packages")]
        await pilot.press("space")  # again: the operation goes, nothing is added
        assert app.screen.ops == []
        await pilot.press("space", "u")
        assert app.screen.ops == []
        await pilot.press("u")  # undo the toggle before that
        assert app.screen.ops == [PathOp("exclude", "packages")]
        await pilot.press("down", "space", "escape")
        await pilot.pause()
    assert Library(dirs).mods["mod"].ops == []


async def test_add_from_nexus(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    payload_path = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload_path, "w") as zf:
        zf.writestr("a.erf", "a")
    listing = {
        "files": [
            {
                "file_id": 22,
                "file_name": "My Mod-7.zip",
                "name": "Main [HD]",
                "category_name": "MAIN",
                "version": "1.1",
                "size_kb": 2048,
            },
            {"file_id": 5, "file_name": "old.zip", "name": "Old", "category_name": "ARCHIVED"},
        ],
        "file_updates": [],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx2.Response(200, content=payload_path.read_bytes())
        if path.endswith("/users/validate.json"):
            return httpx2.Response(200, json={"name": "seb", "is_premium": True})
        if path.endswith("/mods/7.json"):
            return httpx2.Response(200, json={"mod_id": 7, "name": "My Mod"})
        if path.endswith("/mods/7/files.json"):
            return httpx2.Response(200, json=listing)
        if path.endswith("/files/22/download_link.json"):
            return httpx2.Response(200, json=[{"URI": "https://cdn.example/x.zip"}])
        return httpx2.Response(404, json={"message": "nope"})

    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx2.MockTransport(handler),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        assert "seb (premium)" in str(app.query_one("#status", Static).render())

        await pilot.press("n")
        screen = app.screen
        assert isinstance(screen, NexusAddScreen)
        screen.query_one(Input).value = "https://www.nexusmods.com/dragonage/mods/7"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert screen.query_one(OptionList).option_count == 1  # archived file hidden
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [n.message for n in app._notifications] == ["Installed My Mod"]
        assert rows(app)[0][2:7] == ["My Mod", "1.1", "", "override", "nexus:7"]

    mod = Library(dirs).mods["my-mod"]
    assert (mod.nexus.mod_id, mod.nexus.file_id) == (7, 22)
    assert (dirs.downloads / "My Mod-7.zip").is_file()


async def test_tools_menu_laa_patch_and_restore(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch
) -> None:
    from dao_modman.core import laa

    from .test_laa import make_exe

    install_dir = tmp_path / "game"
    exe = make_exe(install_dir / "bin_ship/DAOrigins.exe", large_address_aware=False)
    patched = make_exe(tmp_path / "patched.exe", large_address_aware=True)

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    opened: list[str] = []
    app.open_url = lambda url, **_: opened.append(url)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 8
        await pilot.press("enter")  # first entry: apply the patch
        await pilot.pause()
        assert opened == [laa.GUIDE_URL]
        await pilot.press("escape")  # no path given: nothing happens
        await pilot.pause()
        assert not laa.is_laa(exe)

        await pilot.press("t", "enter")
        await pilot.pause()
        app.screen.query_one(Input).value = str(patched)
        await pilot.press("enter")
        await pilot.pause()
        assert laa.is_laa(exe)

        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 7  # the patch entry is gone
        await pilot.press("enter")
        await pilot.pause()
        assert not laa.is_laa(exe)


async def test_official_addins_are_listed_and_can_be_hidden(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from .conftest import make_dazip
    from .test_addins import ADDINS_XML

    install_dir = tmp_path / "game"
    for uid in ("dao_prc_ep_1", "dao_prc_cp_2"):
        (install_dir / "Addins" / uid).mkdir(parents=True)
    (user_dir / "Settings").mkdir()
    (user_dir / "Settings/addins.xml").write_text(ADDINS_XML, encoding="utf-8")
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))
    # ours: registered under the same UID as the "pretender" entry, so it gets no extra row
    library.import_archive(make_dazip(tmp_path / "p.dazip", uid="pretender", title="Pretender"))

    def names(app: ModManApp) -> list[tuple[str, str]]:
        return [(row[2], row[5]) for row in rows(app)]

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        assert names(app) == [
            ("Dragon Age: Awakening", "official"),
            ("The Stone Prisoner", "official"),
            ("Lock Bash", "external"),
            ("one", "override"),
            ("Pretender", "dazip"),
        ]
        assert rows(app)[0][6] == "game" and rows(app)[2][6] == "user dir"
        # enabled DLC looks like any enabled mod; only the one disabled in the game is dimmed
        assert rows(app)[0][1] == "✓" and not dimmed(app, 0)
        assert rows(app)[1][1] == "" and dimmed(app, 1)
        assert app.current_entry().id == "one"  # the cursor starts on the first mod

        # AddIn rows are read-only
        await pilot.press("up", "space", "J", "delete")
        assert app.current_entry() is None
        assert not isinstance(app.screen, ConfirmScreen)
        assert [e.enabled for e in app.library.profile.mods] == [True, True]

        # mod actions still hit the right mod below the AddIn rows
        await pilot.press("down", "J")
        assert names(app)[3:] == [("Pretender", "dazip"), ("one", "override")]
        assert app.current_entry().id == "one"

        await pilot.press("h")
        assert names(app) == [
            ("Lock Bash", "external"),
            ("Pretender", "dazip"),
            ("one", "override"),
        ]
        assert app.current_entry().id == "one"

    assert Library(dirs).profile.hide_official
    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        assert len(rows(app)) == 3  # still hidden after a restart
        await pilot.press("h")
        assert len(rows(app)) == 5


async def test_tools_menu_enable_console(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.core import console

    from .test_console import make_game

    install_dir = make_game(tmp_path, user_dir)
    opened: list[str] = []
    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    app.open_url = lambda url, **_: opened.append(url)

    async def run_last_tool(pilot) -> None:
        # the console entry, above the report and the update check
        await pilot.press("t", "end", "up", "up", "enter")
        await pilot.pause()

    async with app.run_test(size=(140, 40)) as pilot:
        await run_last_tool(pilot)
        assert console.is_enabled(install_dir)
        assert isinstance(app.screen, ConfirmScreen)  # fonts.erf is missing: offer the mod
        await pilot.press("n")
        assert opened == []

        await run_last_tool(pilot)  # now "disable"
        assert not console.is_enabled(install_dir)

        await run_last_tool(pilot)
        await pilot.press("y")
        assert opened == [console.FONTS_MOD_URL]

        # with fonts.erf in place there is nothing to ask
        await run_last_tool(pilot)
        (user_dir / "packages/core/data").mkdir(parents=True)
        (user_dir / "packages/core/data/Fonts.erf").write_bytes(b"erf")
        await run_last_tool(pilot)
        assert console.is_enabled(install_dir)
        assert not isinstance(app.screen, ConfirmScreen)


async def test_link_from_the_handler_downloads_in_the_running_app(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    import asyncio

    from dao_modman.core import ipc
    from dao_modman.tui.app import DownloadRow

    payload = make_zip(tmp_path / "payload.zip", {"a.erf": "a"}).read_bytes()
    release = asyncio.Event()
    files = [
        {"file_id": 22, "file_name": "one.zip", "name": "One", "category_name": "OPTIONAL"},
        {"file_id": 23, "file_name": "two.zip", "name": "Two", "category_name": "OPTIONAL"},
    ]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            await release.wait()  # keep the downloads running until the test has looked
            return httpx2.Response(200, content=payload)
        if path.endswith("/users/validate.json"):
            return httpx2.Response(200, json={"name": "seb", "is_premium": False})
        if path.endswith("/mods/7.json"):
            return httpx2.Response(200, json={"mod_id": 7, "name": "My Mod"})
        if path.endswith("/mods/7/files.json"):
            return httpx2.Response(200, json={"files": files, "file_updates": []})
        if path.endswith("/download_link.json") and request.url.params.get("key") == "k":
            file_id = path.split("/")[-2]
            return httpx2.Response(200, json=[{"URI": f"https://cdn.example/{file_id}.zip"}])
        return httpx2.Response(403, json={"message": "nope"})

    runtime = tmp_path / "run"
    runtime.mkdir()
    config = Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret", runtime_dir=runtime)
    app = ModManApp(config, transport=httpx2.MockTransport(handler))

    def link(file_id: int) -> str:
        return f"nxm://dragonage/mods/7/files/{file_id}?key=k&expires=99"

    async with app.run_test(size=(140, 40)) as pilot:
        assert ipc.is_running(runtime)
        panel = app.query_one("#downloads")
        assert not panel.display

        await asyncio.to_thread(ipc.send_link, runtime, link(22))
        await asyncio.to_thread(ipc.send_link, runtime, link(23))
        with pytest.raises(ipc.IpcError, match="not 'dragonage'"):
            await asyncio.to_thread(ipc.send_link, runtime, "nxm://skyrim/mods/1/files/2")
        for _ in range(50):  # until both downloads have reached the CDN
            await pilot.pause(0.02)
            if len(app.query(DownloadRow)) == 2 and all(
                row.label.startswith("My Mod") for row in app.query(DownloadRow)
            ):
                break
        assert panel.display
        assert sorted(row.label for row in app.query(DownloadRow)) == [
            "My Mod - One",
            "My Mod - Two",
        ]

        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not panel.display and len(app.query(DownloadRow)) == 0
        assert sorted(row[2] for row in rows(app)) == ["My Mod - One", "My Mod - Two"]

    assert not ipc.is_running(runtime)
    assert not (runtime / ipc.PORT_NAME).exists()


async def test_startup_link_and_second_instance(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.core import ipc

    runtime = tmp_path / "run"
    runtime.mkdir()
    config = Config(dirs=dirs, game_user_dir=user_dir, runtime_dir=runtime)
    # no API token: the startup link is reported instead of downloaded
    app = ModManApp(config, links=["nxm://dragonage/mods/7/files/22"])
    async with app.run_test(size=(140, 40)):
        assert [n.message for n in app._notifications][0].startswith("no Nexus API token")
        # a second instance runs, but leaves the listening to the first
        second = ModManApp(config)
        async with second.run_test(size=(140, 40)):
            assert second._server is None
        assert ipc.is_running(runtime)
        assert (runtime / ipc.PORT_NAME).exists()


async def test_path_tree_folding_and_filter(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    Library(dirs).import_archive(
        make_zip(
            tmp_path / "mod.zip",
            {
                "a.erf": "a",
                "docs/notes.gda": "r",
                "docs/img/Pic.dds": "p",
                "textures/big.dds": "b",
            },
        )
    )
    base = f"{OVERRIDE}/mod"
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        screen = app.screen
        tree = screen.query_one(Tree)

        def nodes() -> dict[str, object]:
            found = {}

            def walk(node) -> None:
                for child in node.children:
                    found[child.data] = child
                    walk(child)

            walk(tree.root)
            return found

        def files() -> list[str]:
            return sorted(p.removeprefix(base + "/") for p, n in nodes().items() if not n.children)

        def folded() -> set[str]:
            return {
                p.removeprefix(base + "/")
                for p, n in nodes().items()
                if n.children and not n.is_expanded
            }

        async def go(path: str) -> None:
            tree.move_cursor(nodes()[f"{base}/{path}"])
            await pilot.pause()

        assert folded() == set()

        # left folds a folder; on a file it jumps to the folder; right unfolds
        await go("docs")
        await pilot.press("left")
        assert folded() == {"docs"}
        await pilot.press("right")
        assert folded() == set()
        await go("docs/img/Pic.dds")
        await pilot.press("left")
        assert tree.cursor_node.data == f"{base}/docs/img"
        await pilot.press("left")
        assert folded() == {"docs/img"}

        # folds and the cursor position survive a change
        await go("textures/big.dds")
        await pilot.press("space")
        assert folded() == {"docs/img"}
        assert tree.cursor_node.data == f"{base}/textures/big.dds"  # still shown, marked
        assert f"{base}/textures/big.dds" in tree.ignored
        await pilot.press("u")
        assert folded() == {"docs/img"} and not tree.ignored

        # fold all / unfold all
        await pilot.press("c")
        assert folded() >= {"docs", "textures"}
        await pilot.press("c")
        assert folded() == set()

        # filter by part of a file name, then of a folder name
        await pilot.press("slash", *"DDS")
        assert files() == ["docs/img/Pic.dds", "textures/big.dds"]
        box = screen.query_one("#filter", Input)
        box.value = "img"
        await pilot.pause()
        assert files() == ["docs/img/Pic.dds"]
        await pilot.press("enter")
        assert tree.has_focus
        assert app.filter_text == ""  # the mod list behind the dialog is not filtered

        # excluding works on the filtered tree
        await go("docs/img/Pic.dds")
        await pilot.press("space")
        assert files() == ["docs/img/Pic.dds"] and f"{base}/docs/img/Pic.dds" in tree.ignored

        # escape leaves the filter first, then the dialog
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen and not box.display
        assert files() == ["a.erf", "docs/img/Pic.dds", "docs/notes.gda", "textures/big.dds"]
        await pilot.press("s")
        await pilot.pause()

    assert Library(dirs).mods["mod"].ops == [PathOp("exclude", f"{base}/docs/img/Pic.dds")]


async def test_rename_mod(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    Library(dirs).import_archive(make_zip(tmp_path / "Extras-4384-5-0.zip", {"a.erf": "1"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("r")
        box = app.screen.query_one(Input)
        assert box.value == "Extras-4384-5-0"
        box.value = "Dain's Fixes - Extras"
        await pilot.press("enter")
        await pilot.pause()
        assert rows(app)[0][2] == "Dain's Fixes - Extras"

        await pilot.press("r")  # an empty name or escape changes nothing
        app.screen.query_one(Input).value = "  "
        await pilot.press("enter")
        await pilot.press("r", "escape")
        await pilot.pause()
        assert rows(app)[0][2] == "Dain's Fixes - Extras"

    library = Library(dirs)
    mod = library.mods["extras-4384-5-0"]  # the id, and so the override folder, is unchanged
    assert mod.name == "Dain's Fixes - Extras"
    assert list(library.targets(mod)) == [f"{OVERRIDE}/extras-4384-5-0/a.erf"]


async def test_launch_game(tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch) -> None:
    from dao_modman.core import launch

    spawned: list[list[str]] = []
    monkeypatch.setattr(launch, "WINDOWS", False)  # Windows: see test_launch_on_windows
    monkeypatch.setattr(launch.shutil, "which", lambda name: name if name == "steam" else None)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda argv, **_: spawned.append(argv))
    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_app_id="47810"))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("l")
        assert spawned == [["steam", "-applaunch", "47810"]]
        messages = [n.message for n in app._notifications]
        assert len(messages) == 1 and "not applied" in messages[0]

        await pilot.press("a", "y")
        await app.workers.wait_for_complete()
        await pilot.press("l")
        assert [n.message for n in app._notifications][-1] == "Launching the game"

        app.config.launch_command = ["lutris", "lutris:rungame/dragon-age-origins"]
        await pilot.press("l")
        assert spawned[-1] == ["lutris", "lutris:rungame/dragon-age-origins"]

        app.config.game_app_id = app.config.launch_command = None
        await pilot.press("l")
        assert len(spawned) == 3
        assert "launch_command" in [n.message for n in app._notifications][-1]


def test_launch_on_windows(tmp_path: Path, monkeypatch) -> None:
    from dao_modman.core import launch

    spawned: list[tuple[list[str], object]] = []
    opened: list[str] = []
    monkeypatch.setattr(launch, "WINDOWS", True)
    monkeypatch.setattr(launch, "_open_url", opened.append)
    monkeypatch.setattr(
        launch.subprocess, "Popen", lambda argv, cwd=None, **_: spawned.append((argv, cwd))
    )
    install_dir = tmp_path / "Dragon Age"

    launch.launch("47810", None, install_dir)  # from Steam: through Steam
    assert opened == ["steam://rungameid/47810"] and spawned == []

    with pytest.raises(launch.LaunchError, match="not found"):
        launch.launch(None, None, install_dir)
    exe = install_dir / "bin_ship/DAOrigins.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    launch.launch(None, None, install_dir)  # not from Steam: the exe itself, in its folder
    assert spawned == [([str(exe)], exe.parent)]

    launch.launch("47810", ["my-launcher"], install_dir)  # launch_command still comes first
    assert spawned[-1] == (["my-launcher"], None)
    with pytest.raises(launch.LaunchError, match="game_install_dir"):
        launch.launch(None, None, None)


def test_launch_command_and_app_id(tmp_path: Path, monkeypatch) -> None:
    from dao_modman.core import launch, paths

    monkeypatch.setattr(launch.shutil, "which", lambda name: name if name == "xdg-open" else None)
    assert launch.command("17450") == ["xdg-open", "steam://rungameid/17450"]
    monkeypatch.setattr(launch.shutil, "which", lambda name: None)
    with pytest.raises(launch.LaunchError):
        launch.command("17450")

    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    assert paths.find_game_app_id([root]) is None
    (root / "steamapps/appmanifest_47810.acf").write_text('"AppState" {}')
    assert paths.find_game_app_id([root]) == "47810"


async def test_group_can_be_picked_from_existing_ones(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    library = Library(dirs)
    for name, group in (("one", "Textures"), ("two", "Bug fixes"), ("three", ""), ("four", "")):
        library.import_archive(make_zip(tmp_path / f"{name}.zip", {f"{name}.erf": name}))
        library.profile.entry(name).group = group
    library.save_profile()

    def options(app: ModManApp) -> list[str]:
        box = app.screen.query_one(OptionList)
        return (
            [box.get_option_at_index(i).id for i in range(box.option_count)] if box.display else []
        )

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        # pick an existing group with the arrow keys
        await pilot.press("down", "down", "g")
        assert options(app) == ["Bug fixes", "Textures"]
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        assert rows(app)[2][4] == "Textures"

        # typing narrows the list; enter in the box takes what was typed
        await pilot.press("down", "g")
        await pilot.press(*"bug")
        assert options(app) == ["Bug fixes"]
        await pilot.press("down", "enter")
        await pilot.pause()
        assert rows(app)[3][4] == "Bug fixes"

        # the current group is prefilled and all groups are offered; a new name still works
        await pilot.press("g")
        assert app.screen.query_one(Input).value == "Bug fixes"
        assert options(app) == ["Bug fixes", "Textures"]
        app.screen.query_one(Input).value = "Gameplay"
        await pilot.press("enter")
        await pilot.pause()
        assert rows(app)[3][4] == "Gameplay"

        # clearing the box removes the group
        await pilot.press("g")
        app.screen.query_one(Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert rows(app)[3][4] == ""

    groups = {e.id: e.group for e in Library(dirs).profile.mods}
    assert groups == {"one": "Textures", "two": "Bug fixes", "three": "Textures", "four": ""}


async def test_open_nexus_page(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.core.models import NexusInfo

    library = Library(dirs)
    library.import_archive(
        make_zip(tmp_path / "one.zip", {"a.erf": "1"}), nexus=NexusInfo("dragonage", 1110, 5571)
    )
    library.import_archive(make_zip(tmp_path / "local.zip", {"b.erf": "2"}))

    opened: list[str] = []
    app = make_app(tmp_path, dirs, user_dir)
    app.open_url = lambda url, **_: opened.append(url)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("o")
        assert opened == ["https://www.nexusmods.com/dragonage/mods/1110"]
        await pilot.press("down", "o")  # a local mod has no page
        assert len(opened) == 1
        assert "did not come from Nexus" in [n.message for n in app._notifications][-1]


async def test_conflicts_dialog(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.tui.screens import ConflictsScreen

    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "Alpha.zip", {"tex/robe.dds": "a", "a.erf": "a"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("v")
        assert not isinstance(app.screen, ConflictsScreen)
        assert [n.message for n in app._notifications][-1] == "Alpha has no conflicts"

        app.library.import_archive(make_zip(tmp_path / "Beta.zip", {"robe.dds": "b"}))
        app.refresh_table()
        assert rows(app)[0][8] == "not applied, overwritten by Beta"
        assert rows(app)[1][8] == "not applied, overwrites Alpha"
        await pilot.press("v")
        assert isinstance(app.screen, ConflictsScreen)
        tree = app.screen.query_one(Tree)

        def labels(node) -> list:
            return [(str(child.label), labels(child)) for child in node.children]

        assert labels(tree.root) == [
            (
                "#1 Alpha  <  #2 Beta   (1 file)",
                [
                    (
                        "robe.dds",
                        [
                            (f"#1 Alpha   {OVERRIDE}/alpha/tex/", []),
                            (f"#2 Beta    {OVERRIDE}/beta/   \u2190 used", []),
                        ],
                    )
                ],
            )
        ]
        await pilot.press("escape")
        assert not isinstance(app.screen, ConflictsScreen)


async def test_notes_show_who_overwrites_whom(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    shipped = {
        "Alpha": {"a.gda": "a", "b.gda": "a"},
        "Beta": {"a.gda": "b"},
        "Gamma": {"b.gda": "c"},
        "Delta": {"d.gda": "d"},
    }
    for name, files in shipped.items():
        library.import_archive(make_zip(tmp_path / f"{name}.zip", files))
    taken = user_dir / OVERRIDE / "hand/d.gda"  # installed by hand: it always wins
    taken.parent.mkdir(parents=True)
    taken.write_text("by hand")

    def notes(app: ModManApp) -> list[str]:  # nothing is applied here: leave that note out
        return [row[8].removeprefix("not applied").removeprefix(", ") for row in rows(app)]

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert notes(app) == [
            "overwritten by 2 mods",
            "overwrites Alpha",
            "overwrites Alpha",
            "overwritten by unmanaged files",
        ]
        await pilot.press("J")  # Alpha after Beta: now it wins against Beta
        assert notes(app)[:3] == [
            "overwritten by Alpha",
            "overwrites Beta, overwritten by Gamma",
            "overwrites Alpha",
        ]
        await pilot.press("space")  # a disabled mod overwrites nothing
        assert notes(app)[:3] == ["", "", ""]


async def test_package_options_dialog(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.tui.screens import ChoiceScreen, OptionsScreen

    from .conftest import make_override

    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "plain.zip", {"a.erf": "a"}))
    library.import_archive(make_override(tmp_path / "Bodies.override"))
    base = "packages/core/override/Bodies"

    def lines(app: ModManApp) -> list[str]:
        box = app.screen.query_one("#option-keys", OptionList)
        return [str(box.get_option_at_index(i).prompt) for i in range(box.option_count)]

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert [row[5] for row in rows(app)] == ["override", "package"]
        assert [row[8] for row in rows(app)] == ["not applied", "not applied, options"]

        await pilot.press("p")  # a mod without options
        assert not isinstance(app.screen, OptionsScreen)
        assert [n.message for n in app._notifications][-1] == "plain has no options"

        await pilot.press("down", "p")
        assert isinstance(app.screen, OptionsScreen)
        assert lines(app) == [
            "Humans / Body: Medium  \u2190 (medium)hf_body.msh",
            "Humans / Skin: Pale  \u2190 (pale)HF_skin.dds",
            "Elves / Ears: Long  \u2190 (long)ef_ears.msh",
        ]

        # change the first option: the current value is preselected, the default is marked
        await pilot.press("enter")
        assert isinstance(app.screen, ChoiceScreen)
        choice = app.screen.query_one(OptionList)
        assert [str(choice.get_option_at_index(i).prompt) for i in range(2)] == [
            "Big",
            "Medium (default)",
        ]
        assert choice.highlighted == 1
        await pilot.press("up", "enter")
        await pilot.pause()
        assert lines(app)[0] == "Humans / Body: Big  (changed)  \u2190 (big)hf_body.msh"

        # escape in the value list changes nothing
        await pilot.press("down", "enter", "escape")
        await pilot.pause()
        assert lines(app)[1] == "Humans / Skin: Pale  \u2190 (pale)HF_skin.dds"

        await pilot.press("escape")
        await pilot.pause()
        assert rows(app)[1][8] == "not applied, options (1 changed)"

        # the path editor shows the deployed name together with the variant behind it
        await pilot.press("e")
        tree = app.screen.query_one(Tree)

        def leaves(node) -> list[str]:
            found: list[str] = []
            for child in node.children:
                found += leaves(child) if child.children else [str(child.label)]
            return found

        assert sorted(leaves(tree.root)) == [
            "Readme.txt  \u00b7 ignored",  # shown, but not installed
            "ef_ears.msh  \u2190 (long)ef_ears.msh",
            "hf_body.msh  \u2190 (big)hf_body.msh",
            "hf_skin.dds  \u2190 (pale)HF_skin.dds",
            "other.mao",
        ]
        await pilot.press("escape")

        await pilot.press("a", "y")
        await app.workers.wait_for_complete()
        assert (user_dir / base / "hf_body.msh").read_text() == "big body"
        assert not (user_dir / base / "Variants").exists()

    assert Library(dirs).mods["bodies"].options == {"Humans/Body": "Big"}


async def test_apply_runs_behind_a_progress_dialog(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch
) -> None:
    import threading

    from dao_modman.core import deploy
    from dao_modman.tui.screens import ProgressScreen

    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1", "b.erf": "2"}))
    release, real_apply = threading.Event(), deploy.apply

    def slow_apply(plan, user_dir, deployed_file, progress):
        progress("Writing files", 1, 2)
        release.wait(5)  # hold the operation so the test can look at the dialog
        return real_apply(plan, user_dir, deployed_file, progress)

    monkeypatch.setattr(deploy, "apply", slow_apply)
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("a", "y")
        for _ in range(50):
            await pilot.pause(0.02)
            if isinstance(app.screen, ProgressScreen):
                break
        assert isinstance(app.screen, ProgressScreen)
        await pilot.pause(0.1)
        assert str(app.screen.query_one("#work-detail", Static).render()) == "Writing files: 1 / 2"

        # nothing gets through while it runs: not quit, not another apply, not a toggle
        await pilot.press("q", "space", "a", "escape")
        assert app.is_running and isinstance(app.screen, ProgressScreen)
        assert rows(app)[0][1] == "\u2713"

        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ProgressScreen)
        assert (user_dir / OVERRIDE / "one/a.erf").is_file()
        await pilot.press("space")  # keys work again
        assert rows(app)[0][1] == ""


async def test_unapply_offers_to_clean_the_mod_directories(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from dao_modman.tui.screens import UnapplyScreen

    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1", "b.erf": "2"}))
    manual = user_dir / OVERRIDE / "by hand/c.erf"

    def files() -> list[str]:
        return sorted(
            p.relative_to(user_dir).as_posix() for p in user_dir.rglob("*") if p.is_file()
        )

    async def apply(pilot, app) -> None:
        await pilot.press("a", "y")
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def unapply(pilot, app, answer: str) -> None:
        await pilot.press("x")
        await until(pilot, lambda: isinstance(app.screen, UnapplyScreen))
        await pilot.press(answer)
        await app.workers.wait_for_complete()
        await pilot.pause()

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("x")  # nothing applied, nothing foreign
        await app.workers.wait_for_complete()
        assert [n.message for n in app._notifications][-1] == "Nothing is applied"

        await apply(pilot, app)
        manual.parent.mkdir(parents=True)
        manual.write_text("installed by hand")
        (user_dir / OVERRIDE / "one/a.erf").write_text("edited")

        await pilot.press("x")
        await until(pilot, lambda: isinstance(app.screen, UnapplyScreen))
        text = app.screen.message
        assert "1 file(s) it has no record of" in text and "by hand/c.erf" in text
        assert "1 applied file(s) that were changed since" in text
        await pilot.press("enter")  # enter must not confirm something that cannot be undone
        assert isinstance(app.screen, UnapplyScreen)
        await pilot.press("n")
        await app.workers.wait_for_complete()
        assert len(files()) == 3

        # "o": only what dao-modman applied and nobody changed
        await unapply(pilot, app, "o")
        assert files() == [f"{OVERRIDE}/by hand/c.erf", f"{OVERRIDE}/one/a.erf"]
        assert "kept 1 modified" in [n.message for n in app._notifications][-1]

        # "y": everything, also when nothing of ours is applied any more
        await unapply(pilot, app, "y")
        assert files() == []
        assert (user_dir / OVERRIDE).is_dir()
        assert "2 that dao-modman did not manage" in [n.message for n in app._notifications][-1]

        await apply(pilot, app)  # and a fresh apply is complete again
        assert files() == [f"{OVERRIDE}/one/a.erf", f"{OVERRIDE}/one/b.erf"]


async def test_footer_fits_and_question_mark_lists_every_key(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from textual.widgets import Footer

    from dao_modman.tui.app import KEYS
    from dao_modman.tui.screens import KeysScreen

    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(80, 30)) as pilot:
        footer = app.query_one(Footer)
        await until(pilot, lambda: len(footer.query("FooterKey")) > 0)
        await pilot.pause()
        right_edge = max(child.region.x + child.region.width for child in footer.children)
        assert right_edge <= footer.region.width == 80  # nothing is cut off
        shown = " ".join(str(key.render()) for key in footer.query("FooterKey"))
        assert shown.split() == (
            [
                "space",
                "On/off",
                "K/J",
                "Move",
                "n",
                "Nexus",
                "a",
                "Apply",
                "x",
                "Unapply",
                "l",
                "Launch",
                "?",
                "Keys",
                "q",
                "Quit",
            ]
        )
        assert "Rename" not in shown and "palette" not in shown

        await pilot.press("g")  # a key that is no longer in the footer still works
        assert isinstance(app.screen, GroupScreen)
        await pilot.press("escape")

        await pilot.press("question_mark")
        assert isinstance(app.screen, KeysScreen)
        text = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
        for key in KEYS:  # every command is listed, whether or not the footer has it
            assert key.help in text, key.help
        sections = [str(label.render()) for label in app.screen.query(".section")]
        assert sections == ["Mod list", "Mod contents", "Nexus", "Game", "App"]
        await pilot.press("escape")
        assert not isinstance(app.screen, KeysScreen)
        await pilot.press("question_mark", "question_mark")  # ? closes it as well
        assert not isinstance(app.screen, KeysScreen)


async def test_tools_menu_shows_the_character_creator_report(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from dao_modman.tui.screens import TextScreen

    cfg = '<morph_config><hairs><resource name="{}" /></hairs></morph_config>'
    Library(dirs).import_archive(
        make_zip(tmp_path / "Hairs.zip", {"chargenmorphcfg.xml": cfg.format("hm_har_x_0")})
    )
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t", "end", "up", "enter")  # the report, above the update check
        await until(pilot, lambda: isinstance(app.screen, TextScreen))
        text = app.screen.text
        assert "#1 Hairs" in text and "hairs: hm_har_x_0" in text
        await pilot.press("escape")
        assert not isinstance(app.screen, TextScreen)


async def test_item_list_of_a_mod(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    library.import_archive(
        make_zip(
            tmp_path / "Armory.zip",
            {
                "blade.uti": make_uti("blade", text="Blade of the Mod"),
                "odd.uti": make_uti("odd"),
                "armory_en-us.tlk": make_tlk(tmp_path / "t.tlk", {7: "Zebra Shield"}).read_bytes(),
                "shield.uti": make_uti("shield", strref=7),
            },
        )
    )
    library.import_archive(make_zip(tmp_path / "Plain.zip", {"a.erf": "no items here"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("L")
        await until(pilot, lambda: isinstance(app.screen, ItemsScreen))
        screen = app.screen
        assert screen.title_text == "3 item(s) added by Armory"
        text = lambda: str(screen.query_one("#items", Static).render())  # noqa: E731
        assert text().splitlines() == ["Blade of the Mod", "odd.uti (no name)", "Zebra Shield"]
        await pilot.press("f")
        assert text().splitlines() == [
            "Blade of the Mod   blade.uti",
            "odd.uti (no name)",
            "Zebra Shield       shield.uti",
        ]
        await pilot.press("f")
        assert "blade.uti" not in text()
        # with the kinds of items known, they are a column of their own
        screen.items = [replace(item, type="Dagger" if item.named else "") for item in screen.items]
        await pilot.press("f")
        assert text().splitlines()[0] == "Blade of the Mod   Dagger  blade.uti"
        await pilot.press("f")
        assert text().splitlines() == [
            "Blade of the Mod   Dagger",
            "odd.uti (no name)",
            "Zebra Shield       Dagger",
        ]
        await pilot.press("escape")

        await pilot.press("down", "L")  # a mod without items only says so
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ItemsScreen)
        assert any("Plain has no items" in n.message for n in app._notifications)


async def test_filter_by_name_or_group(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    for name in ("Iron Armor", "Fancy Hair", "Steel Sword", "Armored Dog"):
        library.import_archive(make_zip(tmp_path / f"{name}.zip", {"a.erf": name}))
    library.profile.entry("fancy-hair").group = "Looks"
    library.profile.entry("steel-sword").group = "ARMORY"
    library.save_profile()
    app = make_app(tmp_path, dirs, user_dir)

    def shown() -> list[tuple[str, str]]:
        return [(row[0], row[2]) for row in rows(app)]

    async with app.run_test(size=(140, 40)) as pilot:
        box = app.query_one("#filter", Input)
        assert not box.display and len(shown()) == 4

        await pilot.press("slash", *"arMO")  # by name and by group, whatever the case
        assert box.display and app.focused is box
        assert shown() == [("1", "Iron Armor"), ("3", "Steel Sword"), ("4", "Armored Dog")]
        assert "Filter: arMO (3 of 4)" in str(app.query_one("#status", Static).render())

        await pilot.press(*"ry", "q", "a", "x")  # letters are text here, not commands
        assert box.value == "arMOryqax" and shown() == []
        assert not isinstance(app.screen, ConfirmScreen)
        await pilot.press(*["backspace"] * 3)
        assert shown() == [("3", "Steel Sword")]
        await pilot.press(*["backspace"] * 2)

        await pilot.press("enter")  # back to the list, the filter stays
        assert app.focused is app.query_one("#mods") and box.display
        assert app.current_mod().name == "Steel Sword"  # the selection survives the typing
        await pilot.press("down", "space")  # acts on the row that is shown there
        assert not library_entry(app, "armored-dog").enabled
        assert (
            library_entry(app, "fancy-hair").enabled and library_entry(app, "steel-sword").enabled
        )
        assert app.current_mod().name == "Armored Dog"

        await pilot.press("K")  # no moving while rows are hidden
        assert [entry.id for entry in app.library.profile.mods][-1] == "armored-dog"
        assert any("Clear the filter" in n.message for n in app._notifications)

        await pilot.press("escape")  # everything again, the selection kept
        assert not box.display and box.value == "" and len(shown()) == 4
        assert app.current_mod().name == "Armored Dog"
        assert "Filter" not in str(app.query_one("#status", Static).render())
        await pilot.press("K")
        assert [entry.id for entry in app.library.profile.mods][-2] == "armored-dog"

        await pilot.press("slash", "enter")  # an empty filter is none
        assert not box.display and len(shown()) == 4

        # a mod the filter would hide is shown when it arrives
        await pilot.press("slash", *"hair", "enter")
        assert len(shown()) == 1
        new = app.library.import_archive(make_zip(tmp_path / "Boots.zip", {"b.erf": "b"}))
        app.refresh_table(select=new.id)
        await pilot.pause()
        assert app.filter_text == "" and len(shown()) == 5 and app.current_mod().name == "Boots"
        assert not box.display


def library_entry(app: ModManApp, mod_id: str):
    return app.library.profile.entry(mod_id)


async def test_tools_menu_sets_the_texture_size_limit(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    file = tmp_path / "config.toml"
    file.write_text('# keep me\nterminal = "kitty"\n')
    found = {"game_user_dir": user_dir, "game_install_dir": tmp_path}  # no setup wizard
    app = ModManApp(Config(dirs=dirs, config_file=file, **found))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t")
        labels = [str(option.prompt) for option in app.screen.query_one(OptionList).options]
        entry = next(i for i, label in enumerate(labels) if "size of textures" in label)
        assert labels[entry].endswith("currently 4K (4096 px)")
        await pilot.press(*["down"] * entry, "enter")
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert [str(o.prompt) for o in options.options] == [
            "No limit", "1K (1024 px)", "2K (2048 px)", "4K (4096 px)",
        ]  # fmt: skip
        assert options.highlighted == 3  # the current value
        await pilot.press("up", "enter")
        await pilot.pause()
        assert app.config.max_texture_size == 2048
        assert file.read_text() == '# keep me\nterminal = "kitty"\nmax_texture_size = 2048\n'
        assert any("2K (2048 px)" in n.message for n in app._notifications)

        await pilot.press("t", *["down"] * entry, "enter")
        await pilot.pause()
        await pilot.press("home", "enter")  # no limit
        await pilot.pause()
        assert app.config.max_texture_size == 0 and "max_texture_size = 0" in file.read_text()

        await pilot.press("t", *["down"] * entry, "enter")
        await pilot.pause()
        await pilot.press("escape")  # cancelled: nothing changes
        assert app.config.max_texture_size == 0


async def test_v_shows_one_mods_conflicts_and_the_tool_all(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from dao_modman.tui.screens import ConflictsScreen

    library = Library(dirs)
    files = {
        "Alpha": {"robe.dds": "a", "chargenmorphcfg.xml": "<morph_config><hairs/></morph_config>"},
        "Beta": {"robe.dds": "b", "sword.dds": "b"},
        "Gamma": {"sword.dds": "c", "chargenmorphcfg.xml": "<morph_config><hairs/></morph_config>"},
        "Delta": {"alone.erf": "d"},
    }
    for name, content in files.items():
        library.import_archive(make_zip(tmp_path / f"{name}.zip", content))
    app = make_app(tmp_path, dirs, user_dir)

    def sections() -> list[str]:
        return [str(node.label) for node in app.screen.query_one(Tree).root.children]

    robe, sword = "#1 Alpha  <  #2 Beta   (1 file)", "#2 Beta  <  #3 Gamma   (1 file)"
    merged = "Merged (no mod loses)"
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("v")  # Alpha: its conflict with Beta, and the merge it is part of
        assert isinstance(app.screen, ConflictsScreen)
        title = str(app.screen.query_one(".title", Label).render())
        assert title.startswith("Conflicts of Alpha")
        assert sections() == [robe, merged]
        await pilot.press("escape")

        await pilot.press("down", "v")  # Beta is in both conflicts, in no merge
        assert sections() == [robe, sword]
        await pilot.press("escape", "down", "v")  # Gamma
        assert sections() == [sword, merged]
        await pilot.press("escape", "down", "v")  # Delta: nothing to show
        assert not isinstance(app.screen, ConflictsScreen)

        await pilot.press("t")  # the whole library, from the tools menu
        labels = [str(option.prompt) for option in app.screen.query_one(OptionList).options]
        await pilot.press(*["down"] * labels.index("Show all conflicts"), "enter")
        await pilot.pause()
        assert isinstance(app.screen, ConflictsScreen)
        assert str(app.screen.query_one(".title", Label).render()).startswith("Conflicts (")
        assert sections() == [robe, sword, merged]


async def test_all_conflicts_tool_without_conflicts(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    Library(dirs).import_archive(make_zip(tmp_path / "Alpha.zip", {"a.erf": "a"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t")
        labels = [str(option.prompt) for option in app.screen.query_one(OptionList).options]
        await pilot.press(*["down"] * labels.index("Show all conflicts"), "enter")
        await pilot.pause()
        assert [n.message for n in app._notifications][-1] == "No conflicts"


def choose_tool(app: ModManApp, tool: str) -> None:
    """Highlight a tools menu entry by its id."""
    ids = [key for key, _label in app.screen.choices]
    app.screen.query_one(OptionList).highlighted = ids.index(tool)


@pytest.fixture
def pypi(monkeypatch: pytest.MonkeyPatch) -> dict:
    """PyPI's newest version and this installation, as the tests set them."""
    from dao_modman.core import updates

    state: dict = {"latest": "0.5.0", "installed": "0.5.0", "started": []}

    async def newest(transport: object = None) -> str | None:
        return state["latest"]

    monkeypatch.setattr(updates, "latest", newest)
    monkeypatch.setattr(updates, "installed", lambda: state["installed"])
    command = ["uv", "tool", "upgrade", "dao-modman"]
    monkeypatch.setattr(updates, "install_kind", lambda: updates.Install(updates.UV_TOOL, command))
    monkeypatch.setattr(updates, "start_update", state["started"].append)
    return state


def messages(app: ModManApp) -> list[str]:
    return [n.message for n in app._notifications]


async def test_tools_menu_checks_for_a_new_version(
    tmp_path: Path, dirs: Dirs, user_dir: Path, pypi: dict
) -> None:
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        assert not any("is available" in m for m in messages(app))  # up to date: silent

        await pilot.press("t")
        choose_tool(app, "update")
        await pilot.press("enter")
        await until(pilot, lambda: "dao-modman 0.5.0 is the newest version" in messages(app))

        pypi["latest"] = "0.6.0"
        await pilot.press("t")
        choose_tool(app, "update")
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "dao-modman 0.6.0 is available (this is 0.5.0)" in app.screen.message
        assert "uv tool upgrade dao-modman" in app.screen.message
        await pilot.press("y")  # update: dao-modman quits, the update runs after it
        await until(pilot, lambda: pypi["started"] != [])
    assert pypi["started"] == [["uv", "tool", "upgrade", "dao-modman"]]


async def test_new_version_is_reported_at_start(
    tmp_path: Path, dirs: Dirs, user_dir: Path, pypi: dict
) -> None:
    pypi["latest"] = "0.6.0"
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await until(pilot, lambda: any("0.6.0 is available" in m for m in messages(app)))

    pypi["installed"] = "0.5.1.dev2+g1234567"  # running from source: not checked at start
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        assert not any("is available" in m for m in messages(app))
        await pilot.press("t")
        choose_tool(app, "update")
        await pilot.press("enter")
        await until(pilot, lambda: any("runs from source" in m for m in messages(app)))


async def test_adopt_files_installed_by_hand(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    hand = user_dir / OVERRIDE / "Hand Mod/hand.erf"
    hand.parent.mkdir(parents=True)
    hand.write_text("by hand")
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert any(
            "1 file(s) in the mod directories are not managed" in n.message
            for n in app._notifications
        )
        await pilot.press("t")
        choose_tool(app, "adopt")
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "Hand Mod (files, 1 file(s))" in app.screen.message
        await pilot.press("y")
        await until(
            pilot,
            lambda: isinstance(app.screen, ConfirmScreen) and "Apply now" in app.screen.message,
        )
        assert [mod.name for mod in app.library.mods.values()] == ["Hand Mod"]
        assert not hand.exists() and app.foreign == []

        await pilot.press("y")  # apply now: the usual apply dialog follows
        await until(
            pilot,
            lambda: (
                isinstance(app.screen, ConfirmScreen)
                and "1 file(s) to write, 0 to remove, 0 unchanged" in app.screen.message
            ),
        )
        await pilot.press("y")
        await app.workers.wait_for_complete()
        # installed again from the library, in the mod's own folder
        assert (user_dir / OVERRIDE / "hand-mod/hand.erf").read_text() == "by hand"


async def test_apply_confirmation_fits_a_small_terminal(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    library = Library(dirs)
    for index in range(12):  # each mod overwrites 5 files of the one before: 11 groups
        files = {f"p{part}_{i}.gda": str(index) for part in (index - 1, index) for i in range(5)}
        if index < 2:
            files["chargenmorphcfg.xml"] = "<morph_config><hairs/></morph_config>"
        library.import_archive(make_zip(tmp_path / f"Mod {index:02}.zip", files))
    for index in range(6):
        archive = make_dazip(tmp_path / f"a{index}.dazip", f"addin{index}", f"AddIn {index}")
        library.import_archive(archive)
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("a")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        message = app.screen.message
        assert "55 file(s) overwritten by a later mod (v shows them)" in message
        assert "chargenmorphcfg.xml merged from 2 mods" in message
        assert "\n6 AddIns" in message and "AddIn 0" not in message
        assert "Mod 01" not in message and ".gda" not in message  # no details
        await pilot.press("n")

        # a message longer than the screen scrolls; the keys stay visible
        app.push_screen(ConfirmScreen("\n".join(f"line {i}" for i in range(60))))
        await pilot.pause()
        keys, dialog = app.screen.query_one(".keys"), app.screen.query_one(".dialog")
        assert keys.region.bottom <= dialog.content_region.bottom
        assert app.screen.query_one(".message").max_scroll_y > 0
        await pilot.press("n")


async def test_apply_names_the_files_it_left_alone(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "mine.zip", {"a.erf": "mine", "b.erf": "b"}))
    taken = user_dir / OVERRIDE / "mine/a.erf"
    taken.parent.mkdir(parents=True)
    taken.write_text("by hand")
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert "overwritten by unmanaged files" in rows(app)[0][-1]  # the hand-installed copy
        await pilot.press("a")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "1 file(s) overwritten by a later mod" in app.screen.message
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert any(
            "left 1 existing file(s) untouched: a.erf (t → Adopt takes them over)" in n.message
            for n in app._notifications
        )
    assert taken.read_text() == "by hand"


async def test_notes_show_what_apply_would_change(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "old.zip", {"a.erf": "a"}))
    deploy.apply(deploy.build_plan(library), user_dir, dirs.deployed_file)
    library.import_archive(make_zip(tmp_path / "new.zip", {"b.erf": "b"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert [row[-1] for row in rows(app)] == ["", "not applied"]
        assert "Applied: yes, 1 mod(s) to apply" in str(app.query_one("#status", Static).render())
        await pilot.press("space")  # disable the applied one
        assert [row[-1] for row in rows(app)] == ["still applied", "not applied"]

        await pilot.press("a")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "1 file(s) to write, 1 to remove, 0 unchanged" in app.screen.message
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert [row[-1] for row in rows(app)] == ["", ""]
        assert "Applied: yes, up to date" in str(app.query_one("#status", Static).render())
        assert any(
            "wrote 1 file(s), removed 1, 0 unchanged" in n.message for n in app._notifications
        )


async def test_no_notice_for_ignored_files_only(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    readme = user_dir / OVERRIDE / "some-mod/readme.txt"
    readme.parent.mkdir(parents=True)
    readme.write_text("left behind")
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)):
        assert app.foreign  # it is not ours ...
        assert not any("not managed" in n.message for n in app._notifications)  # ... but no notice


def multi_file_handler(tmp_path: Path, premium: bool):
    """Nexus mod 7 with a main file, an optional one and one that is already installed."""
    listing = {
        "files": [
            {"file_id": 22, "file_name": "Main-7.zip", "name": "Main", "category_name": "MAIN"},
            {"file_id": 23, "file_name": "Addon-7.zip", "name": "Addon",
             "category_name": "OPTIONAL"},
            {"file_id": 24, "file_name": "Extra-7.zip", "name": "Extra",
             "category_name": "MISCELLANEOUS"},
        ],
        "file_updates": [],
    }  # fmt: skip
    payloads = {
        file_id: make_zip(tmp_path / f"payload{file_id}.zip", {f"f{file_id}.erf": "x"})
        for file_id in (22, 23)
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx2.Response(
                200, content=payloads[int(path.strip("/").split(".")[0])].read_bytes()
            )
        if path.endswith("/users/validate.json"):
            return httpx2.Response(200, json={"name": "seb", "is_premium": premium})
        if path.endswith("/mods/7.json"):
            return httpx2.Response(200, json={"mod_id": 7, "name": "My Mod"})
        if path.endswith("/mods/7/files.json"):
            return httpx2.Response(200, json=listing)
        for file_id in payloads:
            if path.endswith(f"/files/{file_id}/download_link.json"):
                return httpx2.Response(200, json=[{"URI": f"https://cdn.example/{file_id}.zip"}])
        return httpx2.Response(404, json={"message": "nope"})

    return handler


async def look_up_mod_7(app: ModManApp, pilot) -> SelectionList:
    await app.workers.wait_for_complete()
    await pilot.press("n")
    app.screen.query_one(Input).value = "7"
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()
    return app.screen.query_one(SelectionList)


async def test_add_several_files_from_nexus(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    library.import_archive(  # file 24 is in the library already
        make_zip(tmp_path / "Extra-7.zip", {"e.erf": "e"}), nexus=NexusInfo("dragonage", 7, 24)
    )
    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx2.MockTransport(multi_file_handler(tmp_path, premium=True)),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        files = await look_up_mod_7(app, pilot)
        assert files.selected == [22]  # the only main file
        labels = [str(files.get_option_at_index(i).prompt) for i in range(files.option_count)]
        assert labels[2].endswith("(installed)")
        files.highlighted = 1
        await pilot.press("space")  # the optional one too
        assert sorted(files.selected) == [22, 23]
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
    installed = {mod.nexus.file_id for mod in Library(dirs).mods.values() if mod.nexus is not None}
    assert installed == {22, 23, 24}  # one library mod per file


async def test_free_account_opens_download_pages(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx2.MockTransport(multi_file_handler(tmp_path, premium=False)),
    )
    opened: list[str] = []
    app.open_url = lambda url, **_: opened.append(url)
    async with app.run_test(size=(140, 40)) as pilot:
        files = await look_up_mod_7(app, pilot)
        files.highlighted = 1
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()
        assert opened == [nexus.download_page(7, 22), nexus.download_page(7, 23)]
        assert any("Opened 2 download page(s)" in n.message for n in app._notifications)
    assert Library(dirs).mods == {}  # nothing downloaded directly


def nexus_files(dirs: Dirs) -> list[int]:
    return sorted(mod.nexus.file_id for mod in Library(dirs).mods.values() if mod.nexus)


async def test_adding_a_nexus_file_again_asks_first(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    Library(dirs).import_archive(
        make_zip(tmp_path / "Main-7.zip", {"m.erf": "m"}),
        version="1.0",
        nexus=NexusInfo("dragonage", 7, 22),
    )
    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx2.MockTransport(multi_file_handler(tmp_path, premium=True)),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        files = await look_up_mod_7(app, pilot)
        assert files.selected == []  # the main file is installed: not preselected
        await pilot.press("space", "down", "space", "enter")  # it and the optional file
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert app.screen.message.startswith("Main-7 1.0\nis already in the library.")
        await pilot.press("n")  # only the optional file is installed
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert nexus_files(dirs) == [22, 23]

        files = await look_up_mod_7(app, pilot)
        await pilot.press("space", "enter", "y")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert nexus_files(dirs) == [22, 22, 23]


async def test_link_for_a_file_in_the_library_asks_once(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    Library(dirs).import_archive(
        make_zip(tmp_path / "Main-7.zip", {"m.erf": "m"}), nexus=NexusInfo("dragonage", 7, 22)
    )
    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx2.MockTransport(multi_file_handler(tmp_path, premium=False)),
    )
    app.open_url = lambda url, **_: None
    link = "nxm://dragonage/mods/7/files/22?key=k&expires=99"
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        assert app.receive_link(link) is None
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await app.workers.wait_for_complete()
        assert nexus_files(dirs) == [22]

        # confirmed in the add dialog: the link that comes back from the browser just installs
        await look_up_mod_7(app, pilot)
        await pilot.press("enter", "y")
        await pilot.pause()
        app.receive_link(link)
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert nexus_files(dirs) == [22, 22]

        app.receive_link(link)  # asked again next time
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")


@pytest.mark.parametrize("excluded", [False, True])
async def test_importing_an_archive_again_asks_first(
    tmp_path: Path, dirs: Dirs, user_dir: Path, excluded: bool
) -> None:
    archive = make_zip(tmp_path / "mod.zip", {"a.erf": "a", "b.erf": "b", "docs/c.gda": "c"})
    other = make_zip(tmp_path / "other.zip", {"b.erf": "b"})
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        for path in (archive, other):  # new archives: no question
            app.import_file(path)
            await app.workers.wait_for_complete()
        assert len(app.library.mods) == 2
        if excluded:
            first = app.library.mods["mod"]
            first.ops = [PathOp("exclude", f"{OVERRIDE}/mod/b.erf")]
            app.library.save_mod(first)
            targets = [f.target for f in deploy.build_plan(app.library).files]
            assert f"{OVERRIDE}/mod/b.erf" not in targets and f"{OVERRIDE}/mod/a.erf" in targets

        app.import_file(archive)
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert app.screen.message.startswith("mod\nis already in the library.")
        await pilot.press("n")
        await app.workers.wait_for_complete()
        assert len(app.library.mods) == 2

        app.import_file(archive)
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert sorted(app.library.mods) == ["mod", "mod-2", "other"]

    # the second copy wins on every file, also on one the first copy excludes: it starts
    # without the first one's operations. Identical copies are no conflict.
    plan = deploy.build_plan(app.library)
    deployed = {
        f.target.rpartition("/")[2]: f.mod_id for f in plan.files if f.mod_id.startswith("mod")
    }
    assert deployed == {"a.erf": "mod-2", "b.erf": "mod-2", "c.gda": "mod-2"}
    assert plan.conflicts == [] and plan.overwrites() == {}
    assert app.library.mods["mod-2"].ops == []


async def test_startup_shows_progress_and_blocks_keys(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    gate = threading.Event()
    scan = deploy.unmanaged

    def slow_scan(*args, **kwargs):
        gate.wait(5)  # the game directories take their time
        return scan(*args, **kwargs)

    monkeypatch.setattr(deploy, "unmanaged", slow_scan)
    app = make_app(tmp_path, dirs, user_dir)
    async with _run_test(app, size=(140, 40)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ProgressScreen))
        assert app.screen.title_text == "Starting dao-modman"
        assert "██████╗" in str(app.screen.query_one("#splash-art", Static).render())
        detail = app.screen.query_one("#work-detail", Static)
        await until(pilot, lambda: "Checking the game directories" in str(detail.render()))
        assert not app.started.is_set() and rows(app) == []
        await pilot.press("space")  # keys do nothing while starting
        gate.set()
        await app.started.wait()
        await pilot.pause()
        assert not isinstance(app.screen, ProgressScreen)
        assert [(row[1], row[2]) for row in rows(app)] == [("✓", "one")]  # not toggled


def tree_nodes(node) -> dict:
    """Every node below `node` by its path."""
    found = {}
    for child in node.children:
        found[child.data] = child
        found.update(tree_nodes(child))
    return found


async def test_path_editor_shows_ignored_files_and_opens_files(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = Library(dirs)
    mod = library.import_archive(
        make_zip(
            tmp_path / "Mod.zip",
            {"data/a.erf": "a", "data/readme.txt": "r", "setup.exe": "MZ"},
        )
    )
    opened: list[Path] = []
    monkeypatch.setattr(opener, "open_file", opened.append)
    files = library.files_dir(mod.id)
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        screen = app.screen
        tree = screen.query_one(PathTree)
        await pilot.pause()
        nodes = tree_nodes(tree.root)
        readme, erf = f"{OVERRIDE}/mod/data/readme.txt", f"{OVERRIDE}/mod/data/a.erf"
        # ignored files are shown, marked, and styled apart
        assert str(nodes[readme].label) == "readme.txt  · ignored"
        assert tree.ignored == {readme}
        assert tree.render_label(nodes[erf], Style(), Style()).plain == "\U0001f4c4 a.erf"
        folder = tree.render_label(nodes[f"{OVERRIDE}/mod/data"], Style(), Style())
        assert folder.plain == "\U0001f4c2 data"

        # o opens the file's copy in the library
        tree.move_cursor(nodes[erf])
        await pilot.press("o")
        assert opened == [files / "data/a.erf"]
        # a folder cannot be opened
        tree.move_cursor(nodes[f"{OVERRIDE}/mod/data"])
        await pilot.press("o")
        await pilot.pause()
        assert opened == [files / "data/a.erf"]
        assert any("Select a file to open" in n.message for n in app._notifications)
        # a program is only opened after asking
        tree.move_cursor(nodes[f"{OVERRIDE}/mod/setup.exe"])
        await pilot.press("o")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        await pilot.pause()
        assert len(opened) == 1
        await pilot.press("o", "y")
        await pilot.pause()
        assert opened[-1] == files / "setup.exe"

        # an excluded folder is marked like the files in it
        screen.ops = [PathOp("exclude", f"{OVERRIDE}/mod/data")]
        screen.rebuild()
        assert tree.ignored == {f"{OVERRIDE}/mod/data", erf, readme}
        await pilot.press("escape")


async def test_startup_warns_about_a_token_file_others_can_read(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    warning = "/x/.nexus-token can be read by other users: chmod 600 '/x/.nexus-token'"
    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, token_warning=warning))
    async with app.run_test(size=(140, 40)):
        await app.started.wait()
        assert warning in [n.message for n in app._notifications]


async def test_changed_hard_link_offers_restore(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy, "_LINK", deploy.HARDLINK)
    monkeypatch.setattr(deploy, "_unlinkable", set())
    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1", "b.erf": "2"}))
    library_copy = dirs.mods / "one/files/a.erf"
    applied = user_dir / OVERRIDE / "one/a.erf"

    def asking(text: str) -> bool:
        return isinstance(app.screen, ConfirmScreen) and text in app.screen.message

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("a", "y")
        await app.workers.wait_for_complete()
        assert applied.samefile(library_copy)
        assert "(2 linked)" in [n.message for n in app._notifications][-1]

        applied.write_text("edited in the game")  # through the link: the library copy too
        await pilot.press("a", "y")
        await until(pilot, lambda: asking("hard links to the"))
        assert f"{OVERRIDE}/one/a.erf" in app.screen.message
        await pilot.press("y")  # restore from the archive
        await until(pilot, lambda: asking("back into the game"))
        assert library_copy.read_text() == "1"
        assert applied.read_text() == "edited in the game"
        await pilot.press("y")  # put the original back
        await until(pilot, lambda: asking("Apply to"))
        assert not applied.exists()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert applied.read_text() == "1" and applied.samefile(library_copy)


async def test_restore_without_the_archive_is_reported(
    tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy, "_LINK", deploy.HARDLINK)
    monkeypatch.setattr(deploy, "_unlinkable", set())
    Library(dirs).import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))
    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("a", "y")
        await app.workers.wait_for_complete()
        (user_dir / OVERRIDE / "one/a.erf").write_text("edited")
        (dirs.downloads / "one.zip").unlink()
        await pilot.press("a", "y")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert any("no longer in downloads" in n.message for n in app._notifications)
        assert not isinstance(app.screen, ConfirmScreen)  # nothing restored: no second question
