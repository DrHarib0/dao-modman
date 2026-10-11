from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from dao_modman.core import deploy, overridepkg, pathops
from dao_modman.core.library import Library, LibraryError
from dao_modman.core.models import KIND_MIXED, KIND_PACKAGE, Mod, PathOp

from .conftest import OVERRIDE_CONFIG, OVERRIDE_MANIFEST, make_dazip, make_override, make_zip

BASE = "packages/core/override/Bodies"


def contents(library: Library, mod: Mod) -> dict[str, str]:
    """Deployed path (below the package's override folder) -> content of the file behind it."""
    files_dir = library.files_dir(mod.id)
    return {
        target.removeprefix(f"{BASE}/"): (files_dir / source).read_text()
        for target, source in library.targets(mod).items()
    }


def test_roots_and_targets() -> None:
    files = [
        "Manifest.xml",
        "BioWare/Dragon Age/packages/core/override/X/a.msh",
        "BioWare/Dragon Age/packages/core/override/X/OverrideConfig.xml",
        "extra.txt",
        "sub/P.override/manifest.xml",
        "sub/P.override/bioware/dragon age/Settings/b.ini",
        "NoPayload/Manifest.xml",
    ]
    assert overridepkg.roots(files) == {
        "": "Manifest.xml",
        "sub/P.override": "sub/P.override/manifest.xml",
    }
    assert overridepkg.target("", files[1]) == "packages/core/override/X/a.msh"
    assert overridepkg.target("", files[2]) is None  # the config is for mod managers
    assert overridepkg.target("", "Manifest.xml") is None
    assert overridepkg.target("sub/P.override", files[5]) == "Settings/b.ini"


def test_parse_manifest_and_config(tmp_path: Path) -> None:
    (tmp_path / "Manifest.xml").write_text(OVERRIDE_MANIFEST.format(name="Bodies"), "utf-8")
    package = overridepkg.parse_manifest(tmp_path / "Manifest.xml")
    assert (package.name, package.version, package.publisher) == ("Bodies", "2.5", "Someone")

    (tmp_path / "OverrideConfig.xml").write_text(OVERRIDE_CONFIG, "utf-8")
    keys = overridepkg.parse_config(tmp_path / "OverrideConfig.xml")
    assert [(key.id, key.original, key.default) for key in keys] == [
        ("Humans/Body", "hf_body.msh", "medium"),
        ("Humans/Skin", "HF_skin.dds", "Pale"),
        ("Elves/Ears", "ef_ears.msh", "Long"),
    ]
    body = keys[0]
    assert [(value.label, value.file) for value in body.values] == [
        ("Big", "(big)hf_body.msh"),
        ("Medium", "(medium)hf_body.msh"),
    ]
    assert body.chosen({}).label == "Medium"  # the default, despite its different casing
    assert body.chosen({"humans/body": "BIG"}).label == "Big"
    assert body.chosen({"Humans/Body": "gone"}).label == "Medium"  # stale choice
    assert body.is_default({}) and not body.is_default({"Humans/Body": "Big"})

    (tmp_path / "bad.xml").write_text("<OverrideConfig><oops")
    with pytest.raises(overridepkg.OverridePkgError):
        overridepkg.parse_config(tmp_path / "bad.xml")


def test_import_bare_package(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "Bodies 2.5.override"))
    assert (mod.id, mod.name, mod.version, mod.kind) == ("bodies", "Bodies", "2.5", KIND_PACKAGE)
    assert Library(library.dirs).mods["bodies"].kind == KIND_PACKAGE

    # payload paths are taken from the package; one file per option, no variants, no extras
    assert contents(library, mod) == {
        "hf_body.msh": "medium body",
        "hf_skin.dds": "pale skin",
        "ef_ears.msh": "long ears",  # no original in the package: placed next to the config
        "other.mao": "plain file",
    }
    assert [(key.id, len(key.values)) for o in library.options(mod) for key in o.keys] == [
        ("Humans/Body", 2),
        ("Humans/Skin", 2),
        ("Elves/Ears", 2),
    ]


def test_import_package_wrapped_in_an_archive(tmp_path: Path, library: Library) -> None:
    inner = make_override(tmp_path / "inner.override")
    archive = make_zip(
        tmp_path / "Bodies-554.zip", {"Bodies.override": inner.read_bytes(), "readme.txt": "r"}
    )
    mod = library.import_archive(archive, name="Bodies from Nexus", version="3")
    assert (mod.name, mod.version, mod.kind) == ("Bodies from Nexus", "3", KIND_PACKAGE)
    assert (library.files_dir(mod.id) / "Bodies.override").is_dir()
    assert sorted(contents(library, mod)) == [
        "ef_ears.msh",
        "hf_body.msh",
        "hf_skin.dds",
        "other.mao",
    ]


def test_package_without_options(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "plain.override", config=False))
    assert library.options(mod) == []
    assert "Variants/(big)hf_body.msh" in contents(library, mod)  # just files, all deployed
    assert contents(library, mod)["hf_body.msh"] == "original body"


