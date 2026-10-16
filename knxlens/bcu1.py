"""BCU1/BCU2: feste Speicherkarte statt Interface-Objekte.

Ältere Geräte (etwa Gira-Tastsensoren um 2000) haben bei BCU1 keine Properties.
Alles Wichtige liegt im EEPROM ab 0x0100:

    0x0104       Hersteller-ID (1 Byte)
    0x0105–0106  Gerätetyp = Applikationsnummer
    0x0107       Applikationsversion
    0x010D       RunError (0xFF = kein Fehler; Bits low-aktiv)
    0x0111       Zeiger Assoziationstabelle (Offset zu 0x0100)
    0x0112       Zeiger Gruppenobjekttabelle (Offset zu 0x0100)
    0x0116       Adresstabelle: 1 Byte Anzahl N (inkl. eigener PA), dann N × 2 Byte

Assoziationstabelle: 1 Byte Anzahl, dann Paare (Index Adresstabelle, Objektnummer).
Gruppenobjekttabelle: 1 Byte Anzahl, 1 Byte Zeiger RAM-Flags, dann je Objekt
3 Byte (Wertzeiger, Konfigurationsbyte, Typbyte).

Quelle: KNX-Spezifikation / BCU1-Handbuch; noch nicht an einem echten Gerät geprüft.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from xknx.telegram import GroupAddress, IndividualAddress

from .model import Device, config_byte

BCU1_MASKS = {0x0010, 0x0011, 0x0012, 0x0013}
BCU2_MASKS = {0x0020, 0x0021, 0x0025}

HEADER_START = 0x0100
HEADER_LEN = 0x17  # 0x0100–0x0116 einschließlich Anzahl-Byte der Adresstabelle
ADDR_TABLE = 0x0116
MANUFACTURER = 0x0104
DEVICE_TYPE = 0x0105
VERSION = 0x0107
RUN_ERROR = 0x010D
ASSOC_PTR = 0x0111
COMM_PTR = 0x0112


def is_bcu1(mask: int | None) -> bool:
    return mask in BCU1_MASKS


def is_bcu2(mask: int | None) -> bool:
    return mask in BCU2_MASKS


def mem_end(mask: int | None) -> int:
    """Erste Adresse hinter dem lesbaren EEPROM."""
    return 0x0500 if is_bcu2(mask) else 0x0200


# --- Lesen (Bus) -------------------------------------------------------------

MemRead = Callable[[int, int], Awaitable[bytes | None]]


async def read(mem: MemRead, mask: int | None) -> dict:
    """Kopf und Tabellen lesen. Zeiger werden nur innerhalb des EEPROMs verfolgt."""
    end = mem_end(mask)
    summary: dict = {}
    header = await mem(HEADER_START, HEADER_LEN)
    if header is None or len(header) < HEADER_LEN:
        summary["header"] = "nicht lesbar"
        return summary
    summary["header"] = header.hex()

    async def counted(base: int, entry: int, extra: int = 0) -> str:
        n = await mem(base, 1)
        if n is None:
            return "nicht lesbar"
        length = 1 + extra + n[0] * entry
        if base + length > end:
            return f"überschreitet EEPROM ({base:#06x}+{length})"
        data = await mem(base, length)
        return f"{len(data or b'')}/{length} Byte"

    summary["address_table"] = await counted(ADDR_TABLE, 2)
    for name, ptr_pos, entry, extra in (("assoc_table", ASSOC_PTR, 2, 0), ("group_object_table", COMM_PTR, 3, 1)):
        ptr = header[ptr_pos - HEADER_START]
        base = HEADER_START + ptr
        if ptr == 0 or base >= end:
            summary[name] = f"Zeiger {ptr:#04x} unplausibel"
            continue
        summary[name] = await counted(base, entry, extra)
    return summary


# --- Dekodieren (offline) ----------------------------------------------------

MemImage = Callable[[int, int], bytes | None]


def decode(mem: MemImage, dev: Device) -> Device:
    """Device aus einem Speicherabbild füllen."""
    header = mem(HEADER_START, HEADER_LEN)
    if header is None:
        dev.problems.append("BCU-Kopf nicht gelesen")
        return dev
    h = lambda addr: header[addr - HEADER_START]  # noqa: E731
    dev.manufacturer = h(MANUFACTURER)
    dev.app_manufacturer = h(MANUFACTURER)
    dev.app_number = (h(DEVICE_TYPE) << 8) | h(DEVICE_TYPE + 1)
    dev.app_version = h(VERSION)
    dev.run_error = h(RUN_ERROR)
    if dev.run_error != 0xFF:
        dev.problems.append(f"RunError {dev.run_error:#04x} (0xFF wäre fehlerfrei)")

    # Adresstabelle
    n = h(ADDR_TABLE)
    body = mem(ADDR_TABLE + 1, 2 * n)
    if body is None:
        dev.problems.append("Adresstabelle nicht lesbar")
        return dev
    values = [int.from_bytes(body[2 * i : 2 * i + 2], "big") for i in range(n)]
    if values and str(IndividualAddress(values[0])) != dev.pa:
        dev.problems.append(f"Adresstabelle beginnt mit {IndividualAddress(values[0])}, nicht mit eigener PA")
    table = [None] + [str(GroupAddress(v)) for v in values[1:]]
    dev.group_addresses = table[1:]

    # Assoziationstabelle
    base = HEADER_START + h(ASSOC_PTR)
    count = mem(base, 1)
    pairs = mem(base + 1, 2 * count[0]) if count else None
    if pairs is None:
        dev.problems.append("Assoziationstabelle nicht lesbar")
    else:
        for i in range(count[0]):
            tsap, asap = pairs[2 * i], pairs[2 * i + 1]
            if 1 <= tsap < len(table):
                dev.links.add((asap, table[tsap]))
            else:
                dev.problems.append(f"Index {tsap} außerhalb der Adresstabelle")

    # Gruppenobjekttabelle
    base = HEADER_START + h(COMM_PTR)
    count = mem(base, 1)
    objs = mem(base + 2, 3 * count[0]) if count else None
    if objs is None:
        dev.problems.append("Gruppenobjekttabelle nicht lesbar")
    else:
        for i in range(count[0]):
            entry = objs[3 * i : 3 * i + 3]
            dev.objects.append(config_byte(i, entry[1], entry[2], entry))
    return dev
