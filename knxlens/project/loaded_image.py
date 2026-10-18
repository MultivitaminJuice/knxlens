"""ETS-Ladeabbild (DeviceInstance-Attribut `LoadedImage`).

Die ETS legt bei manchen Geräten das zuletzt geladene Speicherabbild im Projekt ab:
Base64 → Deflate (roh) → Binärformat (Little Endian):

    u16 Version (1), u16 ?, u32 Anzahl Blöcke
    je Block: u32 Typ (1 = absolute Adresse, 12 = relativ zu einer Load State Machine),
              u16 LSM bzw. Objektindex, u16 Property (7 = Tabellenzeiger),
              u32 Adresse bzw. Offset, u32 Länge n,
              n Byte Daten, n Byte Maske (≠ 0 = Byte wird geschrieben), 1 Byte Abschluss
    Daten[i] gehört zu Adresse + i − 1 (das erste Byte ist ein Platzhalter, Maske 0).

An der Testanlage geprüft: alle gültigen Bytes = gelesener Gerätespeicher (4/4 Geräte).
Damit ist das Abbild eine Referenz aus erster Hand für die eigene Berechnung (E3).
"""

from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass


@dataclass
class Block:
    kind: int  # 1 = absolut, 12 = relativ
    lsm: int
    pid: int
    address: int  # absolut: Speicheradresse; relativ: Offset im Segment
    data: bytes
    mask: bytes

    def bytes(self) -> dict[int, int]:
        """Adresse (bzw. Offset) → Byte, nur gültige."""
        return {self.address + i - 1: self.data[i] for i in range(len(self.data)) if self.mask[i]}


def parse(text: str) -> list[Block]:
    b = zlib.decompress(base64.b64decode(text), -15)
    _version, _x, count = struct.unpack_from("<HHI", b, 0)
    off, out = 8, []
    for _ in range(count):
        kind, lsm, pid, address, n = struct.unpack_from("<IHHII", b, off)
        off += 16
        out.append(Block(kind, lsm, pid, address, b[off:off + n], b[off + n:off + 2 * n]))
        off += 2 * n + 1
    return out


def absolute(blocks: list[Block], rel_base) -> dict[int, int]:
    """Alle gültigen Bytes mit Speicheradresse; rel_base(lsm) liefert die Basis relativer Segmente."""
    out: dict[int, int] = {}
    for blk in blocks:
        base = 0 if blk.kind == 1 else rel_base(blk.lsm)
        if base is None:
            continue
        out.update({base + a: v for a, v in blk.bytes().items()})
    return out
