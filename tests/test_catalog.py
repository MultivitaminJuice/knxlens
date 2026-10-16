"""Produktdaten-Parser gegen eine selbst gebaute .knxprod (Aufbau wie eine BCU1-Applikation)."""

import zipfile

import pytest
from defusedxml import EntitiesForbidden

from knxlens import catalog
from knxlens.catalog import Catalog, ga_dpts, parse_dpt
from knxlens.model import Device, config_byte

NS = 'xmlns="http://knx.org/xml/project/20"'
APP = "M-0008_A-2032-12-ABCD"

HARDWARE = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX {NS}><ManufacturerData><Manufacturer RefId="M-0008"><Hardware>
  <Hardware Id="M-0008_H-1" Name="TS2plus">
    <Products><Product Id="M-0008_H-1_P-1" Text="Push button sensor 2 plus" OrderNumber="1052 00"/></Products>
    <Hardware2Programs><Hardware2Program Id="M-0008_H-1_HP-1"><ApplicationProgramRef RefId="{APP}"/></Hardware2Program></Hardware2Programs>
  </Hardware>
</Hardware>
<Languages><Language Identifier="de-DE"><TranslationUnit RefId="M-0008_H-1">
  <TranslationElement RefId="M-0008_H-1_P-1"><Translation AttributeName="Text" Text="Tastsensor 2 plus"/></TranslationElement>
</TranslationUnit></Language></Languages>
</Manufacturer></ManufacturerData></KNX>"""

APPLICATION = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX {NS}><ManufacturerData><Manufacturer RefId="M-0008"><ApplicationPrograms>
  <ApplicationProgram Id="{APP}" Name="TS2plus RTR" MaskVersion="MV-0012" ApplicationNumber="8242" ApplicationVersion="18">
    <Static>
      <ComObjectTable>
        <ComObject Id="{APP}_O-0" Name="o0" Text="Button 1" FunctionText="Switching" Number="0" ObjectSize="1 Bit"
                   CommunicationFlag="Enabled" ReadFlag="Disabled" WriteFlag="Enabled" TransmitFlag="Enabled" DatapointType="DPST-1-1"/>
        <ComObject Id="{APP}_O-1" Name="o1" Text="Button 1" FunctionText="Dimming" Number="1" ObjectSize="4 Bit"
                   CommunicationFlag="Enabled" WriteFlag="Enabled" TransmitFlag="Enabled" DatapointType="DPST-3-7"/>
        <ComObject Id="{APP}_O-2" Name="o2" Text="Actual temperature" FunctionText="Output" Number="2" ObjectSize="2 Bytes"
                   CommunicationFlag="Enabled" ReadFlag="Enabled" TransmitFlag="Enabled" DatapointType="DPT-9 DPST-9-1"/>
      </ComObjectTable>
      <ComObjectRefs>
        <ComObjectRef Id="{APP}_O-0_R-1" RefId="{APP}_O-0"/>
        <ComObjectRef Id="{APP}_O-0_R-2" RefId="{APP}_O-0" FunctionText="Value" ObjectSize="1 Byte" DatapointType="DPST-5-1"/>
        <ComObjectRef Id="{APP}_O-1_R-3" RefId="{APP}_O-1"/>
        <ComObjectRef Id="{APP}_O-2_R-4" RefId="{APP}_O-2"/>
      </ComObjectRefs>
    </Static>
  </ApplicationProgram>
</ApplicationPrograms>
<Languages><Language Identifier="de-DE"><TranslationUnit RefId="{APP}">
  <TranslationElement RefId="{APP}_O-0"><Translation AttributeName="Text" Text="Taste 1"/>
    <Translation AttributeName="FunctionText" Text="Schalten"/></TranslationElement>
  <TranslationElement RefId="{APP}_O-0_R-2"><Translation AttributeName="FunctionText" Text="Wert"/></TranslationElement>
</TranslationUnit></Language></Languages>
</Manufacturer></ManufacturerData></KNX>"""


def make_knxprod(path, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("M-0008/Hardware.xml", HARDWARE)
        z.writestr(f"M-0008/{APP}.xml", APPLICATION)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


@pytest.fixture
def cat(tmp_path):
    c = Catalog()
    assert c.add(make_knxprod(tmp_path / "gira.knxprod")) == 1
    return c


def test_parse_dpt():
    assert parse_dpt("DPT-9 DPST-9-1") == {"main": 9, "sub": 1}
    assert parse_dpt("DPT-5") == {"main": 5, "sub": None}
    assert parse_dpt("") is None


def test_product_and_translations(cat):
    app = cat.apps["M-0008_A-2032-12"]
    assert app.products == [("Tastsensor 2 plus", "1052 00")]
    labels = [v.label for v in app.variants(0)]
    assert labels == ["Taste 1 – Schalten", "Taste 1 – Wert"]  # deutsch, Ref überschreibt FunctionText
    assert app.variants(2)[0].label == "Actual temperature – Output"  # ohne Übersetzung: Original


def test_variants_narrowed_by_bus_data(cat):
    app = cat.apps["M-0008_A-2032-12"]
    assert app.object_dpt(0) is None  # zwei Varianten mit verschiedenem DPT
    one_bit = config_byte(0, 0b0101_0100, 0, b"")  # K S Ü, 1 Bit
    assert [v.label for v in app.variants(0, one_bit)] == ["Taste 1 – Schalten"]
    assert app.object_dpt(0, one_bit) == {"main": 1, "sub": 1}
    assert app.object_dpt(2) == {"main": 9, "sub": 1}


def test_lookup_and_ga_dpts(cat):
    sensor = Device(pa="1.1.5", mask=0x0012, app_manufacturer=8, app_number=0x2032, app_version=0x12,
                    links={(0, "1/0/1"), (2, "3/1/7")}, objects=[config_byte(0, 0b0101_0100, 0, b"")])
    unknown = Device(pa="1.1.6", mask=0x0012, app_manufacturer=8, app_number=0x9999, app_version=1,
                     links={(0, "1/0/1")})
    assert cat.lookup(sensor) is not None and cat.lookup(unknown) is None
    assert ga_dpts([sensor, unknown], cat) == {"1/0/1": {"main": 1, "sub": 1}, "3/1/7": {"main": 9, "sub": 1}}
    # widersprüchlicher Haupttyp → kein GA-DPT
    clash = Device(pa="1.1.7", mask=0x0012, app_manufacturer=8, app_number=0x2032, app_version=0x12,
                   links={(2, "1/0/1")})
    assert "1/0/1" not in ga_dpts([sensor, clash], cat)


def test_entity_bomb_rejected(tmp_path):
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><KNX>&b;</KNX>'
    path = make_knxprod(tmp_path / "evil.knxprod", {"M-0008/M-0008_A-1111-11-0000.xml": bomb})
    with pytest.raises(EntitiesForbidden):
        Catalog().add(path)


def test_oversized_member_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_MEMBER", 100)
    c = Catalog()
    assert c.add(make_knxprod(tmp_path / "big.knxprod")) == 0
    assert len(c.skipped) == 2
