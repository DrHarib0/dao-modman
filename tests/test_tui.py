from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from textual.widgets import DataTable, Input, OptionList, Static, Tree

from dao_modman.core.config import Config
from dao_modman.core.library import Library
from dao_modman.core.models import PathOp
from dao_modman.core.paths import Dirs
from dao_modman.tui.app import ModManApp
from dao_modman.tui.screens import (
    ConfirmScreen,
    GroupScreen,
    ItemsScreen,
    NexusAddScreen,
    PathOpsScreen,
)

from .conftest import make_zip
from .gamefiles import make_tlk, make_uti

OVERRIDE = "packages/core/override"


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
    archive = make_zip(tmp_path / "mod.zip", {"a.erf": "a", "docs/extra.erf": "r"})
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

        await pilot.press("x")
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
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
    bundled = make_exe(tmp_path / "bundled.exe", large_address_aware=True)
    monkeypatch.setattr(laa, "bundled_exe", lambda: bundled)

    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, game_install_dir=install_dir))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 4
        await pilot.press("enter")  # first entry: apply the patch
        await pilot.pause()
        assert laa.is_laa(exe)

        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 3  # the patch entry is gone
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
        await pilot.press("t", "end", "up", "enter")  # the console entry, above the report
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
        assert files() == ["a.erf", "docs/notes.gda", "textures/big.dds"]
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


async def test_launch_game(tmp_path: Path, dirs: Dirs, user_dir: Path, monkeypatch) -> None:
    from dao_modman.core import launch

    spawned: list[list[str]] = []
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
        assert [n.message for n in app._notifications][-1] == "Launching the game through Steam"

        app.config.game_app_id = None
        await pilot.press("l")
        assert len(spawned) == 2
        assert "game_app_id" in [n.message for n in app._notifications][-1]


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
        assert [n.message for n in app._notifications][-1] == "No conflicts"

        app.library.import_archive(make_zip(tmp_path / "Beta.zip", {"robe.dds": "b"}))
        app.refresh_table()
        assert rows(app)[0][8] == "conflict" and rows(app)[1][8] == "conflict"
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


async def test_accepting_overwrites_in_the_conflicts_dialog(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "Alpha.zip", {"a.gda": "a", "b.gda": "a"}))
    library.import_archive(make_zip(tmp_path / "Beta.zip", {"a.gda": "b", "b.gda": "b"}))

    def notes(app: ModManApp) -> list[str]:
        return [row[8] for row in rows(app)]

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert notes(app) == ["conflict", "conflict"]
        await pilot.press("v")
        tree = app.screen.query_one(Tree)

        def lines() -> list[str]:
            group = tree.root.children[0]
            return [str(group.label), *(str(child.label) for child in group.children)]

        assert lines() == ["#1 Alpha  <  #2 Beta   (2 files)", "a.gda", "b.gda"]

        # accept one file, then take it back
        await pilot.press("down", "a")
        assert lines()[1:] == ["b.gda", "a.gda   \u2713 accepted"]
        assert tree.cursor_node.data[1].name == "a.gda"  # the cursor follows the file
        await pilot.press("a")
        assert lines()[1:] == ["a.gda", "b.gda"]

        # accept everything between the two mods on the mods line
        await pilot.press("up", "a")
        assert lines() == [
            "#1 Alpha  <  #2 Beta   (2 files, accepted)",
            "a.gda   \u2713 accepted",
            "b.gda   \u2713 accepted",
        ]
        assert [(o.mod, o.files) for o in app.library.mods["beta"].overwrites] == [("alpha", [])]
        # a single file cannot be carved out of a whole-mod entry
        await pilot.press("down", "a")
        assert lines()[1] == "a.gda   \u2713 accepted"

        await pilot.press("escape")
        await pilot.pause()
        assert notes(app) == ["", "overwrites Alpha"]  # the rule is visible on the winner

        # reversing the load order turns the accepted overwrite into an error
        await pilot.press("J")
        assert notes(app) == ["wrong order, overwrites Alpha", "wrong order"]
        await pilot.press("v", "a")  # cannot be accepted away
        tree = app.screen.query_one(Tree)
        assert lines()[0].startswith("#1 Beta  <  #2 Alpha")
        assert "wrong order: #1 Beta is set to overwrite #2 Alpha" in lines()[1]
        await pilot.press("escape", "a")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "wrong load order" in app.screen.message

    assert [(o.mod, o.files) for o in Library(dirs).mods["beta"].overwrites] == [("alpha", [])]


