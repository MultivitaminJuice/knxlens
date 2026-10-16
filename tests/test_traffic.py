"""Mitschnitt: Telegrammumwandlung, Auswertung, Live-API."""

from fastapi.testclient import TestClient
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci

from knxlens import monitor, traffic
from knxlens.model import Device
from knxlens.web import create_app
from test_bcu1 import PA, bcu1_image, run_read


def telegram(src, dst, payload):
    return Telegram(source_address=IndividualAddress(src), destination_address=GroupAddress(dst), payload=payload)


def test_row_bits_and_bytes():
    assert monitor.row(telegram("1.1.5", "1/0/1", apci.GroupValueWrite(DPTBinary(1))), 1.0) == (
        1.0, "1.1.5", "1/0/1", "GroupValueWrite", None, 1)
    assert monitor.row(telegram("1.1.5", "1/0/2", apci.GroupValueResponse(DPTArray((0x0C, 0x1A)))), 2.0)[4:] == (
        b"\x0c\x1a", None)
    assert monitor.row(telegram("1.1.5", "1/0/2", apci.GroupValueRead()), 3.0)[3:] == ("GroupValueRead", None, None)


def test_analyse(tmp_path):
    store, _, _ = run_read(tmp_path, bcu1_image())
    store.add_telegrams([
        (10.0, PA, "1/0/1", "GroupValueWrite", None, 1),
        (11.0, PA, "1/0/1", "GroupValueWrite", None, 0),
        (12.0, PA, "3/1/7", "GroupValueWrite", b"\x0c\x1a", None),
        (13.0, PA, "5/5/5", "GroupValueWrite", None, 1),  # nicht in Adresstabelle → Widerspruch
        (14.0, "1.1.3", "1/0/2", "GroupValueRead", None, None),  # Visu fragt nur
        (15.0, "1.1.3", "1/0/2", "GroupValueWrite", b"\x40", None),
    ])
    dev = Device(pa=PA, mask=0x0012, group_addresses=["1/0/1", "1/0/2", "3/1/7"])
    t = traffic.analyse(store.db, {PA: dev})
    assert t.total == 6
    assert t.violations == [(PA, "5/5/5", 1)]
    assert dict(t.foreign_senders) == {"1.1.3": 1}  # das Lesetelegramm zählt nicht als Senden
    assert t.unobserved == []
    assert t.gas["1/0/1"].size == "1 Bit" and t.gas["1/0/1"].dpt_guess() == (1,)
    assert t.gas["3/1/7"].size == "2 Bytes" and t.gas["3/1/7"].dpt_guess()[0] == 9
    assert traffic.format_value(b"\x0c\x1a", None, {"main": 9, "sub": 1}) == "21.0 °C"
    assert traffic.format_value(None, 1, {"main": 1, "sub": 1}) == "Ein"
    assert traffic.format_value(b"\x01\x02", None, {"main": 999, "sub": None}) == "01 02"  # unbekannt → roh


def test_live_api(tmp_path):
    store, _, _ = run_read(tmp_path, bcu1_image())
    store.probe(PA, "device", 0x0012, 300)
    store.add_telegrams([(float(i), PA, "1/0/1", "GroupValueWrite", None, i % 2) for i in range(5)])
    client = TestClient(create_app(str(tmp_path / "t.sqlite")))
    first = client.get("/api/telegrams").json()["telegrams"]
    assert [t["id"] for t in first] == [5, 4, 3, 2, 1]
    assert first[0]["value"] == "Aus" and first[0]["dpt_source"] == "Vorschlag" and first[0]["src_known"]
    assert client.get("/api/telegrams?after=5").json()["telegrams"] == []
    assert client.get("/traffic").status_code == 200
