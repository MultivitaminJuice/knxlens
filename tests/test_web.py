"""Weboberfläche gegen eine kleine Datenbank mit einem nachgebauten BCU1-Gerät."""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from knxrecon.web import create_app
from test_bcu1 import PA, run_read, bcu1_image


@pytest.fixture
def client(tmp_path):
    store, _, _ = run_read(tmp_path, bcu1_image())
    store.probe(PA, "device", 0x0012, 300)
    return TestClient(create_app(str(tmp_path / "t.sqlite")))


def test_pages_render(client):
    for url in ("/", f"/device/{PA}", "/gas", "/gas?q=1/0"):
        r = client.get(url)
        assert r.status_code == 200, url
    assert "M-0008_A-2032-12" in client.get("/").text
    page = client.get(f"/device/{PA}").text
    assert "KLSÜ-" in page
    assert f"<title>{PA} · KNX-Rekonstruktion</title>" in page
    assert client.get("/device/9.9.9").status_code == 404
    assert client.get("/compare").status_code == 404  # ohne Referenz


def test_note_roundtrip(client):
    r = client.post("/api/note", json={"kind": "ga", "key": "1/0/1", "field": "name", "value": " Flur Licht "})
    assert r.status_code == 200
    assert 'value="Flur Licht"' in client.get("/gas").text
    csv = client.get("/export/gruppenadressen.csv").content.decode("utf-8-sig")
    assert "1/0/1;Flur Licht;1.1.5/0" in csv
    client.post("/api/note", json={"kind": "ga", "key": "1/0/1", "field": "name", "value": ""})
    assert 'value="Flur Licht"' not in client.get("/gas").text


def test_ets_export(client):
    client.post("/api/note", json={"kind": "ga", "key": "1/0/1", "field": "name", "value": "Flur"})
    client.post("/api/note", json={"kind": "range", "key": "1", "field": "name", "value": "Licht"})
    xml = client.get("/export/gruppenadressen-ets.xml").text
    assert 'xmlns="http://knx.org/xml/ga-export/01"' in xml
    assert '<GroupRange Name="Licht" RangeStart="2048" RangeEnd="4095">' in xml
    assert '<GroupAddress Name="Flur" Address="1/0/1"' in xml


def test_project_export_needs_products(client):
    assert client.get("/export/projekt.knxproj").status_code == 400


def test_note_rejects_unknown_fields(client):
    assert client.post("/api/note", json={"kind": "ga", "key": "1/0/1", "field": "dpt", "value": "x"}).status_code == 400
    assert client.post("/api/note", json={"kind": "bus", "key": "x", "field": "name", "value": "x"}).status_code == 400
    assert client.post("/api/note", json={"kind": "ga", "key": "1/0/1", "field": "name", "value": "x" * 201}).status_code == 400


def test_web_never_imports_bus_code():
    """Die Oberfläche darf nicht senden können: keine Verbindung, kein Management."""
    tree = ast.parse((Path(__file__).parents[1] / "knxrecon/web/__init__.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names} | {node.module or ""}
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
    forbidden = {"bus", "scan", "XKNX", "P2PConnection"}
    assert not imported & forbidden, imported
    assert not any(m.startswith(("xknx.io", "xknx.management", "xknx.xknx")) for m in imported), imported
