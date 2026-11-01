from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dao_modman.core import deploy, report
from dao_modman.core.library import Library
from dao_modman.core.models import PathOp

from .conftest import make_dazip, make_zip, symlink

OVERRIDE = "packages/core/override"


def by_target(plan: deploy.Plan) -> dict[str, list[str]]:
    """Conflicts as `deployed copy -> mods shipping the file, in load order`."""
    return {conflict.winner.targets[-1]: conflict.mod_ids for conflict in plan.conflicts}


def tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def addin_uids(user_dir: Path) -> list[str]:
    root = ET.parse(user_dir / "Settings/AddIns.xml").getroot()
    return [item.get("UID") for item in root.iter("AddInItem")]


def test_plan_priority_and_conflicts(tmp_path: Path, library: Library) -> None:
    library.import_archive(make_zip(tmp_path / "low.zip", {"x.erf": "low", "l.erf": "l"}))
    library.import_archive(make_zip(tmp_path / "high.zip", {"X.ERF": "high"}))

    # the game finds override files by name: same name in other folders is a conflict
    plan = deploy.build_plan(library)
    assert {f.target: f.mod_id for f in plan.files} == {
        f"{OVERRIDE}/low/l.erf": "low",
        f"{OVERRIDE}/high/X.ERF": "high",
    }
    assert by_target(plan) == {f"{OVERRIDE}/high/X.ERF": ["low", "high"]}
    assert plan.overwrites() == {"low": (set(), {"high"}), "high": ({"low"}, set())}

    library.profile.move("high", -1)
    winner = {f.target.casefold(): f.mod_id for f in deploy.build_plan(library).files}
    assert winner[f"{OVERRIDE}/low/x.erf"] == "low"

    library.profile.entry("low").enabled = False
    plan = deploy.build_plan(library)
    assert [f.mod_id for f in plan.files] == ["high"]
    assert by_target(plan) == {}


def test_apply_and_unapply(tmp_path: Path, library: Library, user_dir: Path) -> None:
    (user_dir / "Packages/Core").mkdir(parents=True)  # existing casing must be reused
    (user_dir / "keep.txt").write_text("mine")
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "docs/r.txt": "r"}))
    mod.ops = [PathOp("exclude", f"{OVERRIDE}/mod/docs")]
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


def test_override_conflicts_are_by_file_name(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    """The game finds override resources by name, whatever subfolder they are in."""
    library.import_archive(
        make_zip(tmp_path / "low.zip", {"textures/Robe.dds": "low", "only_low.dds": "l"})
    )
    library.import_archive(
        make_zip(tmp_path / "high.zip", {"a/robe.dds": "high-a", "b/robe.dds": "high-b"})
    )

    plan = deploy.build_plan(library)
    assert {f.target: f.mod_id for f in plan.files} == {
        f"{OVERRIDE}/high/a/robe.dds": "high",  # duplicates inside one mod are kept
        f"{OVERRIDE}/high/b/robe.dds": "high",
        f"{OVERRIDE}/low/only_low.dds": "low",
    }
    assert by_target(plan) == {f"{OVERRIDE}/high/b/robe.dds": ["low", "high"]}

    library.profile.move("low", 1)  # low is now last, so it wins
    plan = deploy.build_plan(library)
    assert sorted(f.target for f in plan.files) == [
        f"{OVERRIDE}/low/only_low.dds",
        f"{OVERRIDE}/low/textures/Robe.dds",
    ]
    deploy.apply(plan, user_dir, library.dirs.deployed_file)
    assert tree(user_dir) == [f"{OVERRIDE}/low/only_low.dds", f"{OVERRIDE}/low/textures/Robe.dds"]


def test_same_name_outside_override_is_not_a_conflict(tmp_path: Path, library: Library) -> None:
    for name in ("one", "two"):
        files = {
            "Manifest.xml": "<OverrideList/>",
            f"BioWare/Dragon Age/other/{name}/data.erf": name,
        }
        library.import_archive(make_zip(tmp_path / f"{name}.zip", files))
    plan = deploy.build_plan(library)
    assert [f.target for f in plan.files] == ["other/one/data.erf", "other/two/data.erf"]
    assert by_target(plan) == {}


def chargen_xml(hairs: list[str], colors: list[str]) -> str:
    hair = "".join(f'<resource name="{name}" cut="1" />' for name in hairs)
    color = "".join(f'<resource name="{name}" />' for name in colors)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<morph_config><hairs><human_male>'
        f"{hair}</human_male></hairs><hair_colors>{color}</hair_colors></morph_config>"
    )


