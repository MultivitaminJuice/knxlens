"""Download over the bus (E4) – for now only masks 070x (BIM M112, System 7).

Load control on 070x is memory mapped (KNX MP §3.31.2 "DMP_LoadStateMachineWrite_RCo_Mem",
numbers from Test Suite Supplement G; facts as collected by KNXBench, no code taken):
- every event is one A_Memory_Write of 11 bytes to 0x0104, first byte (state machine << 4) | event
  (event 1 load, 2 load completed, 3 additional load control, 4 unload),
- the state of machine n is one byte at 0xB6EA + n − 1 (0 unloaded, 1 loaded, 2 loading, 3 error),
- AllocAbsDataSeg/StackSeg: L3, type 00/01, 00, start, length, access, memory type, flags, 00,
- AllocAbsTaskSeg: L3, 02, 00, start, PEI type, manufacturer, application, version – the identity
  only for the application; address and association table get zeros (CP §3.9.2.2.2).

Not every 070x device takes these memory records: 1.1.83 (MDT, 0705) acknowledges them but ignores
them, with and without authorization (14.11.2026). Then the property form (MP §3.31.3.4) is used:
A_PropertyValue_Write to PID_LOAD_STATE_CONTROL (5) of the object, 10 bytes – event, then the same
fields without state machine nibble and segment ID. The first event (unloading a loaded machine)
decides which form the device takes; ignoring it changes nothing.

Safety, in this order: identity check, backup of every planned segment, PA check, plan guard
(only addresses of the plan), bus guard (writes only connection-oriented to this one device),
read-back of every written block, independent read-back after the restart.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from xknx.exceptions import ManagementConnectionError, ManagementConnectionRefused, ManagementConnectionTimeout
from xknx.telegram import IndividualAddress, apci

from . import bus
from .device import Reader
from .download import DownloadError, PlanGuard, Result, Target, execute
from .loadplan import Step

LOAD_CONTROL = 0x0104
LOAD_STATE = 0xB6EA
EVENT = {"load": 1, "completed": 2, "additional": 3, "unload": 4}
STATE_AFTER = {"load": 2, "completed": 1, "unload": 0}
STATE_NAMES = {0: "entladen", 1: "geladen", 2: "lädt", 3: "Fehler"}
MASKS_070X = (0x0700, 0x0701, 0x0705, 0x070F)


@dataclass(frozen=True)
class Identity:
    """Application identity written with the application's task segment (and checked before)."""

    manufacturer: int
    application: int
    version: int
    pei_type: int

    @property
    def program_version(self) -> bytes:  # PID 13 of the application object
        return self.manufacturer.to_bytes(2, "big") + self.application.to_bytes(2, "big") + bytes([self.version])


def event_record(lsm: int, event: str) -> bytes:
    return bytes([(lsm << 4) | EVENT[event]]) + bytes(10)


def segment_record(lsm: int, attrs: dict) -> bytes:
    seg_type = int(attrs.get("SegType", "0"))
    if seg_type not in (0, 1):
        raise DownloadError(f"Segmenttyp {seg_type} wird nicht unterstützt")
    address, size = int(attrs["Address"]), int(attrs["Size"])
    if not 0 < size or address + size - 1 > 0xFFFF:
        raise DownloadError(f"Segment {address:#06x}+{size} ungültig")
    flags = int(attrs.get("SegFlags", "0")) & 0x80
    return (bytes([(lsm << 4) | EVENT["additional"], seg_type, 0]) + address.to_bytes(2, "big") + size.to_bytes(2, "big")
            + bytes([int(attrs.get("Access", "0")) & 0xFF, int(attrs.get("MemType", "0")) & 0xFF, flags, 0]))


def task_record(lsm: int, address: int, ident: Identity | None) -> bytes:
    i = ident or Identity(0, 0, 0, 0)
    return (bytes([(lsm << 4) | EVENT["additional"], 2, 0]) + address.to_bytes(2, "big") + bytes([i.pei_type])
            + i.manufacturer.to_bytes(2, "big") + i.application.to_bytes(2, "big") + bytes([i.version]))


def prop_event_record(event: str) -> bytes:
    return bytes([EVENT[event]]) + bytes(9)


def prop_segment_record(attrs: dict) -> bytes:
    rec = segment_record(0, attrs)
    return bytes([EVENT["additional"], rec[1]]) + rec[3:]  # without segment ID


def prop_task_record(address: int, ident: Identity | None) -> bytes:
    rec = task_record(0, address, ident)
    return bytes([EVENT["additional"], 2]) + rec[3:]


