"""Download-Ausführung gegen den Simulator: Reihenfolge, Prüfungen, Rücklesen."""

import asyncio

import pytest

from knxlens.download import DownloadError, SimTarget, compare_prop, execute
from knxlens.loadplan import build


def _steps(hw="00000000013000000000"):
    raw = [("Connect", {}), ("CompareProp", {"ObjIdx": "0", "PropId": "78", "InlineData": hw}),
           ("Unload", {"LsmIdx": "1"}), ("Load", {"LsmIdx": "1"}),
           ("AbsSegment", {"LsmIdx": "1", "Address": "16384", "Size": "513"}),
           ("LoadCompleted", {"LsmIdx": "1"}),
           ("Load", {"LsmIdx": "4"}), ("RelSegment", {"LsmIdx": "4", "Size": "20", "Fill": "255"}),
           ("WriteRelMem", {"ObjIdx": "4", "Offset": "0", "Size": "20"}), ("LoadCompleted", {"LsmIdx": "4"}),
           ("Restart", {}), ("Disconnect", {})]
    table = bytes([3, 0x11, 0xD4, 0x09, 0x01, 0x09, 0x02])
    return build(raw, {}, {1: table}, {4: bytes(range(20))},
                 masks={("rel", 4): lambda i: i < 5 or i >= 15})


def test_execute_writes_and_verifies():
    sim = SimTarget(props={(0, 78): bytes.fromhex("000000000130")})
    res = asyncio.run(execute(_steps(), sim))
    assert sim.read_mem.__self__ is sim
    assert bytes(sim.mem[0x4000 + i] for i in range(7)) == bytes([3, 0x11, 0xD4, 0x09, 0x01, 0x09, 0x02])
    base = int.from_bytes(sim.props[(4, 7)], "big")
    assert sim.mem[base + 2] == 2 and sim.mem[base + 7] == 0xFF  # Lücke: Füllwert bleibt, nicht geschrieben
    assert res.bytes_written == 7 + 10 and res.verified == 3
    assert sim.states == {1: "loaded", 4: "loaded"}


def test_wrong_hardware_aborts_before_writing():
    sim = SimTarget(props={(0, 78): bytes.fromhex("000000000999")})
    with pytest.raises(DownloadError, match="Prüfung"):
        asyncio.run(execute(_steps(), sim))
    assert not sim.written


def test_compare_prop_mask_and_length():
    assert compare_prop(bytes.fromhex("000000000130"), "00000000013000000000", None)  # längerer Erwartungswert
    assert compare_prop(bytes.fromhex("0000002b0001"), "0000002B0000", "FFFFFFFF0000")  # Maske
    assert not compare_prop(bytes.fromhex("0000002c0001"), "0000002B0000", "FFFFFFFF0000")
