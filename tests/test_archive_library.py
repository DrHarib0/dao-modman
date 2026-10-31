from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import py7zr
import pytest

from dao_modman.core import archive as archives
from dao_modman.core import metadata
from dao_modman.core.library import Library, LibraryError, slugify
from dao_modman.core.models import KIND_DAZIP, KIND_MIXED, KIND_OVERRIDE

from .conftest import make_dazip, make_zip, symlink


def test_hashes(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    assert archives.file_hashes(path) == (
        hashlib.sha256(b"hello").hexdigest(),
        hashlib.md5(b"hello").hexdigest(),
    )


def test_extract_zip(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {"dir/a.txt": "a", "b\\c.txt": "c"})
    archives.extract(archive, tmp_path / "out")
    assert archives.list_files(tmp_path / "out") == ["b/c.txt", "dir/a.txt"]


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a/../../evil.txt"])
def test_extract_zip_rejects_traversal(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zipfile.ZipInfo(name), "x")
    with pytest.raises(archives.ArchiveError):
        archives.extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_extract_7z(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub/a.txt").write_text("a")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as sz:
        sz.writeall(src, "mod")
    archives.extract(archive, tmp_path / "out")
    assert archives.list_files(tmp_path / "out") == ["mod/sub/a.txt"]


def _rar_binary() -> str | None:
    """rar on PATH, else WinRAR's Rar.exe next to the UnRAR.exe that is found."""
    if found := shutil.which("rar"):
        return found
    unrar = archives.find_tool("unrar")
    rar = Path(unrar).with_name("Rar.exe") if unrar else None
    return str(rar) if rar and rar.is_file() else None


RAR = _rar_binary()


@pytest.mark.skipif(not RAR, reason="needs rar (or WinRAR) to build a fixture")
def test_extract_rar(tmp_path: Path) -> None:
    assert RAR
    (tmp_path / "a.txt").write_text("a")
    subprocess.run([RAR, "a", "-ep", "a.rar", "a.txt"], cwd=tmp_path, check=True)
    archives.extract(tmp_path / "a.rar", tmp_path / "out")
    assert archives.list_files(tmp_path / "out") == ["a.txt"]


def test_find_tool_where_7zip_and_winrar_are_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for folder, exe in (("7-Zip", "7z.exe"), ("WinRAR", "UnRAR.exe")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / exe).write_text("")
    sevenzip, winrar = str(tmp_path / "7-Zip" / "7z.exe"), str(tmp_path / "WinRAR" / "UnRAR.exe")
    monkeypatch.setattr(archives.shutil, "which", lambda _: None)
    monkeypatch.setattr(archives.windows, "program_files", lambda: [])

    def registry_value(key: str, name: str = "") -> str | None:  # 7-Zip writes a trailing \
        return (
            f"{tmp_path / '7-Zip'}{os.sep}" if (key, name) == (r"SOFTWARE\7-Zip", "Path") else None
        )

    def app_folder(exe: str) -> Path | None:
        return tmp_path / "WinRAR" if exe == "WinRAR.exe" else None

    monkeypatch.setattr(archives.windows, "registry_value", registry_value)
    monkeypatch.setattr(archives.windows, "app_folder", app_folder)
    assert archives.find_tool("7z") == sevenzip
    assert archives.find_tool("unrar") == winrar
    assert archives.find_tool("bsdtar") is None

    # nothing in the registry: the default install folders
    monkeypatch.setattr(archives.windows, "registry_value", lambda *_: None)
    monkeypatch.setattr(archives.windows, "app_folder", lambda _: None)
    assert archives.find_tool("7z") is None
    monkeypatch.setattr(archives.windows, "program_files", lambda: [tmp_path])
    assert (archives.find_tool("7z"), archives.find_tool("unrar")) == (sevenzip, winrar)


@pytest.mark.skipif(os.name != "nt", reason="reads the Windows registry")
def test_find_tool_in_the_real_registry() -> None:
    for name in ("7z", "unrar"):
        found = archives.find_tool(name)
        assert found is None or Path(found).is_file()


def _fake_rar(tmp_path: Path) -> Path:
    archive = tmp_path / "a.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x00 not really")
    return archive


def test_unrar_command_is_the_last_resort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archives, "find_tool", lambda _: None)
    archive = _fake_rar(tmp_path)
    with pytest.raises(archives.ArchiveError, match="unrar_command"):
        archives.extract(archive, tmp_path / "none")

    script = (
        "import pathlib, shutil, sys;"
        "shutil.copy(sys.argv[1], pathlib.Path(sys.argv[2], 'copy.rar'));"
        "pathlib.Path('in_cwd.txt').write_text('x')"
    )
    command = [sys.executable, "-c", script, "{archive}", "{dest}"]
    archives.extract(archive, tmp_path / "out", command)
    assert archives.list_files(tmp_path / "out") == ["copy.rar", "in_cwd.txt"]


