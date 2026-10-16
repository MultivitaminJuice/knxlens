"""Rohdaten aus der Datenbank zu Tabellen dekodieren (offline, beliebig wiederholbar)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from xknx.telegram import GroupAddress, IndividualAddress

from .device import (
    OT_ADDRESS_TABLE,
    OT_APPLICATION,
    OT_ASSOCIATION_TABLE,
    PID_LOAD_STATE_CONTROL,
    PID_MANUFACTURER_ID,
    PID_OBJECT_TYPE,
    PID_ORDER_INFO,
    PID_PROGRAM_VERSION,
    PID_SERIAL_NUMBER,
    PID_TABLE_REFERENCE,
)
from .store import Store


@dataclass
class Device:
    pa: str
    mask: int | None
    manufacturer: int | None = None
    serial: str | None = None
    order_info: bytes | None = None
    app_manufacturer: int | None = None
    app_number: int | None = None
    app_version: int | None = None
    group_addresses: list[str] = field(default_factory=list)
    links: set[tuple[int, str]] = field(default_factory=set)  # (Objektnummer, GA)
    problems: list[str] = field(default_factory=list)

    @property
    def app_id(self) -> str | None:
        if self.app_manufacturer is None:
            return None
        return f"M-{self.app_manufacturer:04X}_A-{self.app_number:04X}-{self.app_version:02X}"


class Raw:
    """Zugriff auf die jeweils jüngste Antwort einer Anfrage."""

    def __init__(self, store: Store, pa: str) -> None:
        self.store = store
        self.pa = pa

    def prop(self, obj: int, pid: int, count: int = 1, start: int = 1) -> bytes | None:
        row = self.store.latest_read(self.pa, "prop", {"obj": obj, "pid": pid, "count": count, "start": start})
        return row[0] if row and row[1] is None else None

    def mem(self, address: int, length: int) -> bytes | None:
        """Speicherbereich aus den protokollierten Einzel-Reads zusammensetzen."""
        cur = self.store.db.execute(
            "SELECT args, data FROM read WHERE pa=? AND service='mem' AND error IS NULL ORDER BY ts",
            (self.pa,),
        )
        image: dict[int, int] = {}
        for args, data in cur:
            a = json.loads(args)["addr"]
            for i, b in enumerate(data):
                image[a + i] = b
        if not all(address + i in image for i in range(length)):
            return None
        return bytes(image[address + i] for i in range(length))


def _objects(raw: Raw) -> dict[int, int]:
    """Wie device.read_device: fehlender Typ bei Index 1/2 → Standardtyp, wenn geladen."""
    out = {}
    for idx in range(24):
        data = raw.prop(idx, PID_OBJECT_TYPE)
        if data is not None:
            out[idx] = int.from_bytes(data, "big")
        elif idx in (OT_ADDRESS_TABLE, OT_ASSOCIATION_TABLE) and raw.prop(idx, PID_LOAD_STATE_CONTROL):
            out[idx] = idx
    return out


def _loaded(raw: Raw, objects: dict[int, int], otype: int) -> int | None:
    """Index des (geladenen) Objekts eines Typs."""
    candidates = [i for i, t in objects.items() if t == otype]
    for i in candidates:
        if raw.prop(i, PID_LOAD_STATE_CONTROL) == b"\x01":
            return i
    return candidates[0] if candidates else None


def _table(raw: Raw, objects: dict[int, int], otype: int, head_size: int, entry_size: int) -> list[bytes] | None:
    """Tabelle mit Anzahl-Kopf lesen; nur die gezählten Einträge, keine Restbytes."""
    idx = _loaded(raw, objects, otype)
    if idx is None:
        return None
    ref = raw.prop(idx, PID_TABLE_REFERENCE)
    if ref is None:
        return None
    addr = int.from_bytes(ref, "big")
    head = raw.mem(addr, head_size)
    if head is None:
        return None
    n = int.from_bytes(head, "big")
    body = raw.mem(addr + head_size, n * entry_size)
    if body is None:
        return None
    return [body[i * entry_size : (i + 1) * entry_size] for i in range(n)]


def decode_device(store: Store, pa: str, mask: int | None) -> Device:
    raw = Raw(store, pa)
    dev = Device(pa=pa, mask=mask)
    objects = _objects(raw)

    if (d := raw.prop(0, PID_MANUFACTURER_ID)) is not None:
        dev.manufacturer = int.from_bytes(d, "big")
    if (d := raw.prop(0, PID_SERIAL_NUMBER)) is not None:
        dev.serial = d.hex(":")
    dev.order_info = raw.prop(0, PID_ORDER_INFO)

    app_idx = _loaded(raw, objects, OT_APPLICATION)
    if app_idx is not None and (d := raw.prop(app_idx, PID_PROGRAM_VERSION)) is not None and len(d) >= 5:
        dev.app_manufacturer = int.from_bytes(d[0:2], "big")
        dev.app_number = int.from_bytes(d[2:4], "big")
        dev.app_version = d[4]
    else:
        dev.problems.append("keine Programmversion")

    head = 2 if mask == 0x07B0 else 1
    addr_entries = _table(raw, objects, OT_ADDRESS_TABLE, head, 2)
    if addr_entries is None:
        dev.problems.append("Adresstabelle nicht lesbar")
        return dev
    values = [int.from_bytes(e, "big") for e in addr_entries]
    if head == 1:
        # System 7: Eintrag 0 ist die eigene physikalische Adresse, ab 1 folgen die GAs.
        if values and str(IndividualAddress(values[0])) != pa:
            dev.problems.append(f"Adresstabelle beginnt mit {IndividualAddress(values[0])}, nicht mit eigener PA")
        values = values[1:]
    # Index 0 bleibt frei, damit der TSAP (ab 1) direkt indiziert.
    table = [None] + [str(GroupAddress(v)) for v in values]
    dev.group_addresses = table[1:]

    assoc = _table(raw, objects, OT_ASSOCIATION_TABLE, head, 2 * head)
    if assoc is None:
        dev.problems.append("Assoziationstabelle nicht lesbar")
        return dev
    for e in assoc:
        tsap = int.from_bytes(e[:head], "big")
        asap = int.from_bytes(e[head:], "big")
        if 1 <= tsap < len(table):
            dev.links.add((asap, table[tsap]))
        else:
            dev.problems.append(f"Index {tsap} außerhalb der Adresstabelle")
    return dev