def test_choices_are_stored_and_resolved(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "Bodies.override"))
    keys = {key.id: key for o in library.options(mod) for key in o.keys}

    library.set_option(mod, keys["Humans/Body"], "big")
    library.set_option(mod, keys["Elves/Ears"], "Short")
    library.set_option(mod, keys["Humans/Skin"], "Pale")  # the default is not stored
    raw = tomllib.loads((library.dirs.mods / mod.id / "mod.toml").read_text())
    assert raw["options"] == {"Humans/Body": "Big", "Elves/Ears": "Short"}

    mod = Library(library.dirs).mods[mod.id]
    assert contents(library, mod) == {
        "hf_body.msh": "big body",
        "hf_skin.dds": "pale skin",
        "ef_ears.msh": "short ears",
        "other.mao": "plain file",
    }

    library.set_option(mod, keys["Humans/Body"], "Medium")  # back to the default
    assert mod.options == {"Elves/Ears": "Short"}
    assert contents(library, mod)["hf_body.msh"] == "medium body"


def test_path_operations_apply_after_options(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "Bodies.override"))
    body = library.options(mod)[0].keys[0]
    mod.ops = [
        PathOp("rename", f"{BASE}/hf_body.msh", f"{BASE}/renamed.msh"),
        PathOp("remove", f"{BASE}/hf_skin.dds"),
        PathOp("remove", f"{BASE}/Variants"),  # the variants are not in the list: no effect
    ]
    assert contents(library, mod) == {
        "renamed.msh": "medium body",
        "ef_ears.msh": "long ears",
        "other.mao": "plain file",
    }
    # changing the choice changes the content behind the op's path, not the op
    library.set_option(mod, body, "Big")
    assert contents(library, mod)["renamed.msh"] == "big body"
    assert len(mod.ops) == 3


def test_missing_variant_keeps_the_original_and_warns(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "Bodies.override"))
    variants = library.files_dir(mod.id) / "BioWare/Dragon Age" / BASE / "Variants"
    (variants / "(medium)hf_body.msh").unlink()
    (variants / "(long)ef_ears.msh").unlink()

    plan = deploy.build_plan(library)
    assert plan.warnings == [
        "bodies: option Humans/Body: variant file '(medium)hf_body.msh' is missing",
        "bodies: option Elves/Ears: variant file '(long)ef_ears.msh' is missing",
    ]
    assert contents(library, mod) == {
        "hf_body.msh": "original body",
        "hf_skin.dds": "pale skin",
        "other.mao": "plain file",
    }


def test_broken_config_is_reported_not_fatal(tmp_path: Path, library: Library) -> None:
    mod = library.import_archive(make_override(tmp_path / "Bodies.override"))
    config = library.files_dir(mod.id) / "BioWare/Dragon Age" / BASE / "OverrideConfig.xml"
    config.write_text("<OverrideConfig><oops")
    plan = deploy.build_plan(library)
    assert len(plan.warnings) == 1 and plan.warnings[0].startswith("bodies: cannot read")
    assert contents(library, mod)["hf_body.msh"] == "original body"


def test_apply_deploys_no_package_metadata(
    tmp_path: Path, library: Library, user_dir: Path
) -> None:
    library.import_archive(make_override(tmp_path / "Bodies.override"))
    deploy.apply(deploy.build_plan(library), user_dir, library.dirs.deployed_file)
    deployed = sorted(
        p.relative_to(user_dir).as_posix() for p in user_dir.rglob("*") if p.is_file()
    )
    assert deployed == [
        f"{BASE}/ef_ears.msh",
        f"{BASE}/hf_body.msh",
        f"{BASE}/hf_skin.dds",
        f"{BASE}/other.mao",
    ]
    assert (user_dir / BASE / "hf_body.msh").read_text() == "medium body"
    deploy.unapply(library.dirs.deployed_file)
    assert not (user_dir / "packages").exists()


def test_kinds_with_packages(tmp_path: Path, library: Library) -> None:
    package = ["P.override/Manifest.xml", "P.override/BioWare/Dragon Age/packages/x.erf"]
    dazip = ["D.dazip/Manifest.xml", "D.dazip/Contents/addins/d/a.erf"]
    assert pathops.kind(package) == "package"
    assert pathops.kind([*package, "readme.txt"], library.ignore.matches) == "package"
    assert pathops.kind([*package, "loose.erf"]) == "mixed"
    assert pathops.kind([*package, *dazip]) == "mixed"

    both = make_zip(
        tmp_path / "both.zip",
        {
            "A.dazip": make_dazip(tmp_path / "a.dazip", uid="addin").read_bytes(),
            "B.override": make_override(tmp_path / "b.override").read_bytes(),
        },
    )
    mod = library.import_archive(both)
    assert (mod.name, mod.kind, mod.addin_uids) == ("Test AddIn", KIND_MIXED, ["addin"])
    targets = library.targets(mod)
    assert "addins/addin/module/data/a.gda" in targets and f"{BASE}/hf_body.msh" in targets


def test_broken_nested_package_fails_the_import(tmp_path: Path, library: Library) -> None:
    archive = make_zip(tmp_path / "bad.zip", {"Broken.override": "not a zip"})
    with pytest.raises(LibraryError):
        library.import_archive(archive)
