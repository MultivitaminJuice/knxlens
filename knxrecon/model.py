"""Datenmodell der Rekonstruktion (gemeinsam für Leser, Decoder und Berichte)."""

from __future__ import annotations

from dataclasses import dataclass, field

# Objekttyp-Byte (BCU1/BCU2, System 7/B) → Objektgröße, Schreibweise wie in der ETS
OBJECT_SIZES = {
    0: "1 Bit", 1: "2 Bit", 2: "3 Bit", 3: "4 Bit", 4: "5 Bit", 5: "6 Bit", 6: "7 Bit",
    7: "1 Byte", 8: "2 Bytes", 9: "3 Bytes", 10: "4 Bytes", 11: "6 Bytes",
    12: "8 Bytes", 13: "10 Bytes", 14: "14 Bytes",
}
PRIORITIES = {0: "System", 1: "Normal", 2: "Hoch", 3: "Niedrig"}


@dataclass
class GroupObject:
    number: int
    communication: bool
    read: bool
    write: bool
    transmit: bool
    update: bool
    priority: str
    size: str | None
    raw: bytes = b""

    @property
    def flags(self) -> str:
        """ETS-Kürzel: K L S Ü A (Kommunikation, Lesen, Schreiben, Übertragen, Aktualisieren)."""
        return "".join(
            c if on else "-"
            for c, on in zip("KLSÜA", (self.communication, self.read, self.write, self.transmit, self.update))
        )


def config_byte(number: int, config: int, type_code: int, raw: bytes) -> GroupObject:
    """Konfigurationsbyte eines Gruppenobjekts.

    Bit 7 Aktualisieren · Bit 6 Übertragen · Bit 5 (BCU1: Wert im EEPROM / System B: Init lesen)
    Bit 4 Schreiben · Bit 3 Lesen · Bit 2 Kommunikation · Bit 1–0 Priorität
    """
    return GroupObject(
        number=number,
        communication=bool(config & 0x04),
        read=bool(config & 0x08),
        write=bool(config & 0x10),
        transmit=bool(config & 0x40),
        update=bool(config & 0x80),
        priority=PRIORITIES[config & 0x03],
        size=OBJECT_SIZES.get(type_code),
        raw=raw,
    )


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
    run_error: int | None = None  # nur BCU1/BCU2
    group_addresses: list[str] = field(default_factory=list)
    links: set[tuple[int, str]] = field(default_factory=set)  # (Objektnummer, GA)
    objects: list[GroupObject] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def app_id(self) -> str | None:
        if self.app_manufacturer is None:
            return None
        return f"M-{self.app_manufacturer:04X}_A-{self.app_number:04X}-{self.app_version:02X}"
