from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import pytest
from textual.widgets import DataTable, Input, OptionList, Static, Tree

from dao_modman.core.config import Config
from dao_modman.core.library import Library
from dao_modman.core.models import PathOp
from dao_modman.core.paths import Dirs
from dao_modman.tui.app import ModManApp
from dao_modman.tui.screens import ConfirmScreen, NexusAddScreen, PathOpsScreen, TextPromptScreen

from .conftest import make_zip

OVERRIDE = "packages/core/override"


def make_app(tmp_path: Path, dirs: Dirs, user_dir: Path, **kwargs) -> ModManApp:
    return ModManApp(Config(dirs=dirs, game_user_dir=user_dir), **kwargs)


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
        assert isinstance(app.screen, TextPromptScreen)
        await pilot.press(*"Hair", "enter")
        assert rows(app)[0][4] == "Hair"

    profile = Library(dirs).profile
    assert [(e.id, e.enabled, e.group) for e in profile.mods] == [
        ("one", False, "Hair"),
        ("two", True, ""),
    ]


async def test_import_edit_paths_apply_unapply(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    archive = make_zip(tmp_path / "mod.zip", {"a.erf": "a", "docs/readme.txt": "r"})
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("i")
        app.screen.query_one(Input).value = str(archive)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [row[2] for row in rows(app)] == ["mod"]

        # remove the docs folder, rename the erf
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
        await pilot.press("r")
        select(f"{OVERRIDE}/mod/a.erf")
        await pilot.press("m")
        app.screen.query_one(Input).value = f"{OVERRIDE}/renamed/b.erf"
        await pilot.press("enter")
        await pilot.pause()
        assert len(screen.ops) == 2
        await pilot.press("s")
        await pilot.pause()
        assert rows(app)[0][7] == "2"

        await pilot.press("a")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert (user_dir / OVERRIDE / "renamed/b.erf").read_text() == "a"
        assert not (user_dir / OVERRIDE / "mod").exists()

        await pilot.press("x", "y")
        await app.workers.wait_for_complete()
        assert not (user_dir / "packages").exists()

    assert Library(dirs).mods["mod"].ops == [
        PathOp("remove", f"{OVERRIDE}/mod/docs"),
        PathOp("rename", f"{OVERRIDE}/mod/a.erf", f"{OVERRIDE}/renamed/b.erf"),
    ]


async def test_path_ops_cancel_and_undo(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    Library(dirs).import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a"}))
    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e", "down", "r")
        assert len(app.screen.ops) == 1
        await pilot.press("u")
        assert app.screen.ops == []
        await pilot.press("down", "r", "escape")
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

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=payload_path.read_bytes())
        if path.endswith("/users/validate.json"):
            return httpx.Response(200, json={"name": "seb", "is_premium": True})
        if path.endswith("/mods/7.json"):
            return httpx.Response(200, json={"mod_id": 7, "name": "My Mod"})
        if path.endswith("/mods/7/files.json"):
            return httpx.Response(200, json=listing)
        if path.endswith("/files/22/download_link.json"):
            return httpx.Response(200, json=[{"URI": "https://cdn.example/x.zip"}])
        return httpx.Response(404, json={"message": "nope"})

    app = ModManApp(
        Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret"),
        transport=httpx.MockTransport(handler),
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
    bundled = make_exe(tmp_path / "bundled.exe", large_address_aware=True)
    monkeypatch.setattr(laa, "bundled_exe", lambda: bundled)

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 3
        await pilot.press("enter")  # first entry: apply the patch
        await pilot.pause()
        assert laa.is_laa(exe)

        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 2  # the patch entry is gone
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
        await pilot.press("t", "end", "enter")  # the console entry is the last one
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

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            await release.wait()  # keep the downloads running until the test has looked
            return httpx.Response(200, content=payload)
        if path.endswith("/users/validate.json"):
            return httpx.Response(200, json={"name": "seb", "is_premium": False})
        if path.endswith("/mods/7.json"):
            return httpx.Response(200, json={"mod_id": 7, "name": "My Mod"})
        if path.endswith("/mods/7/files.json"):
            return httpx.Response(200, json={"files": files, "file_updates": []})
        if path.endswith("/download_link.json") and request.url.params.get("key") == "k":
            file_id = path.split("/")[-2]
            return httpx.Response(200, json=[{"URI": f"https://cdn.example/{file_id}.zip"}])
        return httpx.Response(403, json={"message": "nope"})

    runtime = tmp_path / "run"
    runtime.mkdir()
    config = Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret", runtime_dir=runtime)
    app = ModManApp(config, transport=httpx.MockTransport(handler))

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
    assert not (runtime / ipc.SOCKET_NAME).exists()


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
        assert (runtime / ipc.SOCKET_NAME).exists()


async def test_path_tree_folding_and_filter(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    Library(dirs).import_archive(
        make_zip(
            tmp_path / "mod.zip",
            {
                "a.erf": "a",
                "docs/readme.txt": "r",
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

        # folds and the cursor position survive an operation
        await go("textures/big.dds")
        await pilot.press("r")
        assert folded() == {"docs/img"}
        assert tree.cursor_node.data == base  # "textures" is gone, so its parent
        await pilot.press("u")
        assert folded() == {"docs/img"}

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

        # operations work on the filtered tree
        await go("docs/img/Pic.dds")
        await pilot.press("r")
        assert files() == []

        # escape leaves the filter first, then the dialog
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen and not box.display
        assert files() == ["a.erf", "docs/readme.txt", "textures/big.dds"]
        await pilot.press("s")
        await pilot.pause()

    assert Library(dirs).mods["mod"].ops == [PathOp("remove", f"{base}/docs/img/Pic.dds")]


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