def chargen_names(path: Path, section: str) -> list[str]:
    root = ET.parse(path).getroot()
    return [resource.get("name") for resource in root.find(section).iter("resource")]


def test_chargenmorphcfg_is_merged(tmp_path: Path, library: Library, user_dir: Path) -> None:
    library.import_archive(
        make_zip(
            tmp_path / "hair.zip",
            {"chargenmorphcfg.xml": chargen_xml(["base", "hair_a"], ["black"]), "hair_a.mmh": "a"},
        )
    )
    library.import_archive(
        make_zip(
            tmp_path / "colors.zip",
            {"cfg/ChargenMorphCfg.xml": chargen_xml(["BASE", "hair_b"], ["black", "pink"])},
        )
    )
    library.import_archive(make_zip(tmp_path / "broken.zip", {"chargenmorphcfg.xml": "<oops"}))

    plan = deploy.build_plan(library)
    merged = f"{OVERRIDE}/chargenmorphcfg.xml"
    assert plan.merged == {merged: ["hair", "colors", "broken"]}
    assert by_target(plan) == {}
    assert len(plan.warnings) == 1 and plan.warnings[0].startswith("broken:")
    assert [f.target for f in plan.files] == [merged, f"{OVERRIDE}/hair/hair_a.mmh"]

    deploy.apply(plan, user_dir, library.dirs.deployed_file)
    assert chargen_names(user_dir / merged, "hairs") == ["base", "hair_a", "hair_b"]
    assert chargen_names(user_dir / merged, "hair_colors") == ["black", "pink"]
    deploy.unapply(library.dirs.deployed_file)
    assert tree(user_dir) == []


def test_mods_without_files_of_their_own(tmp_path: Path, library: Library) -> None:
    library.import_archive(make_zip(tmp_path / "fix.zip", {"fix.dlg": "fixed"}))
    library.import_archive(make_zip(tmp_path / "again.zip", {"fix.dlg": "fixed"}))
    library.import_archive(make_zip(tmp_path / "lost.zip", {"t.gda": "1"}))
    library.import_archive(make_zip(tmp_path / "wins.zip", {"t.gda": "2"}))
    library.import_archive(
        make_zip(tmp_path / "hair.zip", {"chargenmorphcfg.xml": chargen_xml(["h"], [])})
    )
    library.import_archive(
        make_zip(tmp_path / "colors.zip", {"chargenmorphcfg.xml": chargen_xml([], ["c"])})
    )
    library.import_archive(make_zip(tmp_path / "text.zip", {"readme.txt": "only text"}))

    plan = deploy.build_plan(library)
    assert plan.no_effect == ["fix", "lost", "text"]  # merged files count as an effect
    assert plan.same_files == {"fix": {"again"}}


def test_single_chargenmorphcfg_is_deployed_as_is(tmp_path: Path, library: Library) -> None:
    library.import_archive(make_zip(tmp_path / "hair.zip", {"chargenmorphcfg.xml": "<oops"}))
    plan = deploy.build_plan(library)
    assert [f.target for f in plan.files] == [f"{OVERRIDE}/hair/chargenmorphcfg.xml"]
    assert plan.merged == {} and plan.warnings == []


