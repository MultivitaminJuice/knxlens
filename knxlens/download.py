"""Download ausführen: Plan (loadplan.Step) Schritt für Schritt gegen ein Ziel abarbeiten.

Ziele:
- SimTarget: bildet ein Gerät im Speicher nach (Load State Machines, Speicher, Properties).
  Damit lässt sich jeder Download vollständig durchspielen, ohne den Bus zu berühren.
- ein Busziel folgt in E4 – mit eigenem Schreibschutz (nur Adressen/Properties aus dem Plan),
  ausdrücklicher Freigabe je Gerät und Rücklesen jedes geschriebenen Blocks.

Noch offen für das Busziel (E4), aus der KNX-Spezifikation, an keinem Gerät geprüft: AbsSegment,
TaskSegment, TaskPtr, TaskCtrl1/2 und RelSegment sind „zusätzliche Ladesteuerungen“ – ein
Schreibzugriff auf PID_LOAD_STATE_CONTROL (5) des Objekts mit Ereignis 3, Typ-Byte und Daten
(z. B. TaskCtrl1: Adresse, Anzahl). Erst am Testgerät bestätigen, bevor sie über den Bus gehen.
Bestätigt sind dagegen die MCB-Einträge (PID 27, LoadImageProp): Länge, Steuerbytes, CRC-16.

Sicherheitsregeln schon hier: CompareProp-Schritte (z. B. Hardwaretyp) brechen bei Abweichung ab,
jeder geschriebene Block wird zurückgelesen, Blöcke werden in APDU-taugliche Stücke geteilt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .loadplan import Step

CHUNK = 12  # Nutzbytes je MemoryWrite bei Standard-APDU (15 Byte APDU − Kopf)
PID_LOAD_STATE_CONTROL = 5
PID_TABLE_REFERENCE = 7
LOAD_STATES = {0: "unloaded", 1: "loaded", 2: "loading", 3: "error"}


class DownloadError(RuntimeError):
    pass


class Target:
    """Schnittstelle eines Downloadziels (asynchron, wie der Bus)."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def restart(self) -> None: ...
    async def load_event(self, lsm: int, event: str) -> None: ...  # unload, load, completed
    async def read_prop(self, obj: int, pid: int, start: int = 1) -> bytes | None: ...
    async def write_prop(self, obj: int, pid: int, data: bytes, start: int = 1) -> None: ...
    async def read_mem(self, address: int, length: int) -> bytes | None: ...
    async def write_mem(self, address: int, data: bytes) -> None: ...
    async def alloc_abs(self, lsm: int, address: int, size: int, attrs: dict | None = None) -> None: ...
    async def task_segment(self, lsm: int, address: int) -> None: ...  # Startadresse (070x: AllocAbsTaskSeg)
    async def alloc_rel(self, lsm: int, size: int, fill: int) -> int: ...  # liefert Basisadresse
    async def task_ctrl(self, kind: str, lsm: int, attrs: dict) -> None: ...  # ältere Masken: Steuerzeiger
    async def find_object(self, object_type: int) -> int: ...  # Index eines Interface-Objekts nach Typ


def compare_prop(got: bytes, inline_hex: str, mask_hex: str | None) -> bool:
    """CompareProp wie die ETS: mit Maske nur die freigegebenen Bits; ohne Maske zählt der gemeinsame
    Anfang (Erwartungswert ist oft länger als der Gerätewert, z. B. Hardwaretyp 10 vs. 6 Byte)."""
    expect = bytes.fromhex(inline_hex)
    mask = bytes.fromhex(mask_hex) if mask_hex else b"\xff" * len(expect)
    n = min(len(got), len(expect))
    return all((got[i] & mask[i]) == (expect[i] & mask[i]) for i in range(n) if i < len(mask))