def test_failing_unrar_command_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archives, "find_tool", lambda _: None)
    archive = _fake_rar(tmp_path)
    script = "import pathlib, sys; pathlib.Path('junk').write_text('x'); sys.exit('broken')"
    with pytest.raises(archives.ArchiveError, match="unrar_command: broken"):
        archives.extract(archive, tmp_path / "out", [sys.executable, "-c", script, "{archive}"])
    assert archives.list_files(tmp_path / "out") == []
    with pytest.raises(archives.ArchiveError, match="unrar_command"):
        archives.extract(archive, tmp_path / "out", [str(tmp_path / "missing.exe"), "{archive}"])


def test_extract_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"not an archive")
    with pytest.raises(archives.ArchiveError):
        archives.extract(path, tmp_path / "out")


def test_slugify() -> None:
    assert slugify("Improved Atmosphere v1.0!") == "improved-atmosphere-v1-0"
    assert slugify("???") == "mod"


def test_import_override(tmp_path: Path, library: Library) -> None:
    archive = make_zip(tmp_path / "Nice Hair.zip", {"hair.mmh": "x", "docs/readme.txt": "r"})
    mod = library.import_archive(archive)

    assert mod.id == "nice-hair"
    assert mod.kind == KIND_OVERRIDE
    assert (library.dirs.downloads / "Nice Hair.zip").is_file()
    assert archive.is_file()  # original left in place
    assert mod.archive.sha256 == archives.sha256_file(archive)
    assert metadata.load_mod(library.dirs.mods / mod.id) == mod
    # the readme is an ignored file: kept in the library, never deployed
    assert (library.files_dir(mod.id) / "docs/readme.txt").is_file()
    assert library.targets(mod) == {"packages/core/override/nice-hair/hair.mmh": "hair.mmh"}
    assert [entry.id for entry in library.profile.mods] == ["nice-hair"]
    # survives a reload from disk
    assert Library(library.dirs).mods == {mod.id: mod}


def test_import_dazip(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_dazip(tmp_path / "x.dazip", uid="lock_bash", title="Lock"))
    assert mod.kind == KIND_DAZIP
    assert (mod.name, mod.version, mod.addin_uids) == ("Lock", "1.2", ["lock_bash"])
    assert library.targets(mod) == {
        "addins/lock_bash/module/data/a.gda": "Contents/addins/lock_bash/module/data/a.gda",
        "packages/core/override/lock_bash/b.erf": "Contents/packages/core/override/lock_bash/b.erf",
    }


def test_import_unpacks_nested_dazip_and_keeps_loose_files(
    tmp_path: Path, library: Library
) -> None:
    inner = make_dazip(tmp_path / "inner.dazip", uid="inner", title="Inner AddIn")
    outer = make_zip(
        tmp_path / "outer.zip", {"tex.dds": "t", "mod/Inner.dazip": inner.read_bytes()}
    )
    mod = library.import_archive(outer)
    assert mod.kind == KIND_MIXED  # a dazip plus a loose game file
    assert (mod.name, mod.version) == ("Inner AddIn", "1.2")  # the one AddIn describes the mod
    assert mod.archive.file == "outer.zip"
    assert (library.files_dir(mod.id) / "mod/Inner.dazip").is_dir()
    assert library.targets(mod) == {
        "addins/inner/module/data/a.gda": "mod/Inner.dazip/Contents/addins/inner/module/data/a.gda",
        "packages/core/override/inner/b.erf": (
            "mod/Inner.dazip/Contents/packages/core/override/inner/b.erf"
        ),
        "packages/core/override/inner-addin/tex.dds": "tex.dds",
    }


def test_import_archive_with_several_dazips(tmp_path: Path, library: Library) -> None:
    one = make_dazip(tmp_path / "one.dazip", uid="dog_talents", title="Dog Talents")
    two = make_dazip(tmp_path / "two.dazip", uid="dog_class", title="Dog Class")
    archive = make_zip(
        tmp_path / "Dog Pack.zip",
        {"One.dazip": one.read_bytes(), "Two.DAZIP": two.read_bytes(), "readme.txt": "r"},
    )
    mod = library.import_archive(archive)
    # the readme is the only loose file, and ignored files do not make a mod "mixed"
    assert (mod.id, mod.name, mod.kind) == ("dog-pack", "Dog Pack", KIND_DAZIP)
    assert mod.addin_uids == ["dog_talents", "dog_class"]
    assert [addin.uid for addin in library.addins(mod)] == ["dog_talents", "dog_class"]
    assert sorted(library.targets(mod)) == [
        "addins/dog_class/module/data/a.gda",
        "addins/dog_talents/module/data/a.gda",
        "packages/core/override/dog_class/b.erf",
        "packages/core/override/dog_talents/b.erf",
    ]


def test_import_fails_on_a_broken_nested_dazip(tmp_path: Path, library: Library) -> None:
    archive = make_zip(tmp_path / "bad.zip", {"Broken.dazip": "not a zip", "a.erf": "a"})
    with pytest.raises(LibraryError):
        library.import_archive(archive)
    assert list(library.dirs.mods.iterdir()) == []


def test_import_ids_are_unique(tmp_path: Path, library: Library) -> None:
    first = library.import_archive(make_zip(tmp_path / "a/mod.zip", {"a": "1"}))
    second = library.import_archive(make_zip(tmp_path / "b/other.zip", {"a": "2"}), name="mod")
    assert (first.id, second.id) == ("mod", "mod-2")


