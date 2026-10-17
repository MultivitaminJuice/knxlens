"""Projektmodell: Datei, .knxproj-Import/-Export, Rekonstruktion."""

import dataclasses
import zipfile

from defusedxml import ElementTree as ET

from knxlens.catalog import Application, Catalog, ObjectVariant
from knxlens.model import Device as BusDevice
from knxlens.project import Project
from knxlens.project.knxproj_io import export_knxproj, import_knxproj
from knxlens.project.reconstruct import co_ref, from_reconstruction
from test_catalog import HARDWARE, APPLICATION, APP, make_knxprod

NS = "http://knx.org/xml/project/20"

INSTALLATION = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{NS}" CreatedBy="ETS6" ToolVersion="6.4"><Project Id="P-0123"><Installations>
<Installation Name="" BCUKey="4294967295" IPRoutingLatencyTolerance="2000">
 <Topology><Area Id="P-0123-0_A-2" Address="1" Puid="4"><Line Id="P-0123-0_L-3" Address="1" Puid="7">
  <Segment Id="P-0123-0_S-3" Number="0" MediumTypeRefId="MT-0" Puid="8">
   <DeviceInstance Id="P-0123-0_DI-1" Address="5" Name="Taster Flur" ProductRefId="M-0008_H-1_P-1"
     Hardware2ProgramRefId="M-0008_H-1_HP-1" Comment="" Description="" SerialNumber="AAgBAgME"
     ParametersLoaded="true" LastModified="2026-01-01T00:00:00Z" Puid="9">
    <ParameterInstanceRefs><ParameterInstanceRef RefId="{APP}_P-1_R-1" Value="3" /></ParameterInstanceRefs>
    <ComObjectInstanceRefs>
     <ComObjectInstanceRef RefId="O-0_R-1" Links="GA-2 GA-1" />
     <ComObjectInstanceRef RefId="O-2_R-4" DatapointType="DPST-9-1" Links="GA-3" />
    </ComObjectInstanceRefs>
    <GroupObjectTree GroupObjectInstances="1 2"><Nodes><Node Type="Channel" RefId="X" /></Nodes></GroupObjectTree>
   </DeviceInstance>
   <DeviceInstance Id="P-0123-0_DI-2" Name="noch ohne Adresse" ProductRefId="M-0008_H-1_P-1"
     Hardware2ProgramRefId="M-0008_H-1_HP-1" Comment="" Description="" Puid="10" />
  </Segment></Line></Area></Topology>
 <Locations><Space Type="Building" Id="P-0123-0_BP-1" Name="Haus" Puid="11">
   <Space Type="Room" Id="P-0123-0_BP-2" Name="Flur" Usage="SU-1" Puid="12">
     <Space Type="DistributionBoard" Id="P-0123-0_BP-3" Name="UV" Puid="13"><DeviceInstanceRef RefId="P-0123-0_DI-2" /></Space>
     <DeviceInstanceRef RefId="P-0123-0_DI-1" /></Space></Space></Locations>
 <GroupAddresses><GroupRanges>
  <GroupRange Id="P-0123-0_GR-1" RangeStart="2048" RangeEnd="4095" Name="Licht" Puid="14">
   <GroupRange Id="P-0123-0_GR-2" RangeStart="2304" RangeEnd="2559" Name="Schalten" Puid="15">
    <GroupAddress Id="P-0123-0_GA-2" Address="2306" Name="Flur 2" DatapointType="DPST-1-1" Puid="16" />
    <GroupAddress Id="P-0123-0_GA-1" Address="2305" Name="Flur 1" DatapointType="DPST-1-1" Central="true" Puid="17" />
   </GroupRange>
   <GroupAddress Id="P-0123-0_GA-3" Address="2560" Name="Temperatur" Puid="18" />
  </GroupRange></GroupRanges></GroupAddresses>
</Installation></Installations></Project></KNX>"""

PROJECT = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{NS}"><Project Id="P-0123"><ProjectInformation Name="Testhaus" GroupAddressStyle="ThreeLevel"
 LastUsedPuid="18" Guid="g"><ProjectTraces><ProjectTrace Date="2026" UserName="x" Comment="y" /></ProjectTraces>
</ProjectInformation></Project></KNX>"""


def make_knxproj(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("knx_master.xml", "<KNX/>")
        z.writestr("M-0008/Hardware.xml", HARDWARE)
        z.writestr(f"M-0008/{APP}.xml", APPLICATION)
        z.writestr("M-0008.signature", "SIG")
        z.writestr("P-0123/project.xml", PROJECT)
        z.writestr("P-0123/0.xml", INSTALLATION)
        z.writestr("P-0123.signature", "ETS-SIGNATUR")
    return path


def canon(path, member):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(f"P-0123/{member}"))
    out = []

    def walk(el, prefix):
        tag = el.tag.rsplit("}", 1)[-1]
        here = f"{prefix}/{tag}[{el.get('Id') or el.get('RefId') or ''}]"
        out.append((here, tuple(sorted((k, v) for k, v in el.attrib.items()
                                       if k not in ("Puid", "LastUsedPuid", "CreatedBy", "ToolVersion")))))
        for c in el:
            walk(c, here)

    walk(root, "")
    return out


