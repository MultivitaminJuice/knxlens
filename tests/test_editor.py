"""Editor-API gegen ein kleines ETS-artiges Projekt."""

import pytest
from fastapi.testclient import TestClient

from knxlens.editor import create_editor_app
from knxlens.project import products
from knxlens.project.knxproj_io import import_knxproj
from test_project import make_knxproj


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(products, "CACHE", tmp_path / "cache")
    p = import_knxproj(make_knxproj(tmp_path / "in.knxproj"))
    p.save(tmp_path / "p.knxlens")
    return TestClient(create_editor_app(str(tmp_path / "p.knxlens")))


def test_project_and_trees(client):
    assert client.get("/api/project").json()["devices"] == 2
    topo = client.get("/api/tree/topology").json()
    devices = topo[0]["children"][0]["children"]
    assert [d["label"] for d in devices] == ["1.1.5 Taster Flur", "– noch ohne Adresse"]
    gas = client.get("/api/tree/groupaddresses").json()
    assert gas[0]["label"] == "Licht" and gas[0]["children"][0]["children"][0]["data"]["address"] == "1/1/2"
    rooms = client.get("/api/tree/buildings").json()
    assert rooms[0]["label"] == "Haus" and rooms[0]["children"][0]["label"] == "Flur"


def test_device_detail(client):
    d = client.get("/api/device/P-0123-0_DI-1").json()
    assert d["pa"] == "1.1.5" and d["product"] == "Tastsensor 2 plus" and d["has_products"]
    objs = {o["number"]: o for o in d["objects"]}
    assert [g["address"] for g in objs[0]["gas"]] == ["1/1/2", "1/1/1"]
    assert objs[0]["text"] == "Taste 1" and objs[2]["dpt"] == "9.001"
    assert client.get("/api/device/gibtsnicht").status_code == 404


def test_ga_detail(client):
    g = client.get("/api/ga/1/1/1").json()
    assert g["name"] == "Flur 1" and g["central"]
    assert g["users"] == [{"uid": "P-0123-0_DI-1", "pa": "1.1.5", "device": "Taster Flur", "number": 0,
                           "object": "Taste 1 – Schalten", "sending": False}]
    assert client.get("/api/ga/1/1/2").json()["users"][0]["sending"] is True


def test_edit_and_save(client, tmp_path):
    d = client.get("/api/device/P-0123-0_DI-1").json()
    assert d["params"] == [] or all(not i["changed"] for g in d["params"] for i in g["items"])
    r = client.put("/api/device/P-0123-0_DI-1/param", json={"id": "gibtsnicht", "value": "1"})
    assert r.status_code == 422 and "nicht sichtbar" in r.json()["detail"]
    assert client.get("/api/project").json()["dirty"] is False
    assert client.post("/api/project/save").json() == {"saved": "p.knxlens"}
