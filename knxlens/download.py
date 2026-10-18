"""Download ausführen: Plan (loadplan.Step) Schritt für Schritt gegen ein Ziel abarbeiten.

Ziele:
- SimTarget: bildet ein Gerät im Speicher nach (Load State Machines, Speicher, Properties).
  Damit lässt sich jeder Download vollständig durchspielen, ohne den Bus zu berühren.
- ein Busziel folgt in E4 – mit eigenem Schreibschutz (nur Adressen/Properties aus dem Plan),
  ausdrücklicher Freigabe je Gerät und Rücklesen jedes geschriebenen Blocks.

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
    async def alloc_abs(self, lsm: int, address: int, size: int) -> None: ...
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

    async def alloc_abs(self, lsm: int, address: int, size: int) -> None:
        self._need()
        if self.states.get(lsm) != "loading":
            raise DownloadError(f"Segment für LSM {lsm} außerhalb des Ladens")
        self.log.append(f"alloc abs lsm {lsm} {address:#06x}+{size}")

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
        self.props[(lsm, PID_TABLE_REFERENCE)] = base.to_bytes(4, "big")
        self.log.append(f"alloc rel lsm {lsm} {size} Byte bei {base:#06x}")
        return base


@dataclass
class Result:
    steps: int = 0
    bytes_written: int = 0
    writes: int = 0
    verified: int = 0
    log: list[str] = field(default_factory=list)


async def execute(steps: list[Step], target: Target, verify: bool = True, progress=None) -> Result:
    """Plan abarbeiten. progress(i, n, text) meldet den Fortschritt."""
    res = Result()
    rel_base: dict[int, int] = {}

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
            await target.alloc_abs(lsm, int(a["Address"]), int(a["Size"]))
            if s.chunks:
                await write(int(a["Address"]), s.chunks, s.text)
        elif s.op == "TaskSegment":
            pass  # Startadresse: Teil der Segmentverwaltung des Geräts
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
        elif s.op == "WriteMem":
            await write(int(a.get("Address", "0")), [(0, s.data or b"")], s.text)
        elif s.op == "WriteProp":
            if s.data is None:
                raise DownloadError(f"{s.text}: kein Wert bekannt")
            obj = int(a["ObjIdx"]) if a.get("ObjIdx") else await target.find_object(int(a["ObjType"]))
            await target.write_prop(obj, int(a["PropId"]), s.data, int(a.get("StartElement", "1")))
        elif s.op in ("TaskCtrl1", "TaskCtrl2", "TaskPtr"):
            await target.task_ctrl(s.op, lsm, dict(a))
        elif s.op in ("LoadImageProp", "MapError", "Delay"):
            pass  # TODO E4: Bedeutung von LoadImageProp an echten Geräten klären
        else:
            raise DownloadError(f"Unbekannter Schritt: {s.op}")
        res.steps += 1
    res.log = list(getattr(target, "log", []))
    return res


def simulate_against_store(steps: list[Step], store, pa: str) -> tuple[Result, int, int]:
    """Plan im Simulator ausführen und die geschriebenen Bytes mit dem gelesenen Gerätespeicher
    vergleichen → (Ergebnis, gleich, abweichend). Relative Bereiche werden auf die Basis im Gerät
    umgerechnet. Nur Rechnung, kein Buszugriff."""
    import asyncio
    import json

    from .decode import Raw

    raw = Raw(store, pa)
    sim = SimTarget()
    for obj in range(12):  # Objekttypen und Hardwaretyp wie im Gerät (für Prüf-Schritte)
        if (t := raw.prop(obj, 1)) is not None:
            sim.props[(obj, 1)] = t
    if (hw := raw.prop(0, 78)) is not None:
        sim.props[(0, 78)] = hw
    res = asyncio.run(execute(steps, sim))
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
            if sb <= a < sb + 0x1100:
                return db + a - sb
        return a

    eq = sum(1 for a in sim.written if real(a) in mem and mem[real(a)] == sim.mem[a])
    ne = sum(1 for a in sim.written if real(a) in mem and mem[real(a)] != sim.mem[a])
    return res, eq, ne
