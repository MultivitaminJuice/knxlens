"""Schutz der lokalen Oberflächen: Host-Prüfung (DNS-Rebinding) und Herkunft (CSRF)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knxlens import websafety


def _client(hosts=websafety.LOOPBACK):
    app = FastAPI()
    websafety.install(app, hosts)

    @app.get("/r")
    def r():
        return {"ok": True}

    @app.post("/w")
    def w():
        return {"ok": True}

    return TestClient(app, base_url="http://127.0.0.1:8770")


def test_same_origin_and_tools_allowed():
    c = _client()
    assert c.get("/r").status_code == 200
    assert c.post("/w").status_code == 200  # ohne Origin: curl, Tests
    assert c.post("/w", headers={"Origin": "http://127.0.0.1:8770", "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.get("/r", headers={"Host": "localhost:8770"}).status_code == 200
    assert c.get("/r", headers={"Host": "[::1]:8770"}).status_code == 200


def test_foreign_site_rejected():
    c = _client()
    assert c.post("/w", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/w", headers={"Origin": "null"}).status_code == 403
    assert c.post("/w", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/w", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert c.post("/w", headers={"Origin": "http://localhost:8770"}).status_code == 403  # anderer Name = andere Herkunft


def test_dns_rebinding_rejected():
    c = _client()
    assert c.get("/r", headers={"Host": "evil.example:8770"}).status_code == 421


def test_allowed_hosts():
    assert websafety.allowed_hosts("127.0.0.1") == websafety.LOOPBACK | {"127.0.0.1"}
    assert "odroid.tail1234.ts.net" in websafety.allowed_hosts("100.64.0.5", ["odroid.tail1234.ts.net"])
    assert websafety.allowed_hosts("0.0.0.0") is None  # ohne Freigabe: keine Host-Prüfung (Kommandozeile warnt)
    assert websafety.allowed_hosts("0.0.0.0", ["nas"]) == websafety.LOOPBACK | {"nas"}
    c = _client(websafety.allowed_hosts("100.64.0.5", ["odroid"]))
    assert c.get("/r", headers={"Host": "odroid:8770"}).status_code == 200
    assert c.get("/r", headers={"Host": "100.64.0.5:8770"}).status_code == 200
