from __future__ import annotations

from pathlib import Path

import pytest

from dao_modman.core import addins

# Shaped like the game's own file: BOM, lower-case name, CDATA, Version as a child element.
ADDINS_XML = """﻿<?xml version="1.0" encoding="UTF-8" ?>
<AddInsList>
    <AddInItem UID="DAO_PRC_EP_1" Name="PRC_EP1" ExtendedModuleUID="" Priority="110"
               Enabled="1" State="2" Format="1" BioWare="1">
        <Title DefaultText="Dragon Age: Awakening">
            <en-us>
                <![CDATA[Dragon Age: Awakening]]>
</en-us>
        </Title>
        <Version>1.05</Version>
    </AddInItem>
    <AddInItem UID="DAO_PRC_CP_2" Name="Shale" ExtendedModuleUID="Single Player"
               Enabled="0" BioWare="1">
        <Title DefaultText="The Stone Prisoner" />
    </AddInItem>
    <AddInItem UID="lock_bash" Name="Lock Bash" ExtendedModuleUID="Single Player"
               Enabled="1" BioWare="0">
        <Title DefaultText="Lock Bash" />
        <Version>1.14</Version>
    </AddInItem>
    <AddInItem UID="pretender" Name="Pretender" Enabled="1" BioWare="1" />
    <AddInItem Name="no uid" />
</AddInsList>
"""


@pytest.fixture
def game(tmp_path: Path) -> tuple[Path, Path]:
    user_dir, install_dir = tmp_path / "user", tmp_path / "install"
    (user_dir / "Settings").mkdir(parents=True)
    (user_dir / "Settings/addins.xml").write_text(ADDINS_XML, encoding="utf-8")
    for uid in ("dao_prc_ep_1", "dao_prc_cp_2"):
        (install_dir / "Addins" / uid).mkdir(parents=True)
    return user_dir, install_dir


def test_list_registered(game: tuple[Path, Path]) -> None:
    registered = addins.list_registered(game[0])
    assert [(a.uid, a.name, a.version, a.enabled, a.bioware) for a in registered] == [
        ("DAO_PRC_EP_1", "Dragon Age: Awakening", "1.05", True, True),
        ("DAO_PRC_CP_2", "The Stone Prisoner", "", False, True),
        ("lock_bash", "Lock Bash", "1.14", True, False),
        ("pretender", "Pretender", "", True, True),
    ]
    assert registered[1].extends == "Single Player"


def test_list_registered_missing_or_broken(tmp_path: Path) -> None:
    assert addins.list_registered(tmp_path) == []
    (tmp_path / "Settings").mkdir()
    (tmp_path / "Settings/AddIns.xml").write_text("<AddInsList><oops")
    with pytest.raises(addins.AddInsError):
        addins.list_registered(tmp_path)
    (tmp_path / "Settings/AddIns.xml").write_text("<Other/>")
    with pytest.raises(addins.AddInsError):
        addins.list_registered(tmp_path)


def test_shipped_uids(game: tuple[Path, Path], tmp_path: Path) -> None:
    assert addins.shipped_uids(game[1]) == {"dao_prc_ep_1", "dao_prc_cp_2"}
    assert addins.shipped_uids(None) == set()
    assert addins.shipped_uids(tmp_path / "missing") == set()


def test_classify(game: tuple[Path, Path]) -> None:
    by_uid = {addin.uid: addin for addin in addins.list_registered(game[0])}
    shipped = addins.shipped_uids(game[1])

    def kind(uid: str, managed: set[str] = frozenset(), shipped: set[str] = shipped) -> str:
        return addins.classify(by_uid[uid], set(managed), shipped)

    assert kind("DAO_PRC_EP_1") == addins.OFFICIAL
    assert kind("lock_bash") == addins.EXTERNAL
    assert kind("lock_bash", managed={"lock_bash"}) == addins.MANAGED
    # shipped with the game: official even without trusting the attribute
    by_uid["DAO_PRC_CP_2"].bioware = False
    assert kind("DAO_PRC_CP_2") == addins.OFFICIAL
    # install dir unknown: the BioWare attribute is all there is
    assert kind("DAO_PRC_EP_1", shipped=set()) == addins.OFFICIAL
    # a mod of ours that claims to be BioWare content is still ours
    assert kind("pretender") == addins.OFFICIAL
    assert kind("pretender", managed={"pretender"}) == addins.MANAGED


def test_uids_are_compared_ignoring_case(tmp_path: Path) -> None:
    """A UID already registered in other casing must not get a second entry."""
    import xml.etree.ElementTree as ET

    from dao_modman.core.dazip import addin_from_element

    path = tmp_path / "addins.xml"
    path.write_text('<AddInsList><AddInItem UID="Phae_MainModule" /></AddInsList>')
    new = [
        addin_from_element(ET.fromstring(f'<AddInItem UID="{uid}" />'))
        for uid in ("phae_mainmodule", "phae_twforgedaa", "PHAE_TWFORGEDAA")
    ]
    assert addins.register(path, new) == ["phae_twforgedaa"]

    def registered() -> list[str]:
        return [item.get("UID") for item in ET.parse(path).getroot()]

    assert registered() == ["Phae_MainModule", "phae_twforgedaa"]
    assert addins.classify(new[0], {"PHAE_MAINMODULE"}, set()) == addins.MANAGED
    assert addins.unregister(path, ["PHAE_TwForgeDAA"]) == ["phae_twforgedaa"]
    assert registered() == ["Phae_MainModule"]