class BusTarget(Target):
    """Download target on an open P2P connection to a 070x device."""

    def __init__(self, conn, reader: Reader, ident: Identity, state_wait: float = 0.3) -> None:  # noqa: ANN001
        self.conn = conn
        self.reader = reader
        self.ident = ident
        self.state_wait = state_wait
        self.log: list[str] = []
        self.restarted = False
        self.mode: str | None = None  # "mem" or "prop", decided by the first event

    async def _send(self, payload: apci.APCI) -> None:
        if self.restarted:
            raise DownloadError("Nach dem Neustart wird nichts mehr gesendet")
        try:
            await self.conn.send_data(payload)
        except (ManagementConnectionError, ManagementConnectionTimeout) as e:
            raise DownloadError(f"Senden fehlgeschlagen: {e}") from None

    async def _state(self, lsm: int) -> int | None:
        if self.mode == "prop":
            data = await self.reader.prop(lsm, 5)
        else:
            data = await self.reader.mem(LOAD_STATE + lsm - 1, 1)
        return data[0] if data else None

    async def _wait_state(self, lsm: int, expect: int) -> int | None:
        state = None
        for _ in range(3):  # MP: read the state back at most three times
            state = await self._state(lsm)
            if state == expect or state == 3:
                return state
            await asyncio.sleep(self.state_wait)
        return state

    async def _write_prop_control(self, lsm: int, record: bytes) -> None:
        assert len(record) == 10
        if self.restarted:
            raise DownloadError("Nach dem Neustart wird nichts mehr gesendet")
        try:
            resp = await self.conn.request(apci.PropertyValueWrite(object_index=lsm, property_id=5, count=1,
                                                                   start_index=1, data=record))
        except (ManagementConnectionError, ManagementConnectionTimeout) as e:
            raise DownloadError(f"Ladesteuerung LSM {lsm}: {e}") from None
        if resp.payload.count == 0:
            raise DownloadError(f"Ladesteuerung LSM {lsm}: Gerät lehnt das Schreiben von PID 5 ab")

    async def _control(self, lsm: int, mem_record: bytes, prop_record: bytes, expect: int, what: str) -> None:
        if not 1 <= lsm <= 4:
            raise DownloadError(f"Ladeautomat {lsm} gibt es bei 070x nicht")
        assert len(mem_record) == 11
        state = None
        if self.mode in (None, "mem"):
            before = await self._state(lsm) if self.mode is None else None
            await self._send(apci.MemoryWrite(address=LOAD_CONTROL, data=mem_record))
            state = await self._wait_state(lsm, expect)
            if self.mode is None:
                if state == expect:
                    self.mode = "mem"
                elif state == before and expect != before:
                    self.log.append(f"{what}: Speicherweg ignoriert, versuche PID 5")
                    self.mode = "prop"
                else:
                    raise DownloadError(f"{what}: LSM {lsm} meldet {STATE_NAMES.get(state, state)}, "
                                        f"erwartet {STATE_NAMES[expect]}")
        if self.mode == "prop":
            await self._write_prop_control(lsm, prop_record)
            state = await self._wait_state(lsm, expect)
        if state != expect:
            raise DownloadError(f"{what}: LSM {lsm} meldet {STATE_NAMES.get(state, state)}, erwartet {STATE_NAMES[expect]}")
        self.log.append(f"{what}: LSM {lsm} {STATE_NAMES.get(state, state)} ({self.mode})")

    async def connect(self) -> None:
        self.log.append("connect")  # the connection is opened by the caller

    async def disconnect(self) -> None:
        self.log.append("disconnect")

    async def restart(self) -> None:
        # Basic restart: not confirmed by the device (no T_ACK), the connection breaks down
        if self.restarted:
            return
        await self.conn.send_data(apci.Restart(), wait_for_ack=False)
        self.restarted = True
        self.log.append("restart")

    async def load_event(self, lsm: int, event: str) -> None:
        await self._control(lsm, event_record(lsm, event), prop_event_record(event), STATE_AFTER[event], event)

    async def alloc_abs(self, lsm: int, address: int, size: int, attrs: dict | None = None) -> None:
        attrs = attrs or {"Address": str(address), "Size": str(size)}
        await self._control(lsm, segment_record(lsm, attrs), prop_segment_record(attrs), 2, f"Segment {address:#06x}")

    async def task_segment(self, lsm: int, address: int) -> None:
        ident = self.ident if lsm == 3 else None
        await self._control(lsm, task_record(lsm, address, ident), prop_task_record(address, ident), 2,
                            f"Startadresse {address:#06x}")

    async def read_mem(self, address: int, length: int) -> bytes | None:
        return await self.reader.mem(address, length)

    async def write_mem(self, address: int, data: bytes) -> None:
        await self._send(apci.MemoryWrite(address=address, data=data))

    async def read_prop(self, obj: int, pid: int, start: int = 1) -> bytes | None:
        return await self.reader.prop(obj, pid, 1, start)

    async def write_prop(self, obj: int, pid: int, data: bytes, start: int = 1) -> None:
        if self.restarted:
            raise DownloadError("Nach dem Neustart wird nichts mehr gesendet")
        try:
            resp = await self.conn.request(apci.PropertyValueWrite(object_index=obj, property_id=pid, count=1,
                                                                   start_index=start, data=data))
        except (ManagementConnectionError, ManagementConnectionTimeout) as e:
            raise DownloadError(f"Property {obj}/{pid}: {e}") from None
        if resp.payload.count == 0 or resp.payload.data != data:
            raise DownloadError(f"Property {obj}/{pid}: Gerät hat den Wert nicht übernommen")

    async def alloc_rel(self, lsm: int, size: int, fill: int) -> int:
        raise DownloadError("Relative Segmente (System B) sind über den Bus noch nicht freigegeben")

    async def task_ctrl(self, kind: str, lsm: int, attrs: dict) -> None:
        raise DownloadError(f"{kind} ist über den Bus noch nicht freigegeben")

    async def find_object(self, object_type: int) -> int:
        raise DownloadError("Interface-Objekte nach Typ: über den Bus noch nicht freigegeben")