def test_identical_files_are_not_a_conflict(tmp_path: Path, library: Library) -> None:
    """Mods by the same author often ship the same helper file; only real differences count."""
    library.import_archive(
        make_zip(
            tmp_path / "base.zip", {"a/helper.gda": "same", "b/helper.gda": "same", "t.gda": "1"}
        )
    )
    library.import_archive(
        make_zip(tmp_path / "extras.zip", {"x/helper.gda": "same", "x/t.gda": "2", "x/u.gda": "33"})
    )
    library.import_archive(make_zip(tmp_path / "third.zip", {"u.gda": "44"}))  # same size as u

    plan = deploy.build_plan(library)
    assert by_target(plan) == {
        f"{OVERRIDE}/extras/x/t.gda": ["base", "extras"],
        f"{OVERRIDE}/third/u.gda": ["extras", "third"],
    }
    assert set(plan.overwrites()) == {"base", "extras", "third"}
    # deployment is unchanged: one mod's copy per name, identical or not
    assert [f.target for f in plan.files if f.target.endswith("helper.gda")] == [
        f"{OVERRIDE}/extras/x/helper.gda"
    ]


def test_ignored_files_are_not_deployed(tmp_path: Path, dirs, user_dir: Path) -> None:
    ignored = ["ReadMe.TXT", "notes.txt", "Manual.PDF", "README", "my_readme_de.erf"]
    ignored += ["Homepage.URL", "shots/preview.jpg", "Shot.PNG", "__MACOSX/a.erf", "Thumbs.db"]
    kept = ["tex.dds", "data.xml", "sub/model.mmh"]  # game formats, however similar
    library = Library(dirs)
    for name in ("one", "two"):
        files = {path: f"{name} {path}" for path in [*ignored, *kept]}
        library.import_archive(make_zip(tmp_path / f"{name}.zip", files))

    plan = deploy.build_plan(library)
    assert sorted({f.target.split("/", 4)[-1] for f in plan.files}) == sorted(kept)
    # only game files can conflict
    assert sorted(c.name for c in plan.conflicts) == ["data.xml", "model.mmh", "tex.dds"]
    deploy.apply(plan, user_dir, dirs.deployed_file)
    assert tree(user_dir) == [
        f"{OVERRIDE}/two/data.xml",
        f"{OVERRIDE}/two/sub/model.mmh",
        f"{OVERRIDE}/two/tex.dds",
    ]


def test_ignore_patterns_come_from_the_config(tmp_path: Path, dirs) -> None:
    files = {"a.erf": "a", "notes.txt": "n", "extras/b.erf": "b", "keep/readme.txt": "k"}
    patterns = ["*.txt", "/extras/", "!keep/*.txt"]  # .gitignore syntax, incl. negation
    library = Library(dirs, ignore=patterns)
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", files))
    assert sorted(library.targets(mod).values()) == ["a.erf", "keep/readme.txt"]
    assert sorted(Library(dirs, ignore=[]).targets(mod).values()) == sorted(files)


def test_overwrites_follow_the_load_order(tmp_path: Path, library: Library) -> None:
    for name in ("base", "middle", "top"):
        library.import_archive(make_zip(tmp_path / f"{name}.zip", {"a.gda": name}))
    library.import_archive(make_zip(tmp_path / "solo.zip", {"s.gda": "s"}))

    # only the last mod's copy is used: it overwrites all the others
    assert deploy.build_plan(library).overwrites() == {
        "base": (set(), {"top"}),
        "middle": (set(), {"top"}),
        "top": ({"base", "middle"}, set()),
    }
    library.profile.move("top", -1)
    assert deploy.build_plan(library).overwrites()["middle"] == ({"base", "top"}, set())


