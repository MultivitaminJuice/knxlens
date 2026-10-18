"""Download-Abbild: was die ETS beim Programmieren in ein Gerät schreiben würde.

Nur Berechnung, kein Buszugriff. Grundlage sind die Projektwerte eines Geräts und die
Produktdaten (Segmente mit Standardbild, Parameter mit Speicherort, Dynamic-Teil).

Regeln (gegen ausgelesene Geräte geprüft, siehe tests/test_image.py und `knxlens image`):
- Ausgangspunkt ist das Standardbild jedes Segments (Data der Produktdatei), bei relativen
  Segmenten aufgefüllt mit dem Fill-Wert aus LdCtrlRelSegment (nur Bytes ohne Parameter; Bytes
  mit Parametern beginnen bei 0). Bei absoluten Segmenten ohne Standardbild schreibt die ETS nur
  die Parameterbytes (Lücken behalten im Gerät ihren alten Inhalt) – siehe ParamImage.defined.
- Geschrieben werden die Parameter im aktiven Teil des Dynamic-Baums mit dem Projektwert der
  aktiven ParameterRef bzw. deren Standardwert. Werte gehören zur Ref, nicht zum Parameter:
  ein Wert unter einer anderen Ref desselben Parameters gilt nicht, ein unter einer nicht
  änderbaren Ref gespeicherter Wert schon (MDT 1.1.22/24/83/213/232).
- Parameter außerhalb des aktiven Teils bekommen keinen Projektwert; ihre Bytes behalten das
  Standardbild (MDT/Gira schreiben sie so mit, siehe ETS-Ladeabbild; mit ihren Standardwerten
  beschrieben wären es 7 514 statt 124 abweichende Bytes).
- Mehrere aktive Refs eines Parameters: die änderbare gilt, sonst die erste im Baum.
- Unions: nur das aktive Mitglied; ist keins aktiv, bleibt das Standardbild (DefaultUnionParameter
  wird nicht geschrieben – gegengeprüft, das Gerät enthält dort das Bild der Produktdatei).
- Assign im aktiven Zweig setzt den Zielparameter auf Quellwert bzw. festen Wert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .params import AppParams, Context, Param, PRef, _default, encode_value, parse_value


@dataclass
class Write:
    segment: str | None  # None → Property
    bit: int  # ab Segmentanfang (inkl. Modul-Basisoffset)
    bits: int
    raw: int | bytes
    instance_id: str
    text: str
    obj: int | None = None
    pid: int | None = None


@dataclass
class ParamImage:
    segments: dict[str, bytearray]
    writes: list[Write] = field(default_factory=list)
    props: dict[tuple[int, int], bytearray] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    touched: set[tuple[str, int]] = field(default_factory=set)

    def defined(self, segment: str, offset: int, ap: AppParams) -> bool:
        """Wird dieses Byte geschrieben? Absolute Segmente ohne Standardbild: nur Parameterbytes."""
        s = ap.segments[segment]
        return s.absolute is False or offset < len(s.data) or (segment, offset) in self.touched

    def owner(self, segment: str, offset: int) -> list[Write]:
        """Welche Parameter liegen auf diesem Byte? (für Abweichungsberichte)"""
        return [w for w in self.writes if w.segment == segment and w.bit // 8 <= offset <= (w.bit + w.bits - 1) // 8]


def put(buf: bytearray, bit: int, bits: int, raw: int | bytes) -> None:
    """Wert MSB-zuerst an Bitposition schreiben (Umkehr von Evaluation.read_raw)."""
    start = bit // 8
    if isinstance(raw, bytes):
        n = bits // 8
        data = raw[:n].ljust(n, b"\x00")
        if bit % 8:
            raise ValueError("Text/Farbe nicht byteweise ausgerichtet")
        _grow(buf, start + n)
        buf[start : start + n] = data
        return
    nbytes = (bit % 8 + bits + 7) // 8
    _grow(buf, start + nbytes)
    shift = nbytes * 8 - bit % 8 - bits
    mask = ((1 << bits) - 1) << shift
    cur = int.from_bytes(buf[start : start + nbytes], "big")
    cur = (cur & ~mask) | ((raw << shift) & mask)
    buf[start : start + nbytes] = cur.to_bytes(nbytes, "big")


def _grow(buf: bytearray, n: int) -> None:
    if len(buf) < n:
        buf.extend(b"\x00" * (n - len(buf)))


def param_image(ap: AppParams, values: dict[str, str]) -> ParamImage:
    """values: Projektwerte des Geräts {ParameterInstanceRef-Id: Wert} (nur Abweichungen vom Standard)."""
    img = ParamImage({sid: _initial(ap, s) for sid, s in ap.segments.items()})
    ev = ap.evaluate_values(values)

    # aktive Refs: alles, was die Auswertung im aktiven Teil aufgelöst hat
    active: dict[tuple[str, str | None], tuple[PRef, Context, str]] = {}
    for (ref_id, inst), v in ev._cache.items():
        ref = ap.refs.get(ref_id)
        if ref is None or v is None:
            continue
        key = (ref.param.id, inst)
        prev = active.get(key)
        if prev is None or _wins(ref, prev[0]):
            active[key] = (ref, ev.contexts.get(inst, Context()), v.instance_id)

    chosen: dict[tuple[str, str | None], tuple[PRef, Context, str, object]] = {}
    for key, (ref, ctx, iid) in active.items():
        # Wert der aktiven Ref aus dem Projekt, sonst ihr Standardwert – auch bei nicht änderbaren Refs
        stored = values.get(iid)
        value = parse_value(ref, stored)[0] if stored is not None else _default(ref)[0]
        chosen[key] = (ref, ctx, iid, value)

    # Assign: Zielwert aus Quelle oder fest
    for target, ctx, source, fixed in ev.assigns:
        tref = ap.refs.get(target)
        if tref is None:
            continue
        if source:
            sref = ap.refs.get(source)
            src = chosen.get((sref.param.id, ctx.instance)) if sref else None
            value = src[3] if src else (_default(sref)[0] if sref else None)
        else:
            value = parse_value(tref, fixed or "")[0]
        if value is not None:
            chosen[(tref.param.id, ctx.instance)] = (tref, ctx, tref.id, value)

    for (pid_, inst), (ref, ctx, iid, value) in chosen.items():
        _write(img, ap, ref.param, ctx, value, iid, ref.text)
    return img


def _wins(new: PRef, old: PRef) -> bool:
    """Mehrere aktive Refs eines Parameters: die änderbare gilt, sonst die erste im Baum
    (MDT-Dimmer 1.1.22: Gerät enthält den Wert der änderbaren Ref)."""
    return old.access in ("None", "Read") and new.access not in ("None", "Read")


def _initial(ap: AppParams, s) -> bytearray:
    """Standardbild des Segments; dahinter Füllwert (relativ: aus der Ladeprozedur, absolut: 0xFF)."""
    fill = ap.rel_fill.get(s.lsm, 0) if not s.absolute else 0xFF
    return bytearray(s.data + bytes([fill]) * max(0, s.size - len(s.data)))


def _belongs(p: Param, ctx: Context) -> bool:
    """Modulparameter nur in ihrer Instanz, Applikationsparameter nur auf oberster Ebene."""
    if p.loc is None:
        return False
    if p.loc.base_arg:
        return p.loc.base_arg in ctx.numeric
    return ctx.instance is None


def _first_ref(ap: AppParams, p: Param) -> PRef | None:
    return next((r for r in ap.refs.values() if r.param is p), None)


def _write(img: ParamImage, ap: AppParams, p: Param, ctx: Context, value, iid: str, text: str) -> None:
    loc, t = p.loc, p.ptype
    if loc is None or t.kind == "none" or t.bits == 0 or value is None:
        return
    try:
        raw = encode_value(t, value)
    except (ValueError, TypeError, OverflowError) as e:
        img.problems.append(f"{text or p.id}: Wert {value!r} nicht kodierbar ({e})")
        return
    if loc.segment is None:
        buf = img.props.setdefault((loc.obj, loc.pid), bytearray())
        put(buf, loc.bit, t.bits, raw)
        img.writes.append(Write(None, loc.bit, t.bits, raw, iid, text, loc.obj, loc.pid))
        return
    seg = img.segments.get(loc.segment)
    if seg is None:
        img.problems.append(f"{text or p.id}: Segment {loc.segment} fehlt")
        return
    base = ctx.numeric.get(loc.base_arg, 0) * 8 if loc.base_arg else 0
    s = ap.segments[loc.segment]
    # Füllwert gilt nur für Bytes ohne Parameter; belegte Bytes beginnen bei 0
    for b in range((base + loc.bit) // 8, (base + loc.bit + t.bits - 1) // 8 + 1):
        if b >= len(s.data) and (loc.segment, b) not in img.touched:
            _grow(seg, b + 1)
            seg[b] = 0
        img.touched.add((loc.segment, b))
    put(seg, base + loc.bit, t.bits, raw)
    img.writes.append(Write(loc.segment, base + loc.bit, t.bits, raw, iid, text))


# --- Adress- und Assoziationstabelle ------------------------------------------------------

SYSTEM_B = 0x07B0


@dataclass
class Tables:
    addresses: list[str]  # Gruppenadressen in Tabellenreihenfolge (ohne eigene PA)
    associations: list[tuple[int, int]]  # (TSAP = Index in addresses ab 1, ASAP = Objektnummer)
    address_bytes: bytes  # Tabellen wie im Gerätespeicher (inkl. Anzahl-Kopf)
    association_bytes: bytes
    problems: list[str] = field(default_factory=list)


def tables(device, mask: int | None, number_of=None, active: set[int] | None = None) -> Tables:
    """Tabellen aus den Verknüpfungen des Projekts.

    System 7/BCU: Kopf 1 Byte, Eintrag 0 der Adresstabelle = eigene PA, Assoziation 1+1 Byte.
    System B: Kopf 2 Byte, keine PA, Assoziation 2+2 Byte.
    number_of(link) → Objektnummer, falls die Verknüpfung keine gespeichert hat (ETS-Import).
    active: Nummern der laut Parametern aktiven Objekte – Zuordnungen an inaktiven Objekten
    („verwaist“) kommen nicht ins Gerät, ihre GAs auch nicht in die Adresstabelle.
    """
    from xknx.telegram import GroupAddress, IndividualAddress

    problems = []
    links = []
    for link in device.links:
        number = link.number if link.number is not None else (number_of(link) if number_of else None)
        if number is None:
            problems.append(f"Objekt {link.ref}: Nummer unbekannt")
        elif active is not None and number not in active:
            problems.append(f"Objekt {number} ist inaktiv – {len(link.gas)} Zuordnung(en) nicht übernommen")
        else:
            links.append((number, link))
    gas = sorted({g for _, link in links for g in link.gas}, key=lambda a: GroupAddress(a).raw)
    index = {g: i + 1 for i, g in enumerate(gas)}
    assoc = []
    for number, link in links:
        assoc += [(rank, number, index[g]) for rank, g in enumerate(link.gas)]
    # Reihenfolge wie die ETS: erst je Objekt die sendende GA (nach Objektnummer), dann die übrigen
    # (gegen 40 Geräte geprüft: 80/80 Tabellen byte-genau gleich)
    assoc.sort(key=lambda e: (e[0], e[1]))
    assoc = [(t, a) for _, a, t in assoc]
    head = 2 if mask == SYSTEM_B else 1
    entries = [GroupAddress(g).raw for g in gas]
    if head == 1:
        if device.pa is None:
            problems.append("Gerät hat keine physikalische Adresse")
        entries = [IndividualAddress(device.pa or "0.0.0").raw] + entries
    addr = len(entries).to_bytes(head, "big") + b"".join(e.to_bytes(2, "big") for e in entries)
    if head == 1 and (len(entries) > 255 or len(assoc) > 255):
        problems.append("Tabelle zu lang für System 7/BCU (höchstens 255 Einträge)")
    ab = len(assoc).to_bytes(head, "big") + b"".join(t.to_bytes(head, "big") + a.to_bytes(head, "big") for t, a in assoc)
    return Tables(gas, assoc, addr, ab, problems)


# --- Abgleich mit ausgelesenen Geräten ------------------------------------------------------


@dataclass
class Check:
    pa: str
    param_equal: int = 0
    param_diff: list[tuple[str, int, int, int, list[str]]] = field(default_factory=list)  # (Segment, Offset, Gerät, berechnet, Parameter)
    object_table: tuple[int, int] | None = None  # Gruppenobjekttabelle (gleich, gelesen)
    object_table_diff: list[tuple[int, int, int]] = field(default_factory=list)  # (Offset, Gerät, berechnet)
    ets_equal: int = 0  # Vergleich mit dem ETS-Ladeabbild im Projekt (falls vorhanden)
    ets_diff: list[tuple[str, int, int, int, list[str]]] = field(default_factory=list)
    address_table: bool | None = None  # None = nicht lesbar
    association_table: bool | None = None
    problems: list[str] = field(default_factory=list)


def check_against_store(ap: AppParams | None, device, store, mask: int | None, number_of=None) -> Check:
    """Berechnetes Abbild gegen den zuletzt gelesenen Gerätespeicher (nur gelesene Bytes)."""
    import json

    from .decode import Raw, _objects, _table
    from .device import OT_ADDRESS_TABLE, OT_ASSOCIATION_TABLE, PID_TABLE_REFERENCE

    c = Check(device.pa or "")
    mem: dict[int, int] = {}
    for args, data in store.db.execute(
            "SELECT args, data FROM read WHERE pa=? AND service='mem' AND error IS NULL ORDER BY ts", (device.pa,)):
        a = json.loads(args)["addr"]
        for i, b in enumerate(data):
            mem[a + i] = b
    raw = Raw(store, device.pa)

    def rel_base(lsm: int) -> int | None:
        ref = raw.prop(lsm, PID_TABLE_REFERENCE)
        return int.from_bytes(ref, "big") if ref else None

    if ap is not None:
        img = param_image(ap, device.params)
        if device.extra.get("LoadedImage"):
            from .project.loaded_image import absolute, parse
            ets = absolute(parse(device.extra["LoadedImage"]), rel_base)
            for sid, buf in img.segments.items():
                seg = ap.segments[sid]
                base = seg.address if seg.absolute else rel_base(seg.lsm)
                if base is None:
                    continue
                for off in range(len(buf)):
                    if base + off in ets and img.defined(sid, off, ap):
                        if ets[base + off] == buf[off]:
                            c.ets_equal += 1
                        else:
                            owners = [w.text.strip() or "(versteckt)" for w in img.owner(sid, off)]
                            c.ets_diff.append((sid, off, ets[base + off], buf[off], owners))
        c.problems += img.problems
        needed: dict[str, set[int]] = {}
        for sid, start, n in ap.needed_ranges():
            needed.setdefault(sid, set()).update(range(start, start + n))
        for sid, buf in img.segments.items():
            seg = ap.segments[sid]
            if seg.absolute:
                base = seg.address
            else:
                ref = raw.prop(seg.lsm, PID_TABLE_REFERENCE)
                if not ref:
                    continue
                base = int.from_bytes(ref, "big")
            for off in sorted(needed.get(sid, ())):
                if off >= len(buf) or base + off not in mem or not img.defined(sid, off, ap):
                    continue
                if mem[base + off] == buf[off]:
                    c.param_equal += 1
                else:
                    owners = [f"{w.text.strip() or '(versteckt)'} [{w.instance_id.rsplit('_', 2)[-2]}]" for w in img.owner(sid, off)]
                    c.param_diff.append((sid, off, mem[base + off], buf[off], owners))

    if ap is not None and ap.co_table:
        ev = ap.evaluate_values(device.params)
        linked = set()
        for link in device.links:
            n = link.number if link.number is not None else (number_of(link) if number_of else None)
            if n is not None and link.gas:
                linked.add(n)
        ot = object_table(ap, ev, linked)
        if ot is not None:
            seg_id, off, data = ot
            base = ap.segments[seg_id].address + off
            known = [i for i in range(len(data)) if base + i in mem]
            if known:
                eq = [i for i in known if mem[base + i] == data[i]]
                c.object_table = (len(eq), len(known))
                c.object_table_diff = [(i, mem[base + i], data[i]) for i in known if mem[base + i] != data[i]]

    if mask is not None and mask not in (0x0010, 0x0011, 0x0012, 0x0013):
        active = set(ap.evaluate_values(device.params).active_cos) if ap is not None else None
        t = tables(device, mask, number_of, active)
        c.problems += t.problems
        objs = _objects(raw)
        head = 2 if mask == SYSTEM_B else 1
        a = _table(raw, objs, OT_ADDRESS_TABLE, head, 2)
        s = _table(raw, objs, OT_ASSOCIATION_TABLE, head, 2 * head)
        if a is not None:
            c.address_table = len(a).to_bytes(head, "big") + b"".join(a) == t.address_bytes
        if s is not None:
            c.association_table = len(s).to_bytes(head, "big") + b"".join(s) == t.association_bytes
    return c


# --- Gruppenobjekttabelle System 7 ------------------------------------------------------------

SIZE_CODE = {"1 Bit": 0, "2 Bit": 1, "3 Bit": 2, "4 Bit": 3, "5 Bit": 4, "6 Bit": 5, "7 Bit": 6, "1 Byte": 7,
             "2 Bytes": 8, "3 Bytes": 9, "4 Bytes": 10, "6 Bytes": 11, "8 Bytes": 12, "10 Bytes": 13, "14 Bytes": 14}
PRIORITY_CODE = {"System": 0, "High": 1, "Alert": 2, "Low": 3}


def object_table(ap: AppParams, ev, linked: set[int]) -> tuple[str, int, bytes] | None:
    """Gruppenobjekttabelle (System 7) aus der Vorlage im Code-Segment → (Segment, Offset, Bytes).

    Aufbau: Anzahl (1 Byte), Zeiger RAM-Flags (2), je Objekt Wertzeiger (2), Konfiguration (1),
    Typ (1). Regeln, an 36 Geräten der Testanlage geprüft (`knxlens image`):
    - Anzahl = höchste Objektnummer der Applikation + 1 (höchstens 255), Zeiger aus der Vorlage
    - Wertzeiger immer aus der Vorlage
    - aktives Objekt: Typ = Größe der aktiven Variante; Konfiguration = Flags der Variante
      (Aktualisieren 0x80, Übertragen 0x40, Schreiben 0x10, Lesen 0x08), Bit 0x20 aus der Vorlage,
      Priorität (Bit 0–1) aus der Variante, Kommunikation (0x04) nur wenn mit einer GA verknüpft
    - inaktives Objekt: Vorlage ohne Kommunikations-Bit
    Bekannte Ausnahme: Theben 1.1.152 lässt das Kommunikations-Bit unverknüpfter Objekte stehen.
    """
    if not ap.co_table:
        return None
    seg_id, off = ap.co_table
    seg = ap.segments.get(seg_id)
    if seg is None or len(seg.data) < off + 3:
        return None
    tpl = seg.data[off:]
    numbers = set(ap.app.ref_number.values()) | {n for n, base in ap.co_numbers.values() if not base}
    count = min(255, max(numbers) + 1) if numbers else tpl[0]
    out = bytearray([count]) + bytearray(tpl[1:3])
    for i in range(count):
        entry = bytearray(tpl[3 + 4 * i:7 + 4 * i].ljust(4, b"\x00"))
        key = ev.active_cos.get(i)
        v = ap.app.variant_by_ref.get(key) if key else None
        if v is not None:
            f = v.flags
            entry[2] = ((entry[2] & 0x20) | (0x80 if f.get("update") else 0) | (0x40 if f.get("transmit") else 0)
                        | (0x10 if f.get("write") else 0) | (0x08 if f.get("read") else 0)
                        | (0x04 if i in linked else 0) | PRIORITY_CODE.get(v.priority, 3))
            entry[3] = SIZE_CODE.get(v.size or "", entry[3])
        else:
            entry[2] &= ~0x04
        out += entry
    return seg_id, off, bytes(out)
