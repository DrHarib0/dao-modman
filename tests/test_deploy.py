from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dao_modman.core import deploy
from dao_modman.core.library import Library
from dao_modman.core.models import PathOp

from .conftest import make_dazip, make_zip

OVERRIDE = "packages/core/override"


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def addin_uids(user_dir: Path) -> list[str]:
    root = ET.parse(user_dir / "Settings/AddIns.xml").getroot()
    return [item.get("UID") for item in root.iter("AddInItem")]


def test_plan_priority_and_conflicts(tmp_path: Path, library: Library) -> None:
    low = library.import_archive(make_zip(tmp_path / "low.zip", {"x.erf": "low", "l.erf": "l"}))
    high = library.import_archive(make_zip(tmp_path / "high.zip", {"X.ERF": "high"}))
    for mod in (low, high):  # make both mods write to the same directory
        mod.ops = [PathOp("rename", f"{OVERRIDE}/{mod.id}", f"{OVERRIDE}/shared")]

    plan = deploy.build_plan(library)
    assert {f.target: f.mod_id for f in plan.files} == {
        f"{OVERRIDE}/shared/l.erf": "low",
        f"{OVERRIDE}/shared/X.ERF": "high",
    }
    assert plan.conflicts == {f"{OVERRIDE}/shared/X.ERF": ["low", "high"]}
    assert plan.conflicting_mods() == {"low", "high"}

    library.profile.move("high", -1)
    winner = {f.target.casefold(): f.mod_id for f in deploy.build_plan(library).files}
    assert winner[f"{OVERRIDE}/shared/x.erf"] == "low"

    library.profile.entry("low").enabled = False
    plan = deploy.build_plan(library)
    assert [f.mod_id for f in plan.files] == ["high"]
    assert plan.conflicts == {}


def test_apply_and_unapply(tmp_path: Path, library: Library, user_dir: Path) -> None:
    (user_dir / "Packages/Core").mkdir(parents=True)  # existing casing must be reused
    (user_dir / "keep.txt").write_text("mine")
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "docs/r.txt": "r"}))
    mod.ops = [PathOp("remove", f"{OVERRIDE}/mod/docs")]
    deployed = library.dirs.deployed_file

    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert result.written == 1
    assert tree(user_dir) == ["Packages/Core/override/mod/a.erf", "keep.txt"]
    record = json.loads(deployed.read_text())
    assert [f["path"] for f in record["files"]] == ["Packages/Core/override/mod/a.erf"]
    assert record["dirs"] == ["Packages/Core/override", "Packages/Core/override/mod"]

    result = deploy.unapply(deployed)
    assert result.removed == 1
    assert tree(user_dir) == ["keep.txt"]
    assert (user_dir / "Packages/Core").is_dir()  # existed before, so it stays
    assert not (user_dir / "Packages/Core/override").exists()
    assert not deployed.exists()


def test_existing_and_edited_files_are_left_alone(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "b.erf": "b"}))
    target = user_dir / OVERRIDE / "mod"
    target.mkdir(parents=True)
    (target / "a.erf").write_text("pre-existing")

    result = deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    assert result.skipped == [f"{OVERRIDE}/mod/a.erf"]
    assert (target / "a.erf").read_text() == "pre-existing"

    (target / "b.erf").write_text("edited by user")
    result = deploy.unapply(library.dirs.deployed_file)
    assert result.removed == 0
    assert result.kept == [f"{OVERRIDE}/mod/b.erf"]
    assert tree(user_dir) == [f"{OVERRIDE}/mod/a.erf", f"{OVERRIDE}/mod/b.erf"]


def test_reapply_replaces_previous_deployment(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    library.import_archive(make_zip(tmp_path / "two.zip", {"b.erf": "b"}))
    deployed = library.dirs.deployed_file
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == [f"{OVERRIDE}/one/a.erf", f"{OVERRIDE}/two/b.erf"]

    library.profile.entry("one").enabled = False
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == [f"{OVERRIDE}/two/b.erf"]


def test_addins_registered_and_unregistered(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    settings = user_dir / "Settings"
    settings.mkdir()
    (settings / "AddIns.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<AddInsList><AddInItem UID="other"/></AddInsList>'
    )
    library.import_archive(make_dazip(tmp_path / "x.dazip", uid="mine"))
    deployed = library.dirs.deployed_file

    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert result.addins == ["mine"]
    assert addin_uids(user_dir) == ["other", "mine"]
    assert (user_dir / "addins/mine/module/data/a.gda").is_file()

    deploy.unapply(deployed)
    assert addin_uids(user_dir) == ["other"]
    assert not (user_dir / "addins").exists()


def test_preexisting_addin_is_not_removed(tmp_path: Path, library: Library, user_dir: Path) -> None:
    settings = user_dir / "Settings"
    settings.mkdir()
    (settings / "AddIns.xml").write_text('<AddInsList><AddInItem UID="mine"/></AddInsList>')
    library.import_archive(make_dazip(tmp_path / "x.dazip", uid="mine"))
    result = deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    assert result.addins == []
    deploy.unapply(library.dirs.deployed_file)
    assert addin_uids(user_dir) == ["mine"]


def test_apply_requires_user_dir(tmp_path: Path, library: Library) -> None:
    with pytest.raises(deploy.DeployError):
        deploy.apply(deploy.Plan(), tmp_path / "missing", library.dirs.deployed_file)


def test_unapply_without_record_is_a_noop(library: Library) -> None:
    assert deploy.unapply(library.dirs.deployed_file).removed == 0
