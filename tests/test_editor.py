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
    topo = [n for n in client.get("/api/tree/topology").json() if n["data"]["type"] == "area"]
    devices = topo[0]["children"][0]["children"]
    assert [d["label"] for d in devices] == ["1.1.5 Taster Flur", "– noch ohne Adresse"]
    gas = client.get("/api/tree/groupaddresses").json()
    assert gas[0]["label"] == "1 Licht" and gas[0]["children"][0]["children"][0]["data"]["address"] == "1/1/1"  # nach Adresse sortiert
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
    assert g["users"] == [{"uid": "P-0123-0_DI-1", "pa": "1.1.5", "device": "Taster Flur", "ref": "O-0_R-1", "number": 0,
                           "object": "Taste 1 – Schalten", "sending": False, "dpt": "1.001", "size": "1 Bit",
                           "flags": "K-SÜ-", "priority": "Niedrig"}]
    assert client.get("/api/ga/1/1/2").json()["users"][0]["sending"] is True


def test_edit_and_save(client, tmp_path):
    d = client.get("/api/device/P-0123-0_DI-1").json()
    assert d["params"] == [] or all(not i["changed"] for g in d["params"] for i in g["items"])
    r = client.put("/api/device/P-0123-0_DI-1/param", json={"id": "gibtsnicht", "value": "1"})
    assert r.status_code == 422 and "nicht sichtbar" in r.json()["detail"]
    assert client.get("/api/project").json()["dirty"] is False
    assert client.post("/api/project/save").json() == {"saved": "p.knxlens"}


def test_topology_buildings_and_products(client, tmp_path):
    from test_catalog import make_knxprod
    r = client.post("/api/op", json={"op": "area_add", "args": {"name": "Garage"}})
    assert r.status_code == 200 and r.json()["result"] == 2
    client.post("/api/op", json={"op": "line_add", "args": {"area": 2}})
    topo = [n for n in client.get("/api/tree/topology").json() if n["data"]["type"] == "area"]
    assert [a["label"] for a in topo] == ["1 Bereich", "2 Garage"]
    assert [l["data"]["line"] for l in topo[1]["children"]] == [0, 1]
    info = client.get("/api/project").json()
    assert info["space_root"] == ["Building"] and info["space_types"]["Room"] == "Raum"
    rooms = client.get("/api/tree/buildings").json()
    haus = rooms[0]["data"]
    assert haus["uid"] and "Floor" in haus["child_types"]
    uid = client.post("/api/op", json={"op": "space_add", "args": {"parent_uid": haus["uid"], "type": "Room", "name": "Bad"}}).json()["result"]
    assert client.post("/api/op", json={"op": "space_assign", "args": {"device_uids": ["P-0123-0_DI-1"], "space_uid": uid}}).status_code == 200
    rooms = client.get("/api/tree/buildings").json()
    bad = next(c for c in rooms[0]["children"] if c["label"] == "Bad")
    assert bad["children"][0]["data"]["uid"] == "P-0123-0_DI-1"
    r = client.post("/api/op", json={"op": "space_add", "args": {"parent_uid": None, "type": "Room"}})
    assert r.status_code == 422 and "nicht erlaubt" in r.json()["detail"]
    # Produktdatei: kein neues Produkt (dieselbe Applikation), aber gültig
    data = make_knxprod(tmp_path / "x.knxprod").read_bytes()
    r = client.post("/api/products?name=x.knxprod", content=data)
    assert r.status_code == 200 and r.json()["products"][0]["name"] == "Tastsensor 2 plus"
    assert client.post("/api/products", content=b"kaputt").status_code == 422
    assert client.get("/api/project").json()["dirty"]


def test_param_change_asks_before_orphaning(client, monkeypatch):
    from knxlens import editor as ed
    effect = [{"ref": "O-0_R-1", "number": 0, "text": "Taste 1", "gas": ["1/1/2", "1/1/1"]}]
    monkeypatch.setattr(ed, "param_change_effect", lambda ap, d, i, v: effect)
    monkeypatch.setattr(ed, "set_param", lambda ap, d, i, v: True)
    monkeypatch.setattr(ed, "rebind_variants", lambda ap, d: 0)
    url = "/api/device/P-0123-0_DI-1/param"
    r = client.put(url, json={"id": "x", "value": "1"})
    assert r.status_code == 409 and r.json()["detail"]["orphans"] == effect
    assert not client.get("/api/project").json()["dirty"]  # nichts geändert
    r = client.put(url, json={"id": "x", "value": "1", "orphans": "keep"})
    assert r.status_code == 200 and any(o["gas"] for o in r.json()["objects"] if o["number"] == 0)
    r = client.put(url, json={"id": "x", "value": "1", "orphans": "unlink"})
    assert r.status_code == 200 and not any(o["number"] == 0 and o["gas"] for o in r.json()["objects"])
    client.post("/api/undo")
    assert any(o["number"] == 0 and o["gas"] for o in client.get("/api/device/P-0123-0_DI-1").json()["objects"])