@dataclass
class SimTarget(Target):
    """Gerät im Speicher. props: (Objekt, PID) → Bytes; mem: Adresse → Byte."""

    props: dict[tuple[int, int], bytes] = field(default_factory=dict)
    mem: dict[int, int] = field(default_factory=dict)
    states: dict[int, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    written: set[int] = field(default_factory=set)  # tatsächlich beschriebene Adressen
    elements: dict[tuple[int, int, int], bytes] = field(default_factory=dict)  # (Objekt, PID, Element) → Daten
    seed_rel: dict[int, dict[int, int]] = field(default_factory=dict)  # LSM → {Offset: Byte} alter Inhalt
    connected: bool = False
    next_rel: int = 0x3000

    async def connect(self) -> None:
        self.connected = True
        self.log.append("connect")

    async def disconnect(self) -> None:
        self.connected = False
        self.log.append("disconnect")

    async def restart(self) -> None:
        self.log.append("restart")

    def _need(self) -> None:
        if not self.connected:
            raise DownloadError("Nicht verbunden")

    async def load_event(self, lsm: int, event: str) -> None:
        self._need()
        before = self.states.get(lsm, "loaded")
        if event == "load" and before not in ("unloaded", "loaded"):
            raise DownloadError(f"LSM {lsm}: Laden aus Zustand {before}")
        if event == "completed" and before != "loading":
            raise DownloadError(f"LSM {lsm}: Abschluss ohne Laden")
        self.states[lsm] = {"unload": "unloaded", "load": "loading", "completed": "loaded"}[event]
        self.log.append(f"lsm {lsm} {event}")

    async def read_prop(self, obj: int, pid: int, start: int = 1) -> bytes | None:
        self._need()
        return self.props.get((obj, pid))

    async def write_prop(self, obj: int, pid: int, data: bytes, start: int = 1) -> None:
        self._need()
        self.props[(obj, pid)] = data
        self.elements[(obj, pid, start)] = data
        self.log.append(f"prop {obj}/{pid} ← {data.hex()}")

    async def read_mem(self, address: int, length: int) -> bytes | None:
        self._need()
        if not all(address + i in self.mem for i in range(length)):
            return None
        return bytes(self.mem[address + i] for i in range(length))

    async def write_mem(self, address: int, data: bytes) -> None:
        self._need()
        for i, b in enumerate(data):
            self.mem[address + i] = b
            self.written.add(address + i)
        self.log.append(f"mem {address:#06x} ← {len(data)} Byte")

    async def alloc_abs(self, lsm: int, address: int, size: int, attrs: dict | None = None) -> None:
        self._need()
        if self.states.get(lsm) != "loading":
            raise DownloadError(f"Segment für LSM {lsm} außerhalb des Ladens")
        self.log.append(f"alloc abs lsm {lsm} {address:#06x}+{size}")

    async def task_segment(self, lsm: int, address: int) -> None:
        self._need()
        if self.states.get(lsm) != "loading":
            raise DownloadError(f"Startadresse für LSM {lsm} außerhalb des Ladens")
        self.log.append(f"task segment lsm {lsm} {address:#06x}")

    async def find_object(self, object_type: int) -> int:
        for (obj, pid), data in self.props.items():
            if pid == 1 and int.from_bytes(data, "big") == object_type:
                return obj
        raise DownloadError(f"Kein Interface-Objekt vom Typ {object_type}")

    async def task_ctrl(self, kind: str, lsm: int, attrs: dict) -> None:
        self._need()
        self.log.append(f"{kind} lsm {lsm} {attrs}")

    async def alloc_rel(self, lsm: int, size: int, fill: int) -> int:
        self._need()
        if self.states.get(lsm) != "loading":
            raise DownloadError(f"Segment für LSM {lsm} außerhalb des Ladens")
        base = self.next_rel
        # Tabellen (Size 2, Mode 0) wachsen beim Schreiben – großzügig Abstand lassen
        self.next_rel += max(size, 0x1000) + 0x100
        for i in range(size):
            self.mem[base + i] = fill
        for off, v in self.seed_rel.get(lsm, {}).items():  # alter Inhalt wie im Gerät (Simulation)
            self.mem[base + off] = v
        self.props[(lsm, PID_TABLE_REFERENCE)] = base.to_bytes(4, "big")
        self.log.append(f"alloc rel lsm {lsm} {size} Byte bei {base:#06x}")
        return base


@dataclass
class Result:
    steps: int = 0
    bytes_written: int = 0
    writes: int = 0
    verified: int = 0
    mcb: tuple[int, int] | None = None  # Prüfsummen-Einträge (PID 27) gleich Gerät / verglichen
    log: list[str] = field(default_factory=list)


async def execute(steps: list[Step], target: Target, verify: bool = True, progress=None) -> Result:
    """Plan abarbeiten. progress(i, n, text) meldet den Fortschritt."""
    res = Result()
    rel_base: dict[int, int] = {}
    prop_writes: dict[tuple[int, int, int], bytes] = {}  # (Objekt, PID, Element) → geschriebene Daten
    written_len: dict[int, int] = {}  # Objektindex → geschriebene Länge (für MCB der Tabellen)
    abs_segments: dict[int, list[dict]] = {}  # LSM → feste Segmente in Plan-Reihenfolge (für MCB)

    async def write(address: int, chunks: list[tuple[int, bytes]], what: str) -> None:
        for off, data in chunks:
            for i in range(0, len(data), CHUNK):
                part = data[i:i + CHUNK]
                await target.write_mem(address + off + i, part)
                res.writes += 1
                res.bytes_written += len(part)
            if verify:
                back = await target.read_mem(address + off, len(data))
                if back != data:
                    raise DownloadError(f"{what}: Rücklesen bei {address + off:#06x} weicht ab")
                res.verified += 1

    for i, s in enumerate(steps, 1):
        if progress:
            progress(i, len(steps), s.text)
        a = s.attrs
        lsm = int(a.get("LsmIdx", a.get("ObjIdx", "0")) or 0)
        if s.op == "Connect":
            await target.connect()
        elif s.op == "Disconnect":
            await target.disconnect()
        elif s.op == "Restart":
            await target.restart()
        elif s.op == "Unload":
            await target.load_event(lsm, "unload")
        elif s.op == "Load":
            await target.load_event(lsm, "load")
        elif s.op == "LoadCompleted":
            await target.load_event(lsm, "completed")
        elif s.op == "CompareProp":
            got = await target.read_prop(int(a["ObjIdx"]), int(a["PropId"]))
            if got is not None and not compare_prop(got, a.get("InlineData", ""), a.get("Mask")):
                # Gerät passt nicht zur Applikation (z. B. anderer Hardwaretyp) → abbrechen
                raise DownloadError(f"Prüfung fehlgeschlagen: {s.text} (Gerät: {got.hex()})")
        elif s.op == "AbsSegment":
            abs_segments.setdefault(lsm, []).append(dict(a))
            await target.alloc_abs(lsm, int(a["Address"]), int(a["Size"]), dict(a))
            if s.chunks:
                await write(int(a["Address"]), s.chunks, s.text)
        elif s.op == "TaskSegment":
            await target.task_segment(lsm, int(a["Address"]))
        elif s.op == "RelSegment":
            rel_base[lsm] = await target.alloc_rel(lsm, int(a["Size"]), int(a.get("Fill", "0")))
        elif s.op == "WriteRelMem":
            base = rel_base.get(lsm)
            if base is None:
                ref = await target.read_prop(lsm, PID_TABLE_REFERENCE)
                base = int.from_bytes(ref, "big") if ref else None
            if base is None:
                raise DownloadError(f"{s.text}: Basisadresse unbekannt")
            await write(base + int(a.get("Offset", "0")), s.chunks, s.text)
            if s.data:
                written_len[lsm] = int(a.get("Offset", "0")) + len(s.data)
        elif s.op == "WriteMem":
            await write(int(a.get("Address", "0")), [(0, s.data or b"")], s.text)
        elif s.op == "WriteProp":
            if s.data is None:
                raise DownloadError(f"{s.text}: kein Wert bekannt")
            obj = int(a["ObjIdx"]) if a.get("ObjIdx") else await target.find_object(int(a["ObjType"]))
            start = int(a.get("StartElement", "1"))
            await target.write_prop(obj, int(a["PropId"]), s.data, start)
            prop_writes[(obj, int(a["PropId"]), start)] = s.data
        elif s.op in ("TaskCtrl1", "TaskCtrl2", "TaskPtr"):
            await target.task_ctrl(s.op, lsm, dict(a))
        elif s.op == "LoadImageProp":
            await load_image_prop(target, int(a["ObjIdx"]), int(a["PropId"]), int(a.get("Count", "1")),
                                  rel_base, prop_writes, written_len, abs_segments)
        elif s.op in ("MapError", "Delay"):
            pass
        else:
            raise DownloadError(f"Unbekannter Schritt: {s.op}")
        res.steps += 1
    res.log = list(getattr(target, "log", []))
    return res


def crc16_aug(data: bytes, crc: int = 0x1D0F) -> int:
    """CRC-16 mit Polynom 0x1021 – Prüfsumme der MCB-Tabelle (PID 27). An der Testanlage bestätigt:
    System B (relative Segmente) Start 0x1D0F = AUG-CCITT, 7/7 Segmente;
    System 7 (feste Segmente) Start 0xFFFF = CCITT-FALSE, 6/6 Segmente (B.E.G., Jung)."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


async def load_image_prop(target: Target, obj: int, pid: int, count: int, rel_base: dict[int, int],
                          prop_writes: dict, written_len: dict[int, int],
                          abs_segments: dict[int, list[dict]] | None = None) -> None:
    """LdCtrlLoadImageProp: MCB-Einträge (PID 27) mit Prüfsumme über den geschriebenen Speicher.

    Eintrag: Länge (4 Byte), zwei Steuerbytes, CRC (2 Byte). Länge/Steuerbytes stammen aus dem
    vorigen WriteProp desselben Elements (Parameter, mehrere Segmente hintereinander), sonst bei
    Tabellen aus der geschriebenen Länge mit Steuerbytes 00 33 (wie an 1.1.231/1.1.81 gelesen).
    Feste Segmente (System 7): je nichtflüchtigem Segment (MemType ≠ 2) des Objekts ein Eintrag mit
    voller Segmentgröße, Steuerbytes (00|01, Access), Prüfsumme nur bei SegFlags 0x80, sonst 0000
    (an B.E.G. 1.1.152 und Jung 1.1.161 gelesen).
    """
    segs = [g for g in (abs_segments or {}).get(obj, []) if g.get("MemType") != "2"]
    if segs and obj not in rel_base:
        for i, g in enumerate(segs[:count], 1):
            addr, size, flags = int(g["Address"]), int(g["Size"]), int(g.get("SegFlags", "0"))
            crc = 0
            if flags & 0x80:
                data = await target.read_mem(addr, size)
                if data is None:
                    raise DownloadError(f"Prüfsumme Objekt {obj}/{i}: Speicher {addr:#06x} nicht lesbar")
                crc = crc16_aug(data, 0xFFFF)
            entry = size.to_bytes(4, "big") + bytes([0x00 if flags & 0x80 else 0x01, int(g.get("Access", "0")) & 0xFF])
            await target.write_prop(obj, pid, entry + crc.to_bytes(2, "big"), i)
        return
    base = rel_base.get(obj)
    if base is None:
        ref = await target.read_prop(obj, PID_TABLE_REFERENCE)
        base = int.from_bytes(ref, "big") if ref else None
    if base is None:
        raise DownloadError(f"Prüfsumme Objekt {obj}: Basisadresse unbekannt")
    offset = 0
    for i in range(1, count + 1):
        desc = prop_writes.get((obj, pid, i))
        if desc is not None:
            size, ctrl = int.from_bytes(desc[:4], "big"), desc[4:6]
        elif obj in written_len:
            size, ctrl = written_len[obj], b"\x00\x33"
        else:
            raise DownloadError(f"Prüfsumme Objekt {obj}/{i}: Länge unbekannt")
        data = await target.read_mem(base + offset, size)
        if data is None:
            raise DownloadError(f"Prüfsumme Objekt {obj}/{i}: Speicher nicht lesbar")
        await target.write_prop(obj, pid, size.to_bytes(4, "big") + ctrl + crc16_aug(data).to_bytes(2, "big"), i)
        offset += size


def simulate_against_store(steps: list[Step], store, pa: str) -> tuple[Result, int, int]:
    """Plan im Simulator ausführen und die geschriebenen Bytes mit dem gelesenen Gerätespeicher
    vergleichen → (Ergebnis, gleich, abweichend). Relative Bereiche werden auf die Basis im Gerät
    umgerechnet. Nur Rechnung, kein Buszugriff."""
    import asyncio
    import json

    from .decode import Raw

    raw = Raw(store, pa)
    sim = SimTarget(next_rel=0x100000)  # relative Bereiche außerhalb des echten Adressraums
    for args, data in store.db.execute(  # gelesener Gerätespeicher als Ausgangszustand (für Prüfsummen)
            "SELECT args, data FROM read WHERE pa=? AND service='mem' AND error IS NULL ORDER BY ts", (pa,)):
        a0 = json.loads(args)["addr"]
        for i, v in enumerate(data):
            sim.mem[a0 + i] = v
    for obj in range(12):  # Objekttypen und Hardwaretyp wie im Gerät (für Prüf-Schritte)
        if (t := raw.prop(obj, 1)) is not None:
            sim.props[(obj, 1)] = t
        if (ref := raw.prop(obj, PID_TABLE_REFERENCE)) is not None:  # alter Inhalt relativer Bereiche
            dev_base = int.from_bytes(ref, "big")
            sim.seed_rel[obj] = {a - dev_base: v for a, v in sim.mem.items() if dev_base <= a < dev_base + 0x2000}
    if (hw := raw.prop(0, 78)) is not None:
        sim.props[(0, 78)] = hw
    res = asyncio.run(execute(steps, PlanGuard(sim, steps)))  # wie später am Bus: nur durch den Schreibschutz
    mem: dict[int, int] = {}
    for args, data in store.db.execute(
            "SELECT args, data FROM read WHERE pa=? AND service='mem' AND error IS NULL ORDER BY ts", (pa,)):
        a = json.loads(args)["addr"]
        for i, v in enumerate(data):
            mem[a + i] = v
    shift = {}
    for (obj, pid), ref in sim.props.items():
        if pid == PID_TABLE_REFERENCE and (dev := raw.prop(obj, PID_TABLE_REFERENCE)):
            shift[int.from_bytes(ref, "big")] = int.from_bytes(dev, "big")

    def real(a: int) -> int:
        for sb, db in shift.items():
            if sb <= a < sb + 0x1100 and sb >= 0x100000:
                return db + a - sb
        return a

    # Prüfsummen-Einträge gegen die im Gerät gelesenen (nur wo gelesen)
    compared = [(data, raw.prop(obj, pid, 1, el)) for (obj, pid, el), data in sim.elements.items() if pid == 27]
    compared = [(c, d) for c, d in compared if d is not None]
    if compared:
        res.mcb = (sum(c == d for c, d in compared), len(compared))
    eq = sum(1 for a in sim.written if real(a) in mem and mem[real(a)] == sim.mem[a])
    ne = sum(1 for a in sim.written if real(a) in mem and mem[real(a)] != sim.mem[a])
    return res, eq, ne


# --- Schreibschutz für den Download ------------------------------------------------------------


class WriteBlocked(DownloadError):
    """Ein Zugriff stand nicht im Plan."""


class PlanGuard(Target):
    """Sitzt vor dem eigentlichen Ziel und lässt nur durch, was der Plan vorsieht.

    Unabhängig von der Ausführung aus den Schritten abgeleitet:
    - Speicher schreiben nur in den Blöcken der Schreibschritte (absolute Adressen bzw. relativ
      zur Basis, die beim Anlegen/Lesen des Tabellenzeigers bekannt wird),
    - Properties schreiben nur, wo der Plan WriteProp/LoadImageProp für dieses Objekt vorsieht,
    - Laden/Entladen nur der Load State Machines aus dem Plan, Neustart nur, wenn geplant.
    Lesen ist immer erlaubt (das schützt der ReadOnlyGuard des Busses ohnehin nicht weg).
    """

    def __init__(self, target: Target, steps: list[Step]) -> None:
        self.target = target
        self.abs_ranges: list[tuple[int, int]] = []
        self.rel_chunks: dict[int, list[tuple[int, int]]] = {}
        self.rel_ranges: list[tuple[int, int]] = []
        self.lsms: set[int] = set()
        self.props: set[tuple[int | str, int]] = set()
        self.restart_planned = False
        self.blocked: list[str] = []
        for s in steps:
            a = s.attrs
            if s.op == "AbsSegment":
                self.abs_ranges += [(int(a["Address"]) + off, len(d)) for off, d in s.chunks]
            elif s.op == "WriteMem":
                self.abs_ranges.append((int(a.get("Address", "0")), len(s.data or b"")))
            elif s.op == "WriteRelMem":
                lsm = int(a.get("ObjIdx", "0"))
                self.rel_chunks.setdefault(lsm, []).extend(
                    (int(a.get("Offset", "0")) + off, len(d)) for off, d in s.chunks)
            elif s.op in ("Load", "Unload", "LoadCompleted"):
                self.lsms.add(int(a.get("LsmIdx", "0")))
            elif s.op == "WriteProp":
                obj = ("type", int(a["ObjType"])) if a.get("ObjType") else int(a.get("ObjIdx", "0"))
                self.props.add((obj, int(a["PropId"])))
            elif s.op == "LoadImageProp":
                self.props.add((int(a["ObjIdx"]), int(a["PropId"])))
            elif s.op == "Restart":
                self.restart_planned = True
        self.type_index: dict[int, int] = {}

    def _block(self, what: str) -> None:
        self.blocked.append(what)
        raise WriteBlocked(f"Schreibschutz: {what} steht nicht im Plan")

    def _rel_base(self, lsm: int, base: int) -> None:
        self.rel_ranges += [(base + off, n) for off, n in self.rel_chunks.get(lsm, [])]

    # Lesen und Verbindung: durchreichen
    async def connect(self) -> None:
        await self.target.connect()

    async def disconnect(self) -> None:
        await self.target.disconnect()

    async def read_mem(self, address: int, length: int) -> bytes | None:
        return await self.target.read_mem(address, length)

    async def read_prop(self, obj: int, pid: int, start: int = 1) -> bytes | None:
        data = await self.target.read_prop(obj, pid, start)
        if pid == PID_TABLE_REFERENCE and data and obj in self.rel_chunks:
            self._rel_base(obj, int.from_bytes(data, "big"))
        return data

    async def find_object(self, object_type: int) -> int:
        idx = await self.target.find_object(object_type)
        self.type_index[idx] = object_type
        return idx

    # Schreiben: nur nach Plan
    async def restart(self) -> None:
        if not self.restart_planned:
            self._block("Neustart")
        await self.target.restart()

    async def load_event(self, lsm: int, event: str) -> None:
        if lsm not in self.lsms:
            self._block(f"Ladezustand LSM {lsm} ({event})")
        await self.target.load_event(lsm, event)

    async def write_mem(self, address: int, data: bytes) -> None:
        end = address + len(data)
        if not any(a <= address and end <= a + n for a, n in self.abs_ranges + self.rel_ranges):
            self._block(f"Speicher {address:#06x}+{len(data)}")
        await self.target.write_mem(address, data)

    async def write_prop(self, obj: int, pid: int, data: bytes, start: int = 1) -> None:
        keys = {(obj, pid)} | ({(("type", self.type_index[obj]), pid)} if obj in self.type_index else set())
        if not keys & self.props:
            self._block(f"Property {obj}/{pid}")
        await self.target.write_prop(obj, pid, data, start)

    async def alloc_abs(self, lsm: int, address: int, size: int, attrs: dict | None = None) -> None:
        if lsm not in self.lsms:
            self._block(f"Segment für LSM {lsm}")
        await self.target.alloc_abs(lsm, address, size, attrs)

    async def task_segment(self, lsm: int, address: int) -> None:
        if lsm not in self.lsms:
            self._block(f"Startadresse für LSM {lsm}")
        await self.target.task_segment(lsm, address)

    async def alloc_rel(self, lsm: int, size: int, fill: int) -> int:
        if lsm not in self.lsms:
            self._block(f"Speicher für LSM {lsm}")
        base = await self.target.alloc_rel(lsm, size, fill)
        self._rel_base(lsm, base)
        return base

    async def task_ctrl(self, kind: str, lsm: int, attrs: dict) -> None:
        if lsm not in self.lsms:
            self._block(f"{kind} LSM {lsm}")
        await self.target.task_ctrl(kind, lsm, attrs)