def test_import_refuses_different_archive_with_same_name(tmp_path: Path, library: Library) -> None:
    library.import_archive(make_zip(tmp_path / "a/mod.zip", {"a": "1"}))
    with pytest.raises(LibraryError):
        library.import_archive(make_zip(tmp_path / "b/mod.zip", {"a": "2"}))


def test_failed_import_leaves_no_trace(tmp_path: Path, library: Library) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"garbage")
    with pytest.raises(LibraryError):
        library.import_archive(bad)
    assert list(library.dirs.mods.iterdir()) == []
    assert library.mods == {}


def test_remove(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a": "1"}))
    library.remove(mod.id, delete_archive=True)
    assert library.mods == {}
    assert library.profile.mods == []
    assert not (library.dirs.mods / mod.id).exists()
    assert not (library.dirs.downloads / "mod.zip").exists()


def test_reload_reports_broken_metadata(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a": "1"}))
    (library.dirs.mods / mod.id / "mod.toml").write_text("not = [toml")
    library.reload()
    assert library.mods == {}
    assert len(library.errors) == 1


def test_replace_archive_keeps_id_order_and_ops(tmp_path: Path, library: Library) -> None:
    from dao_modman.core.models import PathOp

    mod = library.import_archive(make_zip(tmp_path / "v1.zip", {"a.erf": "1", "docs/r.txt": "r"}))
    library.import_archive(make_zip(tmp_path / "other.zip", {"o": "o"}))
    mod.ops = [PathOp("exclude", "packages/core/override/v1/docs")]
    library.save_mod(mod)

    new = make_zip(tmp_path / "v2.zip", {"a.erf": "2", "new.erf": "n", "docs/r.txt": "r"})
    library.replace_archive(mod, new, version="2.0")

    reloaded = Library(library.dirs)
    assert [entry.id for entry in reloaded.profile.mods] == ["v1", "other"]
    assert reloaded.mods["v1"].archive.file == "v2.zip"
    assert reloaded.mods["v1"].version == "2.0"
    assert set(reloaded.targets(reloaded.mods["v1"])) == {
        "packages/core/override/v1/a.erf",
        "packages/core/override/v1/new.erf",
    }
    assert (library.files_dir("v1") / "a.erf").read_text() == "2"
    assert [p.name for p in library.dirs.mods.iterdir() if p.name.startswith(".")] == []


def test_rar_falls_back_to_the_next_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bsdtar cannot read solid rar archives; another tool must get its turn."""
    archive = tmp_path / "solid.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")
    calls: list[str] = []
    works: set[str] = set()

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        tool = Path(command[0]).name  # tools run by their full path
        calls.append(tool)
        dest = tmp_path / "out"
        if tool in works:
            (dest / "good.erf").write_text(tool)
            return subprocess.CompletedProcess(command, 0, "", "")
        (dest / "partial.txt").write_text("half an extraction")
        return subprocess.CompletedProcess(command, 1, "", f"noise\n{tool} cannot read this")

    monkeypatch.setattr(archives.subprocess, "run", fake_run)
    monkeypatch.setattr(archives.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    works.add("7z")  # unrar fails, 7z succeeds, bsdtar is never asked
    archives.extract(archive, tmp_path / "out")
    assert calls == ["unrar", "7z"]
    assert archives.list_files(tmp_path / "out") == ["good.erf"]  # no leftovers of the failure

    calls.clear()
    works.clear()
    shutil.rmtree(tmp_path / "out")
    with pytest.raises(archives.ArchiveError) as error:
        archives.extract(archive, tmp_path / "out")
    assert calls == ["unrar", "7z", "bsdtar"]
    message = str(error.value)
    assert "unrar: unrar cannot read this" in message and "bsdtar: bsdtar cannot read" in message

    monkeypatch.setattr(archives, "find_tool", lambda tool: None)
    with pytest.raises(archives.ArchiveError, match="need unrar, 7z or bsdtar"):
        archives.extract(archive, tmp_path / "out")


def test_list_files_skips_links(tmp_path: Path) -> None:
    root = tmp_path / "files"
    (root / "b/deep").mkdir(parents=True)
    (root / "a.erf").write_text("a")
    (root / "b/deep/c.erf").write_text("c")
    (root / "b/Z.erf").write_text("z")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.erf").write_text("x")
    symlink(root / "link.erf", root / "a.erf")
    symlink(root / "linked_dir", outside)
    assert archives.list_files(root) == ["a.erf", "b/Z.erf", "b/deep/c.erf"]
    assert archives.list_files(tmp_path / "missing") == []


def test_listing_follows_the_library(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    assert library.list_files(mod.id) == ["a.erf"]
    library.replace_archive(mod, make_zip(tmp_path / "one-2.zip", {"b.erf": "b"}))
    assert library.list_files(mod.id) == ["b.erf"]  # the new files, not the cached ones
    library.remove(mod.id)
    again = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    assert again.id == mod.id and library.list_files(again.id) == ["a.erf"]
