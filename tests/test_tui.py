from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
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


async def test_toggle_move_and_group(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "1"}))
    library.import_archive(make_zip(tmp_path / "two.zip", {"b.erf": "2"}))

    app = make_app(tmp_path, dirs, user_dir)
    async with app.run_test(size=(140, 40)) as pilot:
        assert [row[2] for row in rows(app)] == ["one", "two"]
        assert "no API token" in str(app.query_one("#status", Static).render())

        await pilot.press("space")
        assert rows(app)[0][1] == ""
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
        assert app.screen.query_one(OptionList).option_count == 2
        await pilot.press("enter")  # first entry: apply the patch
        await pilot.pause()
        assert laa.is_laa(exe)

        await pilot.press("t")
        assert app.screen.query_one(OptionList).option_count == 1  # only restore is left
        await pilot.press("enter")
        await pilot.pause()
        assert not laa.is_laa(exe)