def test_failed_apply_is_rolled_back(
    tmp_path: Path, library: Library, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All or nothing: a failure part-way leaves no file, folder, AddIn or record behind."""
    (user_dir / "Settings").mkdir()
    (user_dir / "Settings/AddIns.xml").write_text('<AddInsList><AddInItem UID="dlc"/></AddInsList>')
    (user_dir / "keep.txt").write_text("mine")
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "sub/b.erf": "b"}))
    library.import_archive(make_dazip(tmp_path / "x.dazip", uid="mine"))
    before = tree(user_dir)
    deployed = library.dirs.deployed_file

    real_write = deploy._write_hashed
    calls = {"n": 0}

    def failing_write(planned: deploy.PlannedFile, dest: Path) -> str:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real_write(planned, dest)

    monkeypatch.setattr(deploy, "_write_hashed", failing_write)
    with pytest.raises(deploy.DeployError, match="rolled back: disk full"):
        deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == before
    assert not (user_dir / "packages").exists() and not (user_dir / "addins").exists()
    assert not deployed.exists()

    # a failure at the very end, while registering AddIns, undoes the copied files too
    monkeypatch.setattr(deploy, "_write_hashed", real_write)
    monkeypatch.setattr(deploy.addins, "register", lambda *_: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(deploy.DeployError, match="rolled back"):
        deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == before and not deployed.exists()
    assert addin_uids(user_dir) == ["dlc"]

    # an interruption (ctrl+c) is rolled back as well, and passed on unchanged
    monkeypatch.undo()
    monkeypatch.setattr(
        deploy, "_write_hashed", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == before and not deployed.exists()


def test_apply_cut_short_is_rolled_back_on_the_next_run(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    """A killed process leaves the intent behind; the next run undoes it."""
    (user_dir / "keep.txt").write_text("mine")
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "b.erf": "b"}))
    deployed = library.dirs.deployed_file
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    complete = json.loads(deployed.read_text())
    assert complete["state"] == "applied"
    assert not deploy.rollback(deployed)  # a finished apply is not rolled back

    # what a kill after the first file would leave: the intent, and one of the two files
    intent = {
        **complete,
        "state": "applying",
        "files": [{"path": f["path"], "new": True} for f in complete["files"]],
    }
    deployed.write_text(json.dumps(intent))
    (user_dir / OVERRIDE / "one/b.erf").unlink()

    assert deploy.rollback(deployed)
    assert tree(user_dir) == ["keep.txt"] and not deployed.exists()
    assert not (user_dir / "packages").exists()

    # the same leftover is also cleared by a plain apply or unapply
    for action in ("apply", "unapply"):
        (user_dir / OVERRIDE / "one").mkdir(parents=True)
        (user_dir / OVERRIDE / "one/a.erf").write_text("a")
        deployed.write_text(json.dumps(intent))
        if action == "apply":
            deploy.apply(deploy.build_plan(library), user_dir, deployed)
            assert tree(user_dir) == ["keep.txt", f"{OVERRIDE}/one/a.erf", f"{OVERRIDE}/one/b.erf"]
            deploy.unapply(deployed)
        else:
            assert deploy.unapply(deployed).removed == 0
        assert tree(user_dir) == ["keep.txt"]


def test_progress_and_folder_casing(tmp_path: Path, library: Library, user_dir: Path) -> None:
    for name, folder in (("one", "Shared"), ("two", "shared")):
        files = {
            "Manifest.xml": "<OverrideList/>",
            f"BioWare/Dragon Age/extra/{folder}/{name}.erf": name,
        }
        library.import_archive(make_zip(tmp_path / f"{name}.zip", files))
    seen: list[tuple[str, int, int]] = []
    deployed = library.dirs.deployed_file
    deploy.apply(deploy.build_plan(library), user_dir, deployed, lambda *step: seen.append(step))
    # both mods end up in one folder although it did not exist before either was copied
    assert tree(user_dir) == ["extra/Shared/one.erf", "extra/Shared/two.erf"]
    assert [step for step in seen if step[0] == "Writing files"] == [
        ("Writing files", 1, 2),
        ("Writing files", 2, 2),
    ]
    assert ("Comparing with the last apply", 2, 2) in seen

    seen.clear()
    deploy.unapply(deployed, lambda *step: seen.append(step))
    assert seen == [("Removing applied files", 1, 2), ("Removing applied files", 2, 2)]


def test_scan_and_clean_the_mod_directories(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "Addins/dao_prc_ep_1").mkdir(parents=True)
    (user_dir / "Settings").mkdir()
    (user_dir / "Settings/AddIns.xml").write_text(
        '<AddInsList><AddInItem UID="DAO_PRC_EP_1" BioWare="1"/>'
        '<AddInItem UID="bought_dlc" BioWare="1"/><AddInItem UID="by_hand"/></AddInsList>'
    )
    (user_dir / "Settings/DragonAge.ini").write_text("settings")
    (user_dir / "Characters").mkdir()
    (user_dir / "Characters/save.das").write_text("save game")
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "b.erf": "b"}))
    library.import_archive(make_dazip(tmp_path / "x.dazip", uid="mine"))
    deployed = library.dirs.deployed_file
    deploy.apply(deploy.build_plan(library), user_dir, deployed)

    # our own unchanged files are not reported; an AddIn entry of unknown origin is
    first = deploy.scan(user_dir, deployed, install_dir)
    assert (first.unmanaged, first.modified, first.addins) == ([], [], ["by_hand"])

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "precious.txt").write_text("not ours to delete")
    foreign_files = {
        f"{OVERRIDE}/manual/tex.dds": "by hand",
        "packages/core/data/fonts.erf": "by hand",
        "AddIns/by_hand/core/data/x.erf": "another manager",
        "AddIns/bought_dlc/core/data/dlc.erf": "official DLC stored in the user dir",
    }
    for rel, content in foreign_files.items():
        (user_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (user_dir / rel).write_text(content)
    symlink(user_dir / OVERRIDE / "link", outside)
    (user_dir / OVERRIDE / "one/a.erf").write_text("edited")

    seen: list[tuple[str, int, int]] = []
    foreign = deploy.scan(user_dir, deployed, install_dir, lambda *step: seen.append(step))
    assert foreign.unmanaged == [
        "AddIns/by_hand/core/data/x.erf",
        "packages/core/data/fonts.erf",
        f"{OVERRIDE}/link",
        f"{OVERRIDE}/manual/tex.dds",
    ]
    assert foreign.modified == [f"{OVERRIDE}/one/a.erf"]
    assert foreign.addins == ["by_hand"]  # not ours, not official
    assert seen[-1][0] == "Checking files" and seen[-1][1] == seen[-1][2]

    with pytest.raises(deploy.DeployError, match="unapply before"):
        deploy.clean(user_dir, deployed, install_dir)
    deploy.unapply(deployed)
    assert deploy.clean(user_dir, deployed, install_dir) == 5

    # ignoring case: on Windows the hand-made AddIns/ files went into the mod's addins/ folder
    assert sorted(path.casefold() for path in tree(user_dir)) == [
        "addins/bought_dlc/core/data/dlc.erf",  # official DLC is never touched
        "characters/save.das",
        "settings/addins.xml",
        "settings/dragonage.ini",
    ]
    assert addin_uids(user_dir) == ["DAO_PRC_EP_1", "bought_dlc"]
    assert (user_dir / OVERRIDE).is_dir() and not (user_dir / "packages/core/data").exists()
    assert (outside / "precious.txt").read_text() == "not ours to delete"  # link not followed
    assert not deploy.scan(user_dir, deployed, install_dir)


def test_foreign_files_show_up_in_conflicts(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    library.import_archive(make_zip(tmp_path / "hair.zip", {"hair.mmh": "mine", "same.erf": "s"}))
    (user_dir / OVERRIDE / "hand").mkdir(parents=True)
    (user_dir / OVERRIDE / "hand/HAIR.MMH").write_text("by hand")  # same name, other folder
    (user_dir / OVERRIDE / "hand/same.erf").write_text("s")  # same content: no clash
    (user_dir / OVERRIDE / "hand/other.erf").write_text("o")  # no counterpart
    foreign = deploy.unmanaged(user_dir, library.dirs.deployed_file)
    assert deploy.build_plan(library).conflicts == []  # without them, nothing to report

    plan = deploy.build_plan(library, foreign)
    assert [(c.name, c.mod_ids) for c in plan.conflicts] == [
        ("HAIR.MMH", ["hair", deploy.UNMANAGED])  # theirs is listed last: it stays
    ]
    assert {f.mod_id for f in plan.files} == {"hair"}  # foreign files are never deployed
    assert plan.overwrites() == {"hair": (set(), {deploy.UNMANAGED})}

    text = report.format_conflicts(library, plan)
    assert "#1 hair  <  not managed by dao-modman   (1 file)" in text
    assert f"not managed by dao-modman   {OVERRIDE}/hand/" in text


def mtimes(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()
    }


def test_apply_only_touches_what_changed(tmp_path: Path, library: Library, user_dir: Path) -> None:
    deployed = library.dirs.deployed_file
    one = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "sub/b.erf": "b"}))
    two = library.import_archive(make_zip(tmp_path / "two.zip", {"c.erf": "c"}))
    library.profile.entry(two.id).enabled = False

    plan = deploy.build_plan(library)
    assert deploy.compare(plan, None).mods == {"one": deploy.NOT_APPLIED}
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (2, 0, 0)
    before = mtimes(user_dir)

    # nothing changed: nothing is written
    plan = deploy.build_plan(library)
    record = deploy.load_record(deployed)
    comparison = deploy.compare(plan, record)
    assert comparison.mods == {"one": deploy.APPLIED} and comparison.pending == []
    assert (comparison.write, comparison.remove, comparison.unchanged) == (0, 0, 2)
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (0, 0, 2)
    assert mtimes(user_dir) == before

    # enabling a mod writes only its files
    library.profile.entry(two.id).enabled = True
    plan = deploy.build_plan(library)
    assert deploy.compare(plan, deploy.load_record(deployed)).mods == {
        "one": deploy.APPLIED,
        "two": deploy.NOT_APPLIED,
    }
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (1, 0, 2)
    assert {k: v for k, v in mtimes(user_dir).items() if k in before} == before

    # excluding a folder removes its files, and the emptied folder
    one.ops = [PathOp("exclude", f"{OVERRIDE}/one/sub")]
    plan = deploy.build_plan(library)
    assert deploy.compare(plan, deploy.load_record(deployed)).mods["one"] == deploy.CHANGED
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (0, 1, 2)
    assert not (user_dir / OVERRIDE / "one/sub").exists()

    # a library file with new content (an update) is written again
    source = library.files_dir(two.id) / "c.erf"
    source.write_text("c2")
    os.utime(source, ns=(1, 1))
    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert (result.written, result.removed) == (1, 1)
    assert (user_dir / OVERRIDE / "two/c.erf").read_text() == "c2"

    # disabling a mod removes only its files, and its folder
    library.profile.entry(one.id).enabled = False
    plan = deploy.build_plan(library)
    assert deploy.compare(plan, deploy.load_record(deployed)).mods == {
        "one": deploy.STILL_APPLIED,
        "two": deploy.APPLIED,
    }
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (0, 1, 1)
    assert tree(user_dir) == [f"{OVERRIDE}/two/c.erf"]
    assert not (user_dir / OVERRIDE / "one").exists()


def test_files_changed_by_hand_are_left_alone(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    deployed = library.dirs.deployed_file
    mod = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "b.erf": "b"}))
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    edited = user_dir / OVERRIDE / "one/a.erf"
    edited.write_text("edited by hand")

    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert result.kept == [f"{OVERRIDE}/one/a.erf"] and result.written == 0
    assert edited.read_text() == "edited by hand"
    assert [item["path"] for item in deploy.load_record(deployed)["files"]] == [
        f"{OVERRIDE}/one/b.erf"
    ]  # no longer ours
    # from now on it is a file we did not write: skipped
    assert deploy.apply(deploy.build_plan(library), user_dir, deployed).skipped == [
        f"{OVERRIDE}/one/a.erf"
    ]
    library.profile.entry(mod.id).enabled = False
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert tree(user_dir) == [f"{OVERRIDE}/one/a.erf"]  # ours removed, theirs kept


def test_addin_of_a_disabled_mod_is_unregistered(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    deployed = library.dirs.deployed_file
    first = library.import_archive(make_dazip(tmp_path / "first.dazip", "first_uid", "First"))
    library.import_archive(make_dazip(tmp_path / "second.dazip", "second_uid", "Second"))
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert addin_uids(user_dir) == ["first_uid", "second_uid"]

    library.profile.entry(first.id).enabled = False
    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert result.unregistered == ["first_uid"] and result.addins == []
    assert addin_uids(user_dir) == ["second_uid"]
    assert deploy.load_record(deployed)["addins"] == ["second_uid"]


def test_failed_incremental_apply_leaves_nothing_applied(
    tmp_path: Path, library: Library, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployed = library.dirs.deployed_file
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "b.erf": "b"}))
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    (user_dir / OVERRIDE / "one/b.erf").write_text("edited by hand")
    two = library.import_archive(make_zip(tmp_path / "two.zip", {"c.erf": "c", "d.erf": "d"}))

    real_write = deploy._write_hashed
    written: list[Path] = []

    def fail_second(planned, dest):
        if written:
            raise OSError("disk full")
        written.append(dest)
        return real_write(planned, dest)

    monkeypatch.setattr(deploy, "_write_hashed", fail_second)
    with pytest.raises(deploy.DeployError, match="rolled back: disk full"):
        deploy.apply(deploy.build_plan(library), user_dir, deployed)
    # ours are gone, kept or new; the file changed by hand survives
    assert tree(user_dir) == [f"{OVERRIDE}/one/b.erf"] and not deployed.exists()
    assert two.id in library.mods


def test_record_without_signatures_is_checked_once(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    """A record from before signatures existed: checked by content, not rewritten."""
    deployed = library.dirs.deployed_file
    one = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a", "b.erf": "b"}))
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    before = mtimes(user_dir)
    record = json.loads(deployed.read_text())
    record["files"] = [{k: item[k] for k in ("path", "mod", "sha256")} for item in record["files"]]
    deployed.write_text(json.dumps(record))
    # the mod's b.erf changed since that apply: only it is written again
    (library.files_dir(one.id) / "b.erf").write_text("b2")

    plan = deploy.build_plan(library)
    assert deploy.compare(plan, record).mods == {"one": deploy.APPLIED}  # applied, as far as known
    result = deploy.apply(plan, user_dir, deployed)
    assert (result.written, result.removed, result.unchanged) == (1, 1, 1)
    assert mtimes(user_dir)[f"{OVERRIDE}/one/a.erf"] == before[f"{OVERRIDE}/one/a.erf"]
    upgraded = deploy.load_record(deployed)["files"]
    assert all("source" in item and "size" in item for item in upgraded)
    assert deploy.apply(deploy.build_plan(library), user_dir, deployed).unchanged == 2


@pytest.fixture
def hard_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply with hard links, as on Windows (Linux supports them as well)."""
    monkeypatch.setattr(deploy, "_LINK", deploy.HARDLINK)
    monkeypatch.setattr(deploy, "_unlinkable", set())


def test_hard_links(tmp_path: Path, library: Library, user_dir: Path, hard_links: None) -> None:
    deployed = library.dirs.deployed_file
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "b.erf": "b"}))
    source = library.files_dir(mod.id) / "a.erf"

    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert (result.written, result.linked) == (2, 2)
    applied = user_dir / OVERRIDE / "mod/a.erf"
    assert applied.samefile(source)
    item = next(i for i in deploy.load_record(deployed)["files"] if i["path"].endswith("a.erf"))
    assert (item["link"], item["linked"]) == ("hard", str(source))

    again = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert (again.written, again.unchanged) == (0, 2)
    assert deploy.unapply(deployed).removed == 2
    assert not applied.exists() and source.read_text() == "a"  # only the link is gone


