from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from dao_modman.core.library import Library
from dao_modman.core.paths import Dirs

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Manifest Type="AddIn">
  <AddInsList>
    <AddInItem UID="{uid}" Name="{uid}" ExtendedModuleUID="Single Player" Priority="50"
               Enabled="1" State="2" Format="1" BioWare="0">
      <Title DefaultText="{title}" />
      <Version>1.2</Version>
    </AddInItem>
  </AddInsList>
</Manifest>
"""


def make_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def make_dazip(path: Path, uid: str = "test_addin", title: str = "Test AddIn") -> Path:
    return make_zip(
        path,
        {
            "Manifest.xml": MANIFEST.format(uid=uid, title=title),
            f"Contents/addins/{uid}/module/data/a.gda": "gda",
            f"Contents/packages/core/override/{uid}/b.erf": "erf",
        },
    )


OVERRIDE_MANIFEST = """\ufeff<?xml version="1.0" encoding="UTF-8"?>
<OverrideList>
  <OverrideItem Name="{name}" Enabled="1">
    <Title>{name}</Title>
    <Version>2.5</Version>
    <Publisher>Someone</Publisher>
    <URL>http://example.com/mod</URL>
    <Description>A test package</Description>
  </OverrideItem>
</OverrideList>
"""

# Shaped like real files: numbered element names, and a default that differs from its
# value's label in case ("medium" vs "Medium").
OVERRIDE_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<OverrideConfig>
  <Section_0 Name="Humans">
    <Key_0 Name="Body" DefaultValue="medium" OriginalFile="hf_body.msh">
      <Value_0 Value="Big" OptionsFile="(big)hf_body.msh"></Value_0>
      <Value_1 Value="Medium" OptionsFile="(medium)hf_body.msh"></Value_1>
      <Description></Description>
    </Key_0>
    <Key_1 Name="Skin" DefaultValue="Pale" OriginalFile="HF_skin.dds">
      <Value_0 Value="Pale" OptionsFile="(pale)HF_skin.dds"></Value_0>
      <Value_1 Value="Tan" OptionsFile="(tan)HF_skin.dds"></Value_1>
    </Key_1>
  </Section_0>
  <Section_1 Name="Elves">
    <Key_0 Name="Ears" DefaultValue="Long" OriginalFile="ef_ears.msh">
      <Value_0 Value="Long" OptionsFile="(long)ef_ears.msh"></Value_0>
      <Value_1 Value="Short" OptionsFile="(short)ef_ears.msh"></Value_1>
    </Key_0>
  </Section_1>
</OverrideConfig>
"""


SHARED_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<OverrideConfig>
  <Section_0 Name="Human">
    <Key_0 Name="Skin" DefaultValue="Plain" OriginalFile="HF_d.dds">
      <Value_0 Value="Plain" OptionsFile="(plain)skin.dds"></Value_0>
      <Value_1 Value="Tattoo" OptionsFile="(tattoo)skin.dds"></Value_1>
    </Key_0>
  </Section_0>
  <Section_1 Name="Elf">
    <Key_0 Name="Skin" DefaultValue="Plain" OriginalFile="EF_d.dds">
      <Value_0 Value="Plain" OptionsFile="(plain)skin.dds"></Value_0>
      <Value_1 Value="Tattoo" OptionsFile="(tattoo)skin.dds"></Value_1>
    </Key_0>
  </Section_1>
  <Section_2 Name="Dwarf">
    <Key_0 Name="Skin" DefaultValue="Plain" OriginalFile="DF_d.dds">
      <Value_0 Value="Plain" OptionsFile="(plain)skin.dds"></Value_0>
      <Value_1 Value="Tattoo" OptionsFile="(tattoo)skin.dds"></Value_1>
    </Key_0>
  </Section_2>
</OverrideConfig>
"""


def make_shared_override(path: Path, name: str = "Skins") -> Path:
    """A package in which three options draw on the same two variant files."""
    base = f"BioWare/Dragon Age/packages/core/override/{name}"
    return make_zip(
        path,
        {
            "Manifest.xml": OVERRIDE_MANIFEST.format(name=name),
            f"{base}/OverrideConfig.xml": SHARED_CONFIG,
            f"{base}/Default/HF_d.dds": "original human",
            f"{base}/Default/EF_d.dds": "original elf",
            f"{base}/Default/DF_d.dds": "original dwarf",
            f"{base}/Default/hf.mao": "material",
            f"{base}/Options/(plain)skin.dds": "plain skin",
            f"{base}/Options/(tattoo)skin.dds": "tattoo skin",
            f"{base}/Options/(unused)extra.dds": "listed by no option",
        },
    )


def make_override(path: Path, name: str = "Bodies", *, config: bool = True) -> Path:
    """An override package: originals for Body and Skin, variants in a subfolder.

    The Ears option has no original file in the package, only its variants.
    """
    base = f"BioWare/Dragon Age/packages/core/override/{name}"
    files = {
        "Manifest.xml": OVERRIDE_MANIFEST.format(name=name),
        f"{base}/hf_body.msh": "original body",
        f"{base}/hf_skin.dds": "original skin",
        f"{base}/other.mao": "plain file",
        f"{base}/Readme.txt": "readme",
        f"{base}/Variants/(big)hf_body.msh": "big body",
        f"{base}/Variants/(medium)hf_body.msh": "medium body",
        f"{base}/Variants/(pale)HF_skin.dds": "pale skin",
        f"{base}/Variants/(tan)HF_skin.dds": "tan skin",
        f"{base}/Variants/(long)ef_ears.msh": "long ears",
        f"{base}/Variants/(short)ef_ears.msh": "short ears",
    }
    if config:
        files[f"{base}/OverrideConfig.xml"] = OVERRIDE_CONFIG
    return make_zip(path, files)


@pytest.fixture
def dirs(tmp_path: Path) -> Dirs:
    return Dirs(data=tmp_path / "data", downloads=tmp_path / "data" / "downloads")


@pytest.fixture
def library(dirs: Dirs) -> Library:
    return Library(dirs)


@pytest.fixture
def user_dir(tmp_path: Path) -> Path:
    path = tmp_path / "Dragon Age"
    path.mkdir()
    return path
