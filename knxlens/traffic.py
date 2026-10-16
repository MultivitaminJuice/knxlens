"""Auswertung des Mitschnitts (offline).

- je GA: Anzahl, Zeitraum, Sender, letzter Wert, Nutzlastgröße → DPT-Vorschlag
- Plausibilität gegen die rekonstruierten Tabellen: Ein bekanntes Gerät kann nur an
  Gruppenadressen senden, die in seiner Adresstabelle stehen. Verstöße deuten auf
  Dekodierfehler oder veraltete Lesedaten hin.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from xknx.dpt import DPTArray, DPTBase, DPTBinary

from .model import Device

# Nutzlast → mögliche Haupttypen, wahrscheinlichster zuerst
SIZE_GUESS: dict[str, tuple[int, ...]] = {
    "1 Bit": (1,),
    "2 Bit": (2,),
    "4 Bit": (3,),
    "1 Byte": (5, 20, 6, 17, 18),
    "2 Bytes": (9, 7, 8),
    "3 Bytes": (10, 11, 232),
    "4 Bytes": (14, 12, 13),
    "8 Bytes": (19, 29),
    "14 Bytes": (16,),
}
DPT_NAMES = {
    1: "Schalten", 2: "Zwangsführung", 3: "Dimmen/Jalousie relativ", 5: "1 Byte vorzeichenlos (z. B. %)",
    6: "1 Byte vorzeichenbehaftet", 7: "2 Byte vorzeichenlos", 8: "2 Byte vorzeichenbehaftet",
    9: "2 Byte Gleitkomma (Temperatur, Lux …)", 10: "Uhrzeit", 11: "Datum", 12: "4 Byte vorzeichenlos",
    13: "4 Byte vorzeichenbehaftet", 14: "4 Byte Gleitkomma (Leistung, Energie …)", 16: "Text",
    17: "Szene", 18: "Szenensteuerung", 19: "Datum/Uhrzeit", 20: "Aufzählung", 29: "8 Byte", 232: "RGB",
}


def size_of(payload: bytes | None, bits: int | None, max_bits: int = 0) -> str | None:
    if payload is not None:
        return f"{len(payload)} Byte" if len(payload) == 1 else f"{len(payload)} Bytes"
    if bits is not None:
        # Kleinwerte: 1 Bit, sofern nie > 1; Dimmen/Jalousie relativ nutzt 4 Bit
        return "1 Bit" if max_bits <= 1 else ("2 Bit" if max_bits <= 3 else "4 Bit")
    return None


def format_value(payload: bytes | None, bits: int | None, dpt: dict | None) -> str:
    if payload is None and bits is None:
        return ""
    raw = DPTBinary(bits) if bits is not None else DPTArray(payload)
    if dpt and dpt.get("main"):
        try:
            transcoder = DPTBase.parse_transcoder(dpt)
            if transcoder is not None:
                value = transcoder.from_knx(raw)
                if hasattr(value, "name") and not isinstance(value, (int, float)):
                    return {"ON": "Ein", "OFF": "Aus", "TRUE": "Ein", "FALSE": "Aus"}.get(value.name, value.name)
                if isinstance(value, float):
                    value = round(value, 2)
                unit = getattr(transcoder, "unit", None)
                return f"{value} {unit}" if unit else str(value)
        except Exception:  # noqa: BLE001 - falscher DPT-Vorschlag darf die Anzeige nicht kippen
            pass
    return str(bits) if bits is not None else payload.hex(" ")


@dataclass
class GAStats:
    ga: str
    count: int = 0
    services: Counter = field(default_factory=Counter)
    senders: Counter = field(default_factory=Counter)
    first: float = 0.0
    last: float = 0.0
    last_payload: bytes | None = None
    last_bits: int | None = None
    max_bits: int = 0
    sizes: Counter = field(default_factory=Counter)

    @property
    def size(self) -> str | None:
        return self.sizes.most_common(1)[0][0] if self.sizes else None

    def dpt_guess(self) -> tuple[int, ...]:
        return SIZE_GUESS.get(self.size or "", ())


@dataclass
class Traffic:
    total: int
    first: float | None
    last: float | None
    gas: dict[str, GAStats]
    foreign_senders: Counter  # Quell-PAs, die kein ausgelesenes Gerät sind
    violations: list[tuple[str, str, int]]  # (Sender, GA, Anzahl): GA nicht in Adresstabelle des Senders
    unobserved: list[str]  # GAs aus den Tabellen, die nie im Verkehr auftauchten


def analyse(db: sqlite3.Connection, devices: dict[str, Device]) -> Traffic:
    gas: dict[str, GAStats] = {}
    pairs: Counter = Counter()
    total = 0
    first = last = None
    for ts, src, dst, service, payload, bits in db.execute(
        "SELECT ts, src, dst, service, payload, bits FROM telegram ORDER BY ts"
    ):
        total += 1
        first = first or ts
        last = ts
        s = gas.setdefault(dst, GAStats(dst, first=ts))
        s.count += 1
        s.last = ts
        s.services[service] += 1
        if service != "GroupValueRead":
            s.senders[src] += 1
            pairs[(src, dst)] += 1
            s.last_payload, s.last_bits = payload, bits
            if bits is not None:
                s.max_bits = max(s.max_bits, bits)
            if (size := size_of(payload, bits)) is not None:
                s.sizes[size if payload is not None else "Bits"] += 1
    for s in gas.values():
        # Bit-Größe erst am Ende festlegen: hängt vom größten beobachteten Wert ab
        if "Bits" in s.sizes:
            s.sizes[size_of(None, 0, s.max_bits)] += s.sizes.pop("Bits")

    foreign = Counter()
    violations = []
    for (src, dst), n in pairs.items():
        d = devices.get(src)
        if d is None:
            foreign[src] += n
        elif dst not in d.group_addresses:
            violations.append((src, dst, n))
    known = {ga for d in devices.values() for ga in d.group_addresses}
    return Traffic(
        total=total,
        first=first,
        last=last,
        gas=gas,
        foreign_senders=foreign,
        violations=sorted(violations),
        unobserved=sorted(known - gas.keys()),
    )