async def test_unused_overwrite_rules_can_be_dropped(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    from dao_modman.core.models import Overwrite
    from dao_modman.tui.screens import ConflictsScreen

    library = Library(dirs)
    mod = library.import_archive(make_zip(tmp_path / "Alpha.zip", {"a.gda": "a"}))
    mod.overwrites = [Overwrite("removed-mod", ["a.gda"])]
    library.save_mod(mod)

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("v")  # opens although nothing conflicts
        assert isinstance(app.screen, ConflictsScreen)
        section = app.screen.query_one(Tree).root.children[0]
        assert [str(leaf.label) for leaf in section.children] == [
            "#1 Alpha may overwrite removed-mod: a.gda"
        ]
        await pilot.press("down", "a")
        assert list(app.screen.query_one(Tree).root.children) == []
    assert Library(dirs).mods["alpha"].overwrites == []


async def test_notes_show_overwrite_rules(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    from dao_modman.core.models import Overwrite

    library = Library(dirs)
    for name in ("Alpha", "Beta", "Gamma"):
        library.import_archive(make_zip(tmp_path / f"{name}.zip", {f"{name}.erf": name}))
    library.mods["beta"].overwrites = [Overwrite("alpha", ["x.gda"]), Overwrite("alpha", ["y.gda"])]
    library.mods["gamma"].overwrites = [Overwrite("alpha"), Overwrite("removed-mod")]
    for mod in library.mods.values():
        library.save_mod(mod)

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)):
        assert [row[8] for row in rows(app)] == ["", "overwrites Alpha", "overwrites 2 mods"]
        app.library.mods["gamma"].overwrites.pop(0)
        app.refresh_table()
        assert rows(app)[2][8] == "overwrites removed-mod (missing)"


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
        assert [row[8] for row in rows(app)] == ["", "options"]

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
        assert rows(app)[1][8] == "options (1 changed)"

        # the path editor shows the deployed name together with the variant behind it
        await pilot.press("e")
        tree = app.screen.query_one(Tree)

        def leaves(node) -> list[str]:
            found: list[str] = []
            for child in node.children:
                found += leaves(child) if child.children else [str(child.label)]
            return found

        assert sorted(leaves(tree.root)) == [
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
        progress("Copying files", 1, 2)
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
        assert str(app.screen.query_one("#work-detail", Static).render()) == "Copying files: 1 / 2"

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
        await pilot.press("t", "end", "enter")  # the report is the last tool
        await until(pilot, lambda: isinstance(app.screen, TextScreen))
        text = app.screen.text
        assert "#1 Hairs" in text and "hairs: hm_har_x_0" in text
        await pilot.press("escape")
        assert not isinstance(app.screen, TextScreen)


async def test_installing_a_known_mod_offers_its_predefined_operations(
    tmp_path: Path, dirs: Dirs, user_dir: Path
) -> None:
    payload = make_zip(tmp_path / "payload.zip", {"fonts.erf": "f", "readme.txt": "r"}).read_bytes()
    files = [
        {"file_id": 5571, "file_name": "fix.zip", "name": "Fix", "category_name": "MAIN"},
        {"file_id": 9999, "file_name": "other.zip", "name": "Other", "category_name": "OPTIONAL"},
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "cdn.example":
            return httpx2.Response(200, content=payload)
        if path.endswith("/users/validate.json"):
            return httpx2.Response(200, json={"name": "seb", "is_premium": True})
        if path.endswith("/mods/1110.json"):
            return httpx2.Response(200, json={"mod_id": 1110, "name": "Console Fonts"})
        if path.endswith("/mods/1110/files.json"):
            return httpx2.Response(200, json={"files": files, "file_updates": []})
        if path.endswith("/download_link.json"):
            return httpx2.Response(200, json=[{"URI": f"https://cdn.example{path}.zip"}])
        return httpx2.Response(404, json={"message": "nope"})

    config = Config(dirs=dirs, game_user_dir=user_dir, nexus_token="secret")
    app = ModManApp(config, transport=httpx2.MockTransport(handler))
    wanted = PathOp("rename", f"{OVERRIDE}/console-fonts/fonts.erf", "packages/core/data/fonts.erf")
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()

        app.install(1110, 5571)  # a file with a preset: asked, declined
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        text = app.screen.message
        assert "Predefined path operations exist for Console Fonts" in text
        assert "only reads fonts.erf from packages/core/data" in text and wanted.describe() in text
        await pilot.press("n")
        await app.workers.wait_for_complete()
        assert app.library.mods["console-fonts"].ops == []

        # catching up in the path editor
        await pilot.press("e")
        screen = app.screen
        hint = str(screen.query_one("#preset", Static).render())
        assert hint.startswith("1 predefined operation not applied, p adds: The game only reads")
        await pilot.press("p")
        assert screen.ops == [wanted] and not screen.query_one("#preset").display
        await pilot.press("p")  # nothing left to add
        assert screen.ops == [wanted]
        await pilot.press("u")  # and it can be undone like any other operation
        assert screen.ops == [] and screen.query_one("#preset").display
        await pilot.press("p", "s")
        await pilot.pause()
        assert app.library.mods["console-fonts"].ops == [wanted]

        app.install(1110, 9999)  # another file of the same mod has no preset: no question
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)

    # accepted at install: the operations are saved with the mod
    for mod_dir in list(dirs.mods.iterdir()):
        Library(dirs).remove(mod_dir.name)
    app = ModManApp(config, transport=httpx2.MockTransport(handler))
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        app.install(1110, 5571)
        await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await app.workers.wait_for_complete()
        assert rows(app)[0][7] == "1"  # the Ops column
    assert Library(dirs).mods["console-fonts"].ops == [wanted]


async def test_broken_user_presets_are_reported(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    user_file = tmp_path / "presets.toml"
    user_file.write_text("not toml [")
    app = ModManApp(Config(dirs=dirs, game_user_dir=user_dir, presets_file=user_file))
    async with app.run_test(size=(140, 40)):
        assert any(str(user_file) in n.message for n in app._notifications)
        assert (1825, 9280) in app.presets.by_file


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
