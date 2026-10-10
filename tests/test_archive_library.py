from __future__ import annotations

import hashlib
import shutil
import subprocess
import zipfile
from pathlib import Path

import py7zr
import pytest

from dao_modman.core import archive as archives
from dao_modman.core import metadata
from dao_modman.core.library import Library, LibraryError, slugify
from dao_modman.core.models import KIND_DAZIP, KIND_OVERRIDE

from .conftest import make_dazip, make_zip


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


@pytest.mark.skipif(not shutil.which("rar"), reason="needs the rar binary to build a fixture")
def test_extract_rar(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    subprocess.run(["rar", "a", "-ep", "a.rar", "a.txt"], cwd=tmp_path, check=True)
    archives.extract(tmp_path / "a.rar", tmp_path / "out")
    assert archives.list_files(tmp_path / "out") == ["a.txt"]


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
    assert library.targets(mod) == {
        "packages/core/override/nice-hair/docs/readme.txt": "docs/readme.txt",
        "packages/core/override/nice-hair/hair.mmh": "hair.mmh",
    }
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


def test_import_unwraps_single_nested_dazip(tmp_path: Path, library: Library) -> None:
    inner = make_dazip(tmp_path / "inner.dazip")
    outer = make_zip(
        tmp_path / "outer.zip", {"readme.txt": "r", "mod/inner.dazip": inner.read_bytes()}
    )
    mod = library.import_archive(outer, name="Wrapped")
    assert mod.kind == KIND_DAZIP
    assert mod.name == "Wrapped"
    assert mod.archive.file == "outer.zip"


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
    mod.ops = [PathOp("remove", "packages/core/override/v1/docs")]
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