def test_import_model(tmp_path):
    p = import_knxproj(make_knxproj(tmp_path / "in.knxproj"))
    assert p.name == "Testhaus" and p.project_id == "P-0123" and p.namespace == NS
    a, b = p.devices
    assert a.pa == "1.1.5" and b.pa is None and a.serial == "000801020304"
    assert a.app_id == APP and a.params == {f"{APP}_P-1_R-1": "3"}
    assert [(l.ref, l.gas, l.extra) for l in a.links] == [
        ("O-0_R-1", ["1/1/2", "1/1/1"], {}), ("O-2_R-4", ["1/2/0"], {"DatapointType": "DPST-9-1"})]
    assert list(p.group_addresses) == ["1/1/2", "1/1/1", "1/2/0"]  # ETS-Reihenfolge, nicht sortiert
    assert p.group_addresses["1/1/1"].extra == {"Id": "P-0123-0_GA-1", "Central": "true"}
    assert [s.name for s in p.spaces] == ["Haus", "Flur", "UV"] and p.spaces[1].devices == ["P-0123-0_DI-1"]
    assert "P-0123.signature" not in p.products  # Projektsignatur wird nicht übernommen


def test_roundtrip_xml_identical(tmp_path):
    src = make_knxproj(tmp_path / "in.knxproj")
    p = import_knxproj(src)
    p.save(tmp_path / "p.knxlens")
    q = Project.load(tmp_path / "p.knxlens")
    assert dataclasses.asdict(p) == dataclasses.asdict(q)
    export_knxproj(q, str(tmp_path / "out.knxproj"))
    for member in ("0.xml", "project.xml"):
        assert canon(src, member) == canon(tmp_path / "out.knxproj", member)  # inkl. Reihenfolge
    with zipfile.ZipFile(tmp_path / "out.knxproj") as z:
        assert not any(n.startswith("P-") and n.endswith(".signature") for n in z.namelist())
        assert z.read("M-0008.signature") == b"SIG"


def test_co_ref_module_conversion():
    app = Application(key="M-0083_A-000B-23", full_id="M-0083_A-000B-23-25DE", name="", mask="", has_modules=True)

    class Ev:
        active_cos = {0: "M-0083_A-000B-23-25DE_MD-2_M-4|M-0083_A-000B-23-25DE_MD-2_O-2-0_R-1",
                      5: "M-0083_A-000B-23-25DE_O-5_R-9"}

    assert co_ref(app, Ev(), 0) == "MD-2_M-4_MI-1_O-2-0_R-1"
    assert co_ref(app, Ev(), 5) == "O-5_R-9"
    app.variant_by_ref = {"M-0083_A-000B-23-25DE_O-7_R-2": ObjectVariant("a", "", None, None, {})}
    app.ref_number = {"M-0083_A-000B-23-25DE_O-7_R-2": 7}
    assert co_ref(app, None, 7) == "O-7_R-2"
    assert co_ref(app, None, 8) is None


def test_reconstruction_to_knxproj(tmp_path):
    cat = Catalog()
    cat.add(make_knxprod(tmp_path / "gira.knxprod", {"M-0008.signature": "SIG", "knx_master.xml": "<KNX/>"}))
    dev = BusDevice(pa="1.1.5", mask=0x0012, app_manufacturer=8, app_number=0x2032, app_version=0x12,
                    serial="00:08:01:02:03:04", links={(0, "1/0/1"), (2, "1/0/1"), (2, "3/1/7")})
    p, stats = from_reconstruction("Test", [dev], cat, {}, ga_names={"1/0/1": "Flur"}, range_names={"1": "Licht"},
                                   dpts={"3/1/7": {"main": 9, "sub": 1}}, device_notes={"1.1.5": {"room": "Flur"}})
    assert stats["devices"] == 1 and not stats["objects_unknown"]
    export_knxproj(p, str(tmp_path / "out.knxproj"))
    q = import_knxproj(tmp_path / "out.knxproj")
    d = q.devices[0]
    assert (d.pa, d.product_ref, d.h2p_ref, d.app_id, d.serial) == (
        "1.1.5", "M-0008_H-1_P-1", "M-0008_H-1_HP-1", APP, "000801020304")
    assert sorted(g for l in d.links for g in l.gas) == ["1/0/1", "1/0/1", "3/1/7"]
    assert q.group_addresses["1/0/1"].name == "Flur" and q.group_addresses["3/1/7"].dpt == "DPST-9-1"
    assert next(r for r in q.ranges if r.start == 2048).name == "Licht"
    assert [s.name for s in q.spaces if s.type == "Room"] == ["Flur"]