def test_changed_hard_link_reports_the_library_copy(
    tmp_path: Path, library: Library, user_dir: Path, hard_links: None
) -> None:
    deployed = library.dirs.deployed_file
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "b.erf": "b"}))
    deploy.apply(deploy.build_plan(library), user_dir, deployed)
    edited, replaced = user_dir / OVERRIDE / "mod/a.erf", user_dir / OVERRIDE / "mod/b.erf"
    edited.write_text("edited in place")  # changes the library's copy too
    replaced.unlink()
    replaced.write_text("replaced")  # a new file: the library's copy is untouched

    result = deploy.apply(deploy.build_plan(library), user_dir, deployed)
    assert sorted(result.kept) == [f"{OVERRIDE}/mod/a.erf", f"{OVERRIDE}/mod/b.erf"]
    assert result.library_changed == {mod.id: [f"{OVERRIDE}/mod/a.erf"]}
    assert (library.files_dir(mod.id) / "a.erf").read_text() == "edited in place"
    assert (library.files_dir(mod.id) / "b.erf").read_text() == "b"


def test_unapply_reports_changed_library_copies(
    tmp_path: Path, library: Library, user_dir: Path, hard_links: None
) -> None:
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a"}))
    deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    (user_dir / OVERRIDE / "mod/a.erf").write_text("edited")
    result = deploy.unapply(library.dirs.deployed_file)
    assert result.library_changed == {mod.id: [f"{OVERRIDE}/mod/a.erf"]}