# --- Ablauf: prüfen, sichern, schreiben, nachprüfen ----------------------------------------------


@dataclass
class Report:
    pa: str
    checks: list[str] = field(default_factory=list)
    segments: dict[int, int] = field(default_factory=dict)  # address → size (backed up)
    changed: int = 0  # planned bytes that differ from the device
    planned: int = 0
    backup: Path | None = None
    result: Result | None = None
    after: dict[str, int] = field(default_factory=dict)  # same / different after the restart
    states: list[int | None] = field(default_factory=list)
    states_after: list[int | None] = field(default_factory=list)
    error: str = ""  # download aborted: why (device state is in states_after)


def planned_bytes(steps: list[Step]) -> dict[int, int]:
    """Absolute address → byte the plan writes."""
    out: dict[int, int] = {}
    for s in steps:
        if s.op == "AbsSegment" and s.chunks:
            for off, data in s.chunks:
                for i, b in enumerate(data):
                    out[int(s.attrs["Address"]) + off + i] = b
        elif s.op in ("WriteRelMem", "RelSegment", "TaskCtrl1", "TaskCtrl2", "TaskPtr", "WriteMem"):
            raise DownloadError(f"Schritt {s.op} wird über den Bus noch nicht unterstützt")
    return out


def backed_up_segments(steps: list[Step]) -> dict[int, int]:
    """Segments with content (EEPROM): back up completely, not only the planned bytes."""
    return {int(s.attrs["Address"]): int(s.attrs["Size"]) for s in steps if s.op == "AbsSegment" and s.chunks}


async def _read_all(r: Reader, segments: dict[int, int]) -> dict[int, bytes]:
    out = {}
    for addr, size in segments.items():
        data = await r.mem(addr, size)
        if data is None or len(data) != size:
            raise DownloadError(f"Speicher {addr:#06x}+{size} nicht vollständig lesbar")
        out[addr] = data
    return out


async def _states(r: Reader) -> list[int | None]:
    data = await r.mem(LOAD_STATE, 4)
    return list(data) if data and len(data) == 4 else [None] * 4


