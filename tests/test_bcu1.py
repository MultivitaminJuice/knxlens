"""BCU1-Pfad gegen ein nachgebautes Speicherabbild (kein echtes Gerät verfügbar)."""

import asyncio
from types import SimpleNamespace

import pytest
from xknx.telegram import GroupAddress, IndividualAddress, apci, tpci

from knxrecon import bcu1
from knxrecon.bus import ReadOnlyGuard
from knxrecon.decode import decode_device
from knxrecon.device import Reader
from knxrecon.store import Store

PA = "1.1.5"
GAS = ["1/0/1", "1/0/2", "3/1/7"]


def bcu1_image(*, run_error=0xFF, assoc_ptr=0x30, comm_ptr=0x40, stale=True) -> bytearray:
    mem = bytearray(0x200)
    mem[0x104] = 0x08  # Gira
    mem[0x105:0x107] = (0x20, 0x32)  # Gerätetyp / Applikationsnummer
    mem[0x107] = 0x12  # Version
    mem[0x10D] = run_error
    mem[0x111] = assoc_ptr
    mem[0x112] = comm_ptr
    # Adresstabelle: Anzahl inkl. PA, dann PA, dann GAs
    entries = [IndividualAddress(PA).raw] + [GroupAddress(g).raw for g in GAS]
    mem[0x116] = len(entries)
    for i, v in enumerate(entries):
        mem[0x117 + 2 * i : 0x119 + 2 * i] = v.to_bytes(2, "big")
    if stale:  # Reste einer früheren Programmierung hinter dem Tabellenende
        mem[0x117 + 2 * len(entries) : 0x119 + 2 * len(entries)] = GroupAddress("7/7/7").raw.to_bytes(2, "big")
    # Assoziationstabelle: (Index Adresstabelle, Objektnummer)
    pairs = [(1, 0), (2, 0), (3, 1)]
    base = 0x100 + assoc_ptr
    mem[base] = len(pairs)
    for i, (t, a) in enumerate(pairs):
        mem[base + 1 + 2 * i] = t
        mem[base + 2 + 2 * i] = a
    if stale:
        mem[base + 1 + 2 * len(pairs) : base + 3 + 2 * len(pairs)] = (4, 1)
    # Gruppenobjekttabelle: Anzahl, RAM-Flag-Zeiger, je 3 Byte (Wertzeiger, Config, Typ)
    base = 0x100 + comm_ptr
    mem[base] = 2
    mem[base + 1] = 0x60
    mem[base + 2 : base + 5] = (0x70, 0b0101_1111, 0)  # K L S Ü, Prio Niedrig, 1 Bit
    mem[base + 5 : base + 8] = (0x71, 0b1001_0101, 7)  # K S A (kein L, kein Ü), 1 Byte
    return mem


class FakeConn:
    """Beantwortet MemoryRead aus einem Abbild; prüft jede Anfrage mit dem Guard."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.address = IndividualAddress(PA)
        self.guard = ReadOnlyGuard()
        self.requests = []

    async def request(self, payload):
        self.guard.check(tpci.TDataConnected(sequence_number=0), payload)
        self.requests.append(payload)
        assert isinstance(payload, apci.MemoryRead)
        assert payload.address + payload.count <= len(self.image), "Lesen außerhalb des Abbilds"
        data = bytes(self.image[payload.address : payload.address + payload.count])
        return SimpleNamespace(payload=apci.MemoryResponse(address=payload.address, data=data))


def run_read(tmp_path, image):
    store = Store(tmp_path / "t.sqlite")
    store.start_session("fake", "1.1.250")
    conn = FakeConn(image)
    summary = asyncio.run(bcu1.read(Reader(conn, store).mem, 0x0012))
    return store, conn, summary


def test_read_and_decode(tmp_path):
    store, conn, summary = run_read(tmp_path, bcu1_image())
    assert summary["address_table"] == "9/9 Byte"
    assert summary["assoc_table"] == "7/7 Byte"
    assert summary["group_object_table"] == "8/8 Byte"
    # nur EEPROM-Bereich, nie mehr als 12 Byte je Telegramm
    assert all(0x100 <= r.address and r.address + r.count <= 0x200 and r.count <= 12 for r in conn.requests)

    dev = decode_device(store, PA, 0x0012)
    assert dev.app_id == "M-0008_A-2032-12"
    assert dev.group_addresses == GAS
    assert dev.links == {(0, "1/0/1"), (0, "1/0/2"), (1, "3/1/7")}  # ohne Restbytes
    assert dev.problems == []
    o0, o1 = dev.objects
    assert (o0.flags, o0.priority, o0.size) == ("KLSÜ-", "Niedrig", "1 Bit")
    assert (o1.flags, o1.priority, o1.size) == ("K-S-A", "Normal", "1 Byte")


def test_run_error_reported(tmp_path):
    store, _, _ = run_read(tmp_path, bcu1_image(run_error=0xEF))
    dev = decode_device(store, PA, 0x0012)
    assert dev.run_error == 0xEF
    assert any("RunError" in p for p in dev.problems)


@pytest.mark.parametrize("ptr", [0x00, 0xFF])
def test_implausible_pointer_not_followed(tmp_path, ptr):
    image = bcu1_image(stale=False)
    image[0x111] = ptr
    if ptr == 0xFF:
        image[0x1FF] = 5  # Anzahl würde über 0x0200 hinausreichen
    _, conn, summary = run_read(tmp_path, image)
    assert "assoc_table" in summary
    assert not summary["assoc_table"].endswith("Byte")
    assert all(r.address + r.count <= 0x200 for r in conn.requests)


def test_bcu2_falls_back_to_fixed_map(tmp_path):
    """BCU2 ohne Properties (alle Property-Reads negativ) → feste Karte."""
    store, _, _ = run_read(tmp_path, bcu1_image())
    dev = decode_device(store, PA, 0x0020)
    assert dev.links == {(0, "1/0/1"), (0, "1/0/2"), (1, "3/1/7")}
    assert dev.problems[0].startswith("BCU2")
