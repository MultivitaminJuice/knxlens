"""Geräte auslesen: Interface-Objekte, Identität, Tabellenzeiger, Tabellenspeicher.

Nur für Masken 0x07xx (System 7 / System B). BCU1/BCU2 (0x001x/0x002x) haben
feste Speicheradressen und kommen in einem eigenen Pfad dazu.
Alle Antworten landen roh in der Datenbank (Store.read); Deutung in decode.py.
"""

from __future__ import annotations

import logging

from xknx import XKNX
from xknx.exceptions import ManagementConnectionError, ManagementConnectionTimeout
from xknx.management.management import P2PConnection
from xknx.telegram import IndividualAddress, apci

from .store import Store

log = logging.getLogger(__name__)

# Property-IDs (KNX 3/7/3 Standardized Identifier Tables)
PID_OBJECT_TYPE = 1
PID_LOAD_STATE_CONTROL = 5
PID_TABLE_REFERENCE = 7
PID_SERIAL_NUMBER = 11
PID_MANUFACTURER_ID = 12
PID_PROGRAM_VERSION = 13
PID_ORDER_INFO = 15
PID_TABLE = 23
PID_MAX_APDU_LENGTH = 56
PID_HARDWARE_TYPE = 78

# Objekttypen
OT_DEVICE = 0
OT_ADDRESS_TABLE = 1
OT_ASSOCIATION_TABLE = 2
OT_APPLICATION = 3
OT_GROUP_OBJECT_TABLE = 9
TABLE_OBJECTS = {
    OT_ADDRESS_TABLE: "address_table",
    OT_ASSOCIATION_TABLE: "assoc_table",
    OT_APPLICATION: "application",
    OT_GROUP_OBJECT_TABLE: "group_object_table",
}

MEM_CHUNK = 12
MAX_TABLE = 1024


class Reader:
    """Leseoperationen auf einer offenen Verbindung, jede wird protokolliert."""

    def __init__(self, conn: P2PConnection, store: Store) -> None:
        self.conn = conn
        self.store = store
        self.pa = str(conn.address)

    async def prop(self, obj: int, pid: int, count: int = 1, start: int = 1) -> bytes | None:
        args = {"obj": obj, "pid": pid, "count": count, "start": start}
        try:
            resp = await self.conn.request(
                apci.PropertyValueRead(object_index=obj, property_id=pid, count=count, start_index=start)
            )
        except (ManagementConnectionTimeout, ManagementConnectionError) as err:
            self.store.read(self.pa, "prop", args, None, str(err))
            return None
        p = resp.payload
        # count=0 in der Antwort = Fehler / Property nicht vorhanden
        if p.count == 0 or p.property_id != pid or p.object_index != obj:
            self.store.read(self.pa, "prop", args, p.data, "negative")
            return None
        self.store.read(self.pa, "prop", args, p.data, None)
        return p.data

    async def mem(self, address: int, length: int) -> bytes | None:
        out = b""
        while len(out) < length:
            n = min(MEM_CHUNK, length - len(out))
            addr = address + len(out)
            args = {"addr": addr, "count": n}
            try:
                resp = await self.conn.request(apci.MemoryRead(address=addr, count=n))
            except (ManagementConnectionTimeout, ManagementConnectionError) as err:
                self.store.read(self.pa, "mem", args, None, str(err))
                return out or None
            data = resp.payload.data
            if resp.payload.address != addr or len(data) != n:
                # Zugriff verweigert: Gerät antwortet mit count=0
                self.store.read(self.pa, "mem", args, data, "denied")
                return out or None
            self.store.read(self.pa, "mem", args, data, None)
            out += data
        return out


async def read_device(xknx: XKNX, store: Store, pa: str, mask: int | None) -> dict:
    """Identität und Tabellen eines System-7/B-Geräts lesen. Ergebnis kurz zusammengefasst."""
    summary: dict = {"pa": pa, "objects": {}}
    async with xknx.management.connection(IndividualAddress(pa)) as conn:
        r = Reader(conn, store)

        # 1. Interface-Objekte aufzählen. Manche System-B-Geräte kennen PID_OBJECT_TYPE
        #    für Index 1/2 nicht; meldet das Objekt einen Ladezustand, gilt der Standardtyp.
        for idx in range(0, 24):
            data = await r.prop(idx, PID_OBJECT_TYPE)
            if data is not None:
                summary["objects"][idx] = int.from_bytes(data, "big")
            elif idx in (OT_ADDRESS_TABLE, OT_ASSOCIATION_TABLE) and await r.prop(idx, PID_LOAD_STATE_CONTROL):
                summary["objects"][idx] = idx
            elif idx > 3:
                break

        # 2. Identität (Geräteobjekt ist immer Index 0)
        for name, pid, count in (
            ("manufacturer", PID_MANUFACTURER_ID, 1),
            ("serial", PID_SERIAL_NUMBER, 1),
            ("order_info", PID_ORDER_INFO, 1),
            ("hardware_type", PID_HARDWARE_TYPE, 1),
            ("max_apdu", PID_MAX_APDU_LENGTH, 1),
        ):
            data = await r.prop(0, pid, count)
            summary[name] = data.hex() if data else None

        # 3. Pro Tabellenobjekt: Programmversion, Ladezustand, Speicherzeiger.
        #    Bei mehreren Applikationsobjekten zählt das geladene (load_state 01).
        for idx, otype in summary["objects"].items():
            name = TABLE_OBJECTS.get(otype)
            if not name:
                continue
            entry: dict = {"index": idx}
            for key, pid in (("version", PID_PROGRAM_VERSION), ("load_state", PID_LOAD_STATE_CONTROL), ("ref", PID_TABLE_REFERENCE)):
                data = await r.prop(idx, pid)
                entry[key] = data.hex() if data else None
            if name in summary and summary[name].get("load_state") == "01":
                continue
            summary[name] = entry

        # 4. Tabellenspeicher lesen. Kopfgröße hängt von der Maske ab:
        #    System 7 (0x0701/0x0705): 1 Byte Anzahl; System B (0x07B0): vermutlich 2 Byte.
        #    Die Anzahl begrenzt streng – dahinter stehen oft Reste alter Programmierungen.
        head_size = 2 if mask == 0x07B0 else 1
        for name in ("address_table", "assoc_table", "group_object_table"):
            ref = (summary.get(name) or {}).get("ref")
            if not ref:
                continue
            addr = int(ref, 16)
            head = await r.mem(addr, head_size)
            if head is None:
                summary[name]["mem"] = "denied"
                continue
            count = int.from_bytes(head, "big")
            entry_size = {"address_table": 2, "assoc_table": 2 * head_size, "group_object_table": 2}[name]
            length = head_size + count * entry_size
            if length > MAX_TABLE:
                summary[name]["mem"] = f"Kopf {head.hex()} unplausibel"
                continue
            data = await r.mem(addr, length)
            summary[name]["mem"] = f"{len(data or b'')}/{length} Byte"
    return summary