def test_hard_link_failure_falls_back_to_copying(
    tmp_path: Path,
    library: Library,
    user_dir: Path,
    hard_links: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def no_links(source: Path, dest: Path) -> None:
        calls.append(dest)
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(deploy.os, "link", no_links)
    mod = library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a", "b.erf": "b"}))
    result = deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    assert (result.written, result.linked) == (2, 0)
    assert len(calls) == 1  # not tried again for the same drives
    applied = user_dir / OVERRIDE / "mod/a.erf"
    assert applied.read_text() == "a"
    assert not applied.samefile(library.files_dir(mod.id) / "a.erf")
    assert "link" not in deploy.load_record(library.dirs.deployed_file)["files"][0]


def test_clone_failure_falls_back_to_copying(
    tmp_path: Path, library: Library, user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def no_clones(src: object, out: object) -> None:
        calls.append(out)
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(deploy, "_LINK", deploy.REFLINK)
    monkeypatch.setattr(deploy, "_unlinkable", set())
    monkeypatch.setattr(deploy, "_clone", no_clones)
    library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a" * 5000, "b.erf": "b"}))
    result = deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    assert (result.written, result.linked) == (2, 0)
    assert len(calls) == 1
    assert (user_dir / OVERRIDE / "mod/a.erf").read_text() == "a" * 5000
    assert deploy.unapply(library.dirs.deployed_file).removed == 2  # hashes match the copies


def test_real_clone(tmp_path: Path, library: Library, user_dir: Path) -> None:
    """Clones where the filesystem can (btrfs, XFS ...); pytest's tmp often cannot."""
    if not sys.platform.startswith("linux"):
        pytest.skip("reflinks are used on Linux")
    probe, copy = tmp_path / "probe", tmp_path / "probe-copy"
    probe.write_bytes(b"x" * 4096)
    with probe.open("rb") as src, copy.open("xb") as out:
        try:
            deploy._clone(src, out)
        except OSError:
            pytest.skip(f"{tmp_path} cannot clone files")
    library.import_archive(make_zip(tmp_path / "mod.zip", {"a.erf": "a" * 9000}))
    result = deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    assert result.linked == 1
    applied = user_dir / OVERRIDE / "mod/a.erf"
    applied.write_text("changed")  # a clone is independent of the library's copy
    assert next(library.dirs.mods.glob("mod/files/a.erf")).read_text() == "a" * 9000


def test_generated_and_reduced_files_are_never_linked(
    tmp_path: Path, library: Library, user_dir: Path, hard_links: None
) -> None:
    one = {"chargenmorphcfg.xml": chargen_xml(["base", "hair_a"], ["black"]), "a.mmh": "a"}
    library.import_archive(make_zip(tmp_path / "one.zip", one))
    two = {"chargenmorphcfg.xml": chargen_xml(["base", "hair_b"], ["pink"])}
    library.import_archive(make_zip(tmp_path / "two.zip", two))
    plan = deploy.build_plan(library)
    assert plan.merged
    result = deploy.apply(plan, user_dir, library.dirs.deployed_file)
    assert (result.written, result.linked) == (2, 1)  # a.mmh linked, the merged file written
    merged = next(i for i in deploy.load_record(library.dirs.deployed_file)["files"]
                  if i["path"].endswith("chargenmorphcfg.xml"))  # fmt: skip
    assert "link" not in merged
