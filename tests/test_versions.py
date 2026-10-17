"""Versionen in der Projektdatei: anlegen, auflisten, zurückholen, beim Speichern behalten."""

import dataclasses

from knxlens.project import Project, versions
from knxlens.project.knxproj_io import import_knxproj
from test_project import make_knxproj


def test_versions_roundtrip_and_survive_save(tmp_path):
    p = import_knxproj(make_knxproj(tmp_path / "in.knxproj"))
    path = tmp_path / "p.knxlens"
    p.save(path)
    v1 = versions.add(path, p, "Ausgangsstand")
    p.devices[0].name = "Taster Bad"
    p.devices[0].params["X_P-9_R-9"] = "2"
    del p.group_addresses["1/2/0"]
    p.save(path)  # neue Datei – die Versionen müssen mitkommen
    v2 = versions.add(path, p, "Bad umbenannt")
    lst = versions.listing(path)
    assert [v["id"] for v in lst] == [v2, v1] and lst[1]["comment"] == "Ausgangsstand"
    assert lst[0]["stats"]["group_addresses"] == 2
    old = versions.get(path, v1, p.products)
    assert old.devices[0].name == "Taster Flur" and "1/2/0" in old.group_addresses
    a = dataclasses.asdict(import_knxproj(make_knxproj(tmp_path / "in2.knxproj")))
    b = dataclasses.asdict(old)
    a.pop("products"), b.pop("products")
    assert a == b  # verlustfrei
    d = versions.diff(old, Project.load(path))
    assert d["gas_removed"] == ["1/2/0"] and any("Name" in c and "1 Parameter" in c for c in d["devices_changed"])
