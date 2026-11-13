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


def test_plan_guard_lets_plan_through_and_blocks_the_rest():
    from knxlens.download import PlanGuard, WriteBlocked
    steps = _steps()
    sim = SimTarget(props={(0, 78): bytes.fromhex("000000000130")})
    guard = PlanGuard(sim, steps)
    asyncio.run(execute(steps, guard))  # der Plan selbst geht durch
    assert sim.states == {1: "loaded", 4: "loaded"}

    async def tamper(call):
        await guard.connect()
        await call()

    for call, what in ((lambda: guard.write_mem(0x4000 + 600, b"\x00"), "Speicher"),  # außerhalb der Blöcke
                       (lambda: guard.write_mem(0x4000, b"\x00" * 20), "Speicher"),  # über den Block hinaus
                       (lambda: guard.write_prop(0, 54, b"\x01"), "Property"),  # nicht geplant
                       (lambda: guard.load_event(3, "unload"), "Ladezustand")):  # fremde LSM
        with pytest.raises(WriteBlocked, match=what):
            asyncio.run(tamper(call))
    assert len(guard.blocked) == 4


def test_mcb_crc_variants():
    from knxlens.download import crc16_aug
    assert crc16_aug(b"123456789") == 0xE5CC  # CRC-16/AUG-CCITT Prüfwert (System B)
    assert crc16_aug(b"123456789", 0xFFFF) == 0x29B1  # CRC-16/CCITT-FALSE Prüfwert (System 7)


def test_load_image_prop_absolute_segments():
    from knxlens.download import load_image_prop
    sim = SimTarget(connected=True, mem={0x4000 + i: i & 0xFF for i in range(16)})
    segs = {3: [{"Address": "1792", "Size": "8", "MemType": "2", "Access": "242", "SegFlags": "0"},  # RAM: kein Eintrag
                {"Address": "16384", "Size": "16", "MemType": "3", "Access": "242", "SegFlags": "128"},
                {"Address": "17000", "Size": "2", "MemType": "3", "Access": "34", "SegFlags": "0"}]}
    asyncio.run(load_image_prop(sim, 3, 27, 2, {}, {}, {}, segs))
    from knxlens.download import crc16_aug
    assert sim.elements[(3, 27, 1)] == (16).to_bytes(4, "big") + b"\x00\xf2" + crc16_aug(bytes(range(16)), 0xFFFF).to_bytes(2, "big")
    assert sim.elements[(3, 27, 2)] == (2).to_bytes(4, "big") + b"\x01\x22\x00\x00"  # ohne Prüfsummen-Flag
