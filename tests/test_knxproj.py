"""Projektexport: Struktur, Verknüpfungen, Modul-Ids."""

import zipfile

from defusedxml import ElementTree as ET

from knxrecon import knxproj
from knxrecon.catalog import Application, Catalog, ObjectVariant
from knxrecon.model import Device
from test_catalog import make_knxprod

NS = "{http://knx.org/xml/project/20}"


def test_co_ref_module_conversion():
    app = Application(key="M-0083_A-000B-23", full_id="M-0083_A-000B-23-25DE", name="", mask="", has_modules=True)

    class Ev:
        active_cos = {0: "M-0083_A-000B-23-25DE_MD-2_M-4|M-0083_A-000B-23-25DE_MD-2_O-2-0_R-1",
                      5: "M-0083_A-000B-23-25DE_O-5_R-9"}

    assert knxproj._co_ref(app, Ev(), 0) == "MD-2_M-4_MI-1_O-2-0_R-1"
    assert knxproj._co_ref(app, Ev(), 5) == "O-5_R-9"
    # ohne aktive Variante: erste Variante der Nummer statt Verknüpfung zu verlieren
    app.variant_by_ref = {"M-0083_A-000B-23-25DE_O-7_R-2": ObjectVariant("a", "", None, None, {})}
    app.ref_number = {"M-0083_A-000B-23-25DE_O-7_R-2": 7}
    assert knxproj._co_ref(app, None, 7) == "O-7_R-2"
    assert knxproj._co_ref(app, None, 8) is None


def test_write_project(tmp_path):
    cat = Catalog()
    cat.add(make_knxprod(tmp_path / "gira.knxprod", {"M-0008.signature": "SIG", "knx_master.xml": "<KNX/>"}))
    dev = Device(pa="1.1.5", mask=0x0012, app_manufacturer=8, app_number=0x2032, app_version=0x12,
                 serial="00:08:01:02:03:04", links={(0, "1/0/1"), (2, "1/0/1"), (2, "3/1/7")})
    inp = knxproj.ProjectInput(name="Test", devices=[dev], catalog=cat, evaluations={},
                               ga_names={"1/0/1": "Flur"}, range_names={"1": "Licht"},
                               dpts={"3/1/7": {"main": 9, "sub": 1}}, device_notes={"1.1.5": {"room": "Flur"}})
    stats = knxproj.write(inp, str(tmp_path / "out.knxproj"))
    assert stats["devices"] == 1 and stats["links"] == 3 and stats["gas"] == 2
    with zipfile.ZipFile(tmp_path / "out.knxproj") as z:
        names = set(z.namelist())
        assert {"knx_master.xml", "M-0008.signature", "M-0008/Hardware.xml", "P-0E7A/0.xml", "P-0E7A/project.xml"} <= names
        assert not any(n.startswith("P-") and n.endswith(".signature") for n in names)  # keine gefälschte Signatur
        root = ET.fromstring(z.read("P-0E7A/0.xml"))
    di = root.find(f".//{NS}DeviceInstance")
    assert di.get("Address") == "5" and di.get("ProductRefId") == "M-0008_H-1_P-1"
    assert di.get("Hardware2ProgramRefId") == "M-0008_H-1_HP-1" and di.get("SerialNumber") == "AAgBAgME"
    refs = {r.get("RefId"): r.get("Links") for r in root.iter(f"{NS}ComObjectInstanceRef")}
    assert set(refs.values()) == {"GA-1", "GA-1 GA-2"}
    gas = {g.get("Address"): (g.get("Name"), g.get("DatapointType")) for g in root.iter(f"{NS}GroupAddress")}
    assert gas == {"2049": ("Flur", None), "6407": ("GA 3/1/7", "DPST-9-1")}
    assert root.find(f".//{NS}Space[@Type='Room']").get("Name") == "Flur"