def test_monitor_rows_decoded(client):
    from xknx.dpt import DPTArray, DPTBinary
    from xknx.telegram import GroupAddress, IndividualAddress, Telegram
    from xknx.telegram.apci import GroupValueWrite
    app = client.app
    mon = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/monitor").__closure__
    monitor = next(c.cell_contents for c in mon if type(c.cell_contents).__name__ == "BusMonitor")
    monitor._received(Telegram(GroupAddress("1/1/1"), source_address=IndividualAddress("1.1.5"),
                               payload=GroupValueWrite(DPTBinary(1))))
    monitor._received(Telegram(GroupAddress("1/2/0"), source_address=IndividualAddress("1.1.9"),
                               payload=GroupValueWrite(DPTArray((0x0C, 0x1A)))))
    r = client.get("/api/monitor").json()
    assert r["state"] == "getrennt" and len(r["rows"]) == 2
    a, b = r["rows"]
    assert a["src_name"] == "Taster Flur" and a["service"] == "Schreiben" and a["raw"] == "1"
    assert b["dst"] == "1/2/0" and b["value"].endswith("°C")  # DPT 9.001 aus dem Projekt
    assert client.get(f"/api/monitor?since={a['id']}").json()["rows"] == [b]
    assert client.post("/api/monitor/start", json={"gateway": " "}).status_code == 422


def test_project_files_new_save_as_versions(client, tmp_path, monkeypatch):
    from knxlens import editor as ed
    monkeypatch.setattr(ed, "RECENT", tmp_path / "recent.json")
    assert client.get("/api/files").json()["files"][0]["current"]
    # Version anlegen, ändern, Unterschied, zurückholen
    v = client.post("/api/versions", json={"comment": "Start"}).json()["id"]
    client.post("/api/op", json={"op": "device_update", "args": {"uid": "P-0123-0_DI-1", "name": "Taster Bad"}})
    d = client.get(f"/api/versions/{v}/diff").json()
    assert any("Name" in c for c in d["devices_changed"])
    client.post(f"/api/versions/{v}/restore")
    assert client.get("/api/device/P-0123-0_DI-1").json()["name"] == "Taster Flur"
    # ungespeichert → Öffnen/Neu fragen zurück
    assert client.post("/api/project/new", json={"name": "Garage"}).status_code == 409
    client.post("/api/project/save")
    r = client.post("/api/project/save_as", json={"name": "Kopie Haus"})
    assert r.status_code == 200 and r.json()["file"] == "Kopie_Haus.knxlens"
    assert [x["comment"] for x in client.get("/api/versions").json()] == ["Start"]  # Verlauf kam mit
    r = client.post("/api/project/new", json={"name": "Garage"})
    assert r.status_code == 200 and r.json()["name"] == "Garage" and r.json()["devices"] == 0
    assert [n for n in client.get("/api/tree/topology").json() if n["data"]["type"] == "area"][0]["children"][1]["data"]["line"] == 1
    assert client.post("/api/project/new", json={"name": "Garage"}).status_code == 409  # gibt es schon
    back = next(f for f in client.get("/api/files").json()["files"] if f["name"] == "p")
    assert client.post("/api/project/open", json={"path": back["path"]}).json()["devices"] == 2
    assert client.post("/api/project/open", json={"path": "/etc/passwd"}).status_code == 422
    exp = client.get("/api/project/export")
    assert exp.status_code == 200 and exp.content[:2] == b"PK"
    r = client.post("/api/project/import?name=Wieder.knxproj", content=exp.content)
    assert r.status_code == 200 and r.json()["file"] == "Wieder.knxlens" and r.json()["devices"] == 2


def test_pending_download_folder(client):
    def pending():
        return [c["data"]["uid"] for c in client.get("/api/tree/topology").json()[0]["children"]]
    assert client.get("/api/device/P-0123-0_DI-1").json()["load"]["par"] is True  # aus dem ETS-Import
    client.post("/api/op", json={"op": "unlink", "args": {"device_uid": "P-0123-0_DI-1", "ref": "O-0_R-1", "address": "1/1/1"}})
    assert "P-0123-0_DI-1" in pending()  # Testprojekt setzt nur ParametersLoaded
    load = client.get("/api/device/P-0123-0_DI-1").json()["load"]
    assert load["grp"] is False and load["par"] is True
