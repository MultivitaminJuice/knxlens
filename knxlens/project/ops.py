"""Bearbeitungsbefehle für ein Projekt – mit Rückgängig/Wiederholen.

Jede Änderung läuft über `Editor.run(name, fn)`: vorher wird eine Momentaufnahme der
veränderlichen Teile gesichert (ohne Produktdaten). Regeln wie „nächste freie Adresse“
oder „Verknüpfungen wandern beim Verschieben mit“ liegen hier, nicht in der Oberfläche.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from xknx.telegram import GroupAddress as _GA

from . import ComLink, Device, GroupAddress, GroupRange, Line, Project

UNDO_LIMIT = 100


class OpError(ValueError):
    """Befehl nicht ausführbar – Meldung ist für den Nutzer gedacht."""


def ga_str(raw: int) -> str:
    return str(_GA(raw))


def ga_raw(address: str) -> int:
    try:
        return _GA(address).raw
    except Exception:  # noqa: BLE001 - xknx wirft verschiedene Typen
        raise OpError(f"Ungültige Gruppenadresse: {address}") from None


@dataclass
class Editor:
    project: Project
    undo_stack: list[tuple[str, dict]] = field(default_factory=list)
    redo_stack: list[tuple[str, dict]] = field(default_factory=list)

    # --- Rückgängig -------------------------------------------------------------

    def _snapshot(self) -> dict:
        p = self.project
        return copy.deepcopy({"name": p.name, "lines": p.lines, "devices": p.devices, "ranges": p.ranges,
                              "group_addresses": p.group_addresses, "spaces": p.spaces})

    def _restore(self, snap: dict) -> None:
        for k, v in copy.deepcopy(snap).items():
            setattr(self.project, k, v)

    def run(self, name: str, fn: Callable[[], object]) -> object:
        snap = self._snapshot()
        try:
            result = fn()
        except Exception:
            self._restore(snap)  # nichts halb geändert zurücklassen
            raise
        self.undo_stack.append((name, snap))
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        return result

    def undo(self) -> str:
        if not self.undo_stack:
            raise OpError("Nichts rückgängig zu machen")
        name, snap = self.undo_stack.pop()
        self.redo_stack.append((name, self._snapshot()))
        self._restore(snap)
        return name

    def redo(self) -> str:
        if not self.redo_stack:
            raise OpError("Nichts zu wiederholen")
        name, snap = self.redo_stack.pop()
        self.undo_stack.append((name, self._snapshot()))
        self._restore(snap)
        return name

    # --- Hilfen -------------------------------------------------------------------

    def _range(self, uid: str) -> GroupRange:
        r = next((r for r in self.project.ranges if r.uid == uid), None)
        if r is None:
            raise OpError("Gruppenbereich nicht gefunden")
        return r

    def _device(self, uid: str) -> Device:
        d = next((d for d in self.project.devices if d.uid == uid), None)
        if d is None:
            raise OpError("Gerät nicht gefunden")
        return d

    def _new_uid(self, kind: str) -> str:
        pid = self.project.project_id
        pattern = re.compile(rf"^{re.escape(pid)}-0_{kind}-(\d+)$")
        used = [int(m[1]) for u in self._all_uids() if (m := pattern.match(u))]
        return f"{pid}-0_{kind}-{max(used, default=0) + 1}"

    def _all_uids(self) -> list[str]:
        p = self.project
        return ([d.uid for d in p.devices] + [r.uid for r in p.ranges] + [s.uid for s in p.spaces]
                + [g.extra.get("Id", "") for g in p.group_addresses.values()])

    def _is_middle(self, r: GroupRange) -> bool:
        return r.parent is not None

    def _middle_for(self, raw: int) -> GroupRange | None:
        return next((r for r in self.project.ranges if self._is_middle(r) and r.start <= raw <= r.end), None)

    def _free_in(self, r: GroupRange, count: int = 1, exclude: set[int] | None = None) -> list[int]:
        used = {g.raw for g in self.project.group_addresses.values()} | (exclude or set())
        # Untergruppe 0 wird nur auf ausdrücklichen Wunsch vergeben (üblich: ab …/1)
        free = [a for a in range(max(r.start, 1), r.end + 1) if a not in used and a & 0xFF]
        if len(free) < count:
            raise OpError(f"In „{r.name}“ sind nur noch {len(free)} Adressen frei")
        return free[:count]

    def _rename_ga_everywhere(self, old: str, new: str) -> None:
        for d in self.project.devices:
            for link in d.links:
                link.gas = [new if g == old else g for g in link.gas]

    def _used_addresses(self, area: int, line: int, exclude: set[str] | None = None) -> set[int]:
        return {d.address for d in self.project.devices
                if (d.area, d.line) == (area, line) and d.address is not None and d.uid not in (exclude or set())}

    def _line(self, area: int, line: int) -> Line:
        ln = next((l for l in self.project.lines if (l.area, l.line) == (area, line)), None)
        if ln is None:
            raise OpError(f"Linie {area}.{line} existiert nicht")
        return ln

    # --- Gruppenbereiche ------------------------------------------------------------

    def range_add(self, parent_uid: str | None, name: str = "") -> str:
        def do():
            if parent_uid is None:
                used = {r.start >> 11 for r in self.project.ranges if r.parent is None}
                main = next((m for m in range(32) if m not in used), None)
                if main is None:
                    raise OpError("Alle 32 Hauptgruppen sind belegt")
                r = GroupRange(self._new_uid("GR"), name or f"Hauptgruppe {main}", max(1, main << 11),
                               (main << 11) | 0x7FF)
            else:
                parent = self._range(parent_uid)
                if parent.parent is not None:
                    raise OpError("Mittelgruppen gehören in eine Hauptgruppe")
                main = parent.end >> 11
                used = {(r.end >> 8) & 7 for r in self.project.ranges if r.parent == parent_uid}
                middle = next((m for m in range(8) if m not in used), None)
                if middle is None:
                    raise OpError("Alle 8 Mittelgruppen dieser Hauptgruppe sind belegt")
                start = (main << 11) | (middle << 8)
                r = GroupRange(self._new_uid("GR"), name or f"Mittelgruppe {main}/{middle}", max(1, start),
                               start | 0xFF, parent_uid)
            self.project.ranges.append(r)
            return r.uid
        return self.run("Gruppe anlegen", do)

    def range_rename(self, uid: str, name: str) -> None:
        def do():
            if not name.strip():
                raise OpError("Name darf nicht leer sein")
            self._range(uid).name = name.strip()
        self.run("Gruppe umbenennen", do)

    def range_delete(self, uid: str, force: bool = False) -> int:
        def do():
            r = self._range(uid)
            ranges = [r] + [c for c in self.project.ranges if c.parent == uid]
            gas = [g.address for g in self.project.group_addresses.values()
                   if any(x.start <= g.raw <= x.end for x in ranges)]
            if gas and not force:
                raise OpError(f"„{r.name}“ enthält {len(gas)} Gruppenadressen")
            removed = self._delete_gas(gas)
            self.project.ranges = [x for x in self.project.ranges if x not in ranges]
            return removed
        return self.run("Gruppe löschen", do)

    # --- Gruppenadressen --------------------------------------------------------------

    def ga_add(self, range_uid: str, name: str = "", address: str | None = None, dpt: str = "") -> str:
        def do():
            r = self._range(range_uid)
            if not self._is_middle(r):
                raise OpError("Gruppenadressen gehören in eine Mittelgruppe")
            if address:
                raw = ga_raw(address)
                if not r.start <= raw <= r.end:
                    raise OpError(f"{address} liegt nicht in „{r.name}“")
                if ga_str(raw) in self.project.group_addresses:
                    raise OpError(f"{address} ist schon vergeben")
            else:
                raw = self._free_in(r)[0]
            addr = ga_str(raw)
            self.project.group_addresses[addr] = GroupAddress(addr, name, dpt, range_uid=r.uid,
                                                              extra={"Id": self._new_uid("GA")})
            return addr
        return self.run("Gruppenadresse anlegen", do)

    def ga_update(self, address: str, **fields) -> None:
        def do():
            g = self.project.group_addresses.get(address)
            if g is None:
                raise OpError(f"{address} nicht gefunden")
            for k, v in fields.items():
                if k == "central":
                    if v:
                        g.extra["Central"] = "true"
                    else:
                        g.extra.pop("Central", None)
                elif k in ("name", "dpt", "description"):
                    setattr(g, k, (v or "").strip())
        self.run("Gruppenadresse ändern", do)

    def ga_readdress(self, old: str, new: str) -> None:
        def do():
            g = self.project.group_addresses.get(old)
            if g is None:
                raise OpError(f"{old} nicht gefunden")
            raw = ga_raw(new)
            new_s = ga_str(raw)
            if new_s == old:
                return
            if new_s in self.project.group_addresses:
                raise OpError(f"{new_s} ist schon vergeben")
            r = self._middle_for(raw)
            if r is None:
                raise OpError(f"Für {new_s} gibt es keine Mittelgruppe")
            self._replace_ga(old, new_s, r.uid)
        self.run("Adresse ändern", do)

    def _replace_ga(self, old: str, new: str, range_uid: str) -> None:
        gas = self.project.group_addresses
        g = gas.pop(old)
        g.address, g.range_uid = new, range_uid
        gas[new] = g
        self._rename_ga_everywhere(old, new)

    def ga_move(self, addresses: list[str], target_uid: str) -> dict[str, str]:
        """In eine andere Mittelgruppe verschieben; nächste freie Adressen, Verknüpfungen wandern mit."""
        def do():
            target = self._range(target_uid)
            if not self._is_middle(target):
                raise OpError("Ziel muss eine Mittelgruppe sein")
            moving = [a for a in addresses if a in self.project.group_addresses]
            stay = [a for a in moving if target.start <= ga_raw(a) <= target.end]
            todo = [a for a in moving if a not in stay]
            free = self._free_in(target, len(todo))
            mapping = {}
            for old, raw in zip(todo, free):
                new = ga_str(raw)
                self._replace_ga(old, new, target.uid)
                mapping[old] = new
            return mapping
        return self.run("Gruppenadressen verschieben", do)

    def ga_copy(self, addresses: list[str], target_uid: str) -> list[str]:
        def do():
            target = self._range(target_uid)
            if not self._is_middle(target):
                raise OpError("Ziel muss eine Mittelgruppe sein")
            src = [self.project.group_addresses[a] for a in addresses if a in self.project.group_addresses]
            new = []
            for g, raw in zip(src, self._free_in(target, len(src))):
                addr = ga_str(raw)
                self.project.group_addresses[addr] = GroupAddress(
                    addr, g.name, g.dpt, g.description, target.uid,
                    {k: v for k, v in g.extra.items() if k != "Id"} | {"Id": self._new_uid("GA")})
                new.append(addr)
            return new
        return self.run("Gruppenadressen kopieren", do)

    def ga_delete(self, addresses: list[str]) -> int:
        return self.run("Gruppenadressen löschen", lambda: self._delete_gas(addresses))

    def _delete_gas(self, addresses: list[str]) -> int:
        gone = set(addresses)
        removed = 0
        for d in self.project.devices:
            for link in d.links:
                before = len(link.gas)
                link.gas = [g for g in link.gas if g not in gone]
                removed += before - len(link.gas)
            d.links = [l for l in d.links if l.gas]
        for a in addresses:
            self.project.group_addresses.pop(a, None)
        return removed

    # --- Verknüpfungen ---------------------------------------------------------------

    def link(self, device_uid: str, ref: str, address: str, number: int | None = None, sending: bool = False) -> None:
        def do():
            d = self._device(device_uid)
            if address not in self.project.group_addresses:
                raise OpError(f"{address} ist keine Gruppenadresse des Projekts")
            link = next((l for l in d.links if l.ref == ref), None)
            if link is None:
                link = ComLink(ref, [], number)
                d.links.append(link)
            if address in link.gas:
                link.gas.remove(address)
                link.gas.insert(0 if sending else len(link.gas), address)
            else:
                link.gas.insert(0 if sending else len(link.gas), address)
        self.run("Verknüpfen", do)

    def unlink(self, device_uid: str, ref: str, address: str) -> None:
        def do():
            d = self._device(device_uid)
            link = next((l for l in d.links if l.ref == ref and address in l.gas), None)
            if link is None:
                raise OpError("Verknüpfung nicht gefunden")
            link.gas.remove(address)
            d.links = [l for l in d.links if l.gas]
        self.run("Verknüpfung lösen", do)

    def set_sending(self, device_uid: str, ref: str, address: str) -> None:
        def do():
            d = self._device(device_uid)
            link = next((l for l in d.links if l.ref == ref and address in l.gas), None)
            if link is None:
                raise OpError("Verknüpfung nicht gefunden")
            link.gas.remove(address)
            link.gas.insert(0, address)
        self.run("Sendende GA festlegen", do)

    # --- Geräte -----------------------------------------------------------------------

    def device_update(self, uid: str, **fields) -> None:
        def do():
            d = self._device(uid)
            for k in ("name", "description", "comment"):
                if k in fields:
                    setattr(d, k, (fields[k] or "").strip())
        self.run("Gerät ändern", do)

    def device_set_pa(self, uid: str, pa: str) -> None:
        def do():
            m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", pa)
            if not m:
                raise OpError("Physikalische Adresse als Bereich.Linie.Gerät, z. B. 1.1.12")
            area, line, addr = (int(x) for x in m.groups())
            if not (0 <= area <= 15 and 0 <= line <= 15 and 0 <= addr <= 255):
                raise OpError("Bereich/Linie 0–15, Gerät 0–255")
            self._line(area, line)
            d = self._device(uid)
            if addr in self._used_addresses(area, line, {uid}):
                raise OpError(f"{area}.{line}.{addr} ist schon vergeben")
            d.area, d.line, d.address = area, line, addr
        self.run("Physikalische Adresse ändern", do)

    def _next_free(self, area: int, line: int, used_extra: set[int]) -> int:
        used = self._used_addresses(area, line) | used_extra
        free = next((a for a in range(1, 256) if a not in used), None)
        if free is None:
            raise OpError(f"Linie {area}.{line} ist voll")
        return free

    def device_move(self, uids: list[str], area: int, line: int) -> None:
        def do():
            self._line(area, line)
            taken: set[int] = set()
            for uid in uids:
                d = self._device(uid)
                if (d.area, d.line) == (area, line):
                    continue
                d.address = self._next_free(area, line, taken)  # Adresse auf der Ziellinie, bevor d dorthin wechselt
                d.area, d.line = area, line
                taken.add(d.address)
        self.run("Geräte verschieben", do)

    def device_copy(self, uids: list[str], area: int, line: int, with_links: bool = False) -> list[str]:
        def do():
            self._line(area, line)
            new = []
            taken: set[int] = set()
            for uid in uids:
                src = self._device(uid)
                d = copy.deepcopy(src)
                d.uid = self._new_uid("DI")
                d.area, d.line = area, line
                d.address = self._next_free(area, line, taken)
                taken.add(d.address)
                d.serial = ""
                d.origin = "neu"
                d.extra = {k: v for k, v in d.extra.items() if k == "__order__"}  # Ladestatus usw. nicht kopieren
                if not with_links:
                    d.links = []
                self.project.devices.append(d)
                new.append(d.uid)
            return new
        return self.run("Geräte kopieren", do)

    def device_delete(self, uids: list[str]) -> None:
        def do():
            gone = set(uids)
            self.project.devices = [d for d in self.project.devices if d.uid not in gone]
            for s in self.project.spaces:
                s.devices = [u for u in s.devices if u not in gone]
        self.run("Geräte löschen", do)

    def device_insert(self, product_ref: str, h2p_ref: str, app_id: str, area: int, line: int,
                      name: str = "") -> str:
        def do():
            self._line(area, line)
            d = Device(uid=self._new_uid("DI"), area=area, line=line, address=self._next_free(area, line, set()),
                       product_ref=product_ref, h2p_ref=h2p_ref, app_id=app_id, name=name, origin="neu")
            self.project.devices.append(d)
            return d.uid
        return self.run("Gerät einfügen", do)
