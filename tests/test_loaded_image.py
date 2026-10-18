"""ETS-Ladeabbild: Format lesen (Block mit Platzhalterbyte und Maske)."""

import base64
import struct
import zlib

from knxlens.project.loaded_image import absolute, parse


def _make(blocks):
    body = struct.pack("<HHI", 1, 1, len(blocks))
    for kind, lsm, addr, data, mask in blocks:
        body += struct.pack("<IHHII", kind, lsm, 7 if kind == 12 else 0, addr, len(data)) + data + mask + b"\xff"
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(comp.compress(body) + comp.flush()).decode()


def test_parse_and_absolute():
    text = _make([(1, 0, 0x4800, b"\x03\xaa\xbb\xcc", b"\x00\xff\x00\xff"),
                  (12, 4, 0x10, b"\x00\x11\x22", b"\x00\xff\xff")])
    blocks = parse(text)
    assert [b.kind for b in blocks] == [1, 12]
    assert blocks[0].bytes() == {0x4800: 0xAA, 0x4802: 0xCC}  # erstes Byte ist Platzhalter
    mem = absolute(blocks, lambda lsm: 0x3000 if lsm == 4 else None)
    assert mem[0x3010] == 0x11 and mem[0x3011] == 0x22 and mem[0x4800] == 0xAA