async def preflight(r: Reader, conn, pa: str, mask: int, ident: Identity, steps: list[Step],  # noqa: ANN001
                    serial: str = "") -> tuple[Report, dict[int, bytes]]:
    """Read-only: is it the right device, what would change? Raises DownloadError if not safe."""
    rep = Report(pa)
    if mask not in MASKS_070X:
        raise DownloadError(f"Maske {mask:04X}: Schreiben über den Bus bisher nur für 070x")
    try:
        resp = await conn.request(apci.DeviceDescriptorRead(descriptor=0))
    except (ManagementConnectionError, ManagementConnectionTimeout) as e:
        raise DownloadError(f"Gerät {pa} antwortet nicht: {e}") from None
    got_mask = resp.payload.value
    if got_mask != mask:
        raise DownloadError(f"Maske im Gerät {got_mask:04X}, erwartet {mask:04X}")
    rep.checks.append(f"Maske {got_mask:04X}")
    manu = await r.prop(0, 12)
    if manu is None or int.from_bytes(manu, "big") != ident.manufacturer:
        raise DownloadError(f"Hersteller im Gerät {manu.hex() if manu else '?'}, erwartet {ident.manufacturer:04X}")
    rep.checks.append(f"Hersteller {ident.manufacturer:04X}")
    if serial:
        sn = await r.prop(0, 11)
        if sn is None or sn.hex().upper() != serial.upper():
            raise DownloadError("Seriennummer passt nicht zum Gerät im Projekt")
        rep.checks.append("Seriennummer wie im Projekt")
    prog = await r.prop(3, 13)
    if prog != ident.program_version:
        # for now only the application already in the device (no change of application)
        raise DownloadError(f"Applikation im Gerät {prog.hex() if prog else '?'}, Plan {ident.program_version.hex()}")
    rep.checks.append(f"Applikation {ident.program_version.hex()} wie im Plan")
    rep.states = await _states(r)
    rep.checks.append("Ladezustand " + " ".join(STATE_NAMES.get(s, "?") if s is not None else "?" for s in rep.states))

    planned = planned_bytes(steps)
    rep.segments = backed_up_segments(steps)
    for a in planned:
        if not any(b <= a < b + n for b, n in rep.segments.items()):
            raise DownloadError(f"Adresse {a:#06x} liegt in keinem gesicherten Segment")
    backup = await _read_all(r, rep.segments)
    # own individual address in the address table (bytes 1–2) must stay as it is
    ia = IndividualAddress(pa).raw.to_bytes(2, "big")
    for s in steps:
        if s.op == "AbsSegment" and s.attrs.get("LsmIdx") == "1" and s.data is not None:
            if s.data[1:3] != ia:
                raise DownloadError(f"Adresstabelle enthält {s.data[1:3].hex()} statt der eigenen Adresse {ia.hex()}")
            rep.checks.append("eigene Adresse in der Adresstabelle unverändert")
    rep.planned = len(planned)
    rep.changed = sum(1 for a, v in planned.items() if _byte(backup, a) != v)
    return rep, backup


def _byte(mem: dict[int, bytes], a: int) -> int | None:
    for b, data in mem.items():
        if b <= a < b + len(data):
            return data[a - b]
    return None


def save_backup(folder: Path, pa: str, backup: dict[int, bytes], states: list, ident: Identity) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pa}_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps({
        "pa": pa, "time": time.time(), "program_version": ident.program_version.hex(),
        "load_states": states, "memory": {f"{a:#06x}": d.hex() for a, d in backup.items()},
    }, indent=1))
    return path


async def run(xknx, store, pa: str, mask: int, ident: Identity, steps: list[Step], backup_dir: Path,  # noqa: ANN001
              write: bool = False, serial: str = "", progress=None, settle: float = 4.0) -> Report:
    """Check and back up (always), then – only with write=True – download and verify."""
    addr = IndividualAddress(pa)
    guard = bus.GUARDS.get(id(xknx))
    if write and guard is None:
        raise DownloadError("Verbindung ohne ReadOnlyGuard – nur über bus.connect verbinden")
    conn = await xknx.management.connect(addr)
    try:
        r = Reader(conn, store)
        rep, backup = await preflight(r, conn, pa, mask, ident, steps, serial)
        rep.backup = save_backup(backup_dir, pa, backup, rep.states, ident)
        if not write:
            return rep
        # like the ETS: authorize with the default key first – without it the device acknowledges
        # load control writes but ignores them (1.1.83, mask 0705, 14.11.2026)
        with guard.permit("authorize"):
            try:
                auth = await conn.request(apci.AuthorizeRequest(key=0xFFFFFFFF))
            except (ManagementConnectionError, ManagementConnectionTimeout) as e:
                raise DownloadError(f"Authorize fehlgeschlagen: {e}") from None
        rep.checks.append(f"Zugriffsstufe {auth.payload.level} (Standardschlüssel)")
        target = BusTarget(conn, r, ident)
        with guard.permit_download(pa):
            try:
                rep.result = await execute(steps, PlanGuard(target, steps), progress=progress)
            except Exception as e:  # noqa: BLE001 - reported, device state is read below
                rep.error = f"{type(e).__name__}: {e}"
                rep.result = Result(log=list(target.log))
    finally:
        try:
            await xknx.management.disconnect(addr)
        except (ManagementConnectionError, ManagementConnectionTimeout, ManagementConnectionRefused):
            pass  # after the restart the device has dropped the connection already
    # after the restart (or an abort): new connection, everything read back independently
    await asyncio.sleep(settle)
    expected = {a: bytearray(d) for a, d in backup.items()}
    for a, v in planned_bytes(steps).items():
        for b, buf in expected.items():
            if b <= a < b + len(buf):
                buf[a - b] = v
    async with xknx.management.connection(addr) as conn:
        r = Reader(conn, store)
        rep.states_after = await _states(r)
        after = await _read_all(r, rep.segments)
    same = sum(1 for b, d in after.items() for i, v in enumerate(d) if expected[b][i] == v)
    total = sum(len(d) for d in after.values())
    rep.after = {"gleich": same, "abweichend": total - same}
    return rep
