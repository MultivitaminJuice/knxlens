"""Parameter aus dem Gerätespeicher dekodieren (offline, nach dem Lesen).

Die Produktdaten beschreiben je Parameter Typ, Standardwert und Ort:
- Memory: Codesegment + Offset + BitOffset (MSB-zuerst); in Modulen zusätzlich ein
  Basisargument (BaseOffset). Absolute Segmente liegen fest im Speicher, relative
  dort, wohin das Gerät sie geladen hat (Tabellenzeiger des LSM-Objekts).
- Property: Objekt/Property/Offset.
- ohne Ort: nur in der ETS (Kanalnamen, Anzeigeschalter) – nicht rekonstruierbar.

Sichtbarkeit: Der Dynamic-Baum (choose/when) wird mit den gelesenen Werten
ausgewertet, genau wie die ETS die Parameterseiten aufbaut. Hängt eine Bedingung
an einem Parameter ohne Speicherort, gilt sein Standardwert und der Zweig ist
als unsicher markiert. Nebenbei fallen die aktiven Objektvarianten ab.
"""

from __future__ import annotations

import re
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as ET

from .catalog import MAX_MEMBER, Application, _children, _local, _translations

MemFn = Callable[[int, int], bytes | None]
PropFn = Callable[[int, int], bytes | None]


@dataclass
class Segment:
    id: str
    absolute: bool
    address: int  # bei relativ: 0
    lsm: int | None
    size: int
    data: bytes


@dataclass
class PType:
    kind: str  # number, enum, float, text, time, color, none
    bits: int
    signed: bool = False
    encoding: str = ""
    enums: dict[int, str] = field(default_factory=dict)  # in Anzeigereihenfolge
    unit: str = ""
    minv: float | None = None
    maxv: float | None = None
    ui: str = ""  # UIHint, z. B. CheckBox, Duration_hhmmss


@dataclass
class Location:
    segment: str | None  # None → Property
    bit: int  # Bitposition ab Segment- bzw. Property-Anfang (MSB zuerst)
    base_arg: str | None = None
    obj: int | None = None
    pid: int | None = None


@dataclass
class Param:
    id: str
    text: str
    ptype: PType
    default: str
    access: str
    loc: Location | None
    suffix: str = ""


@dataclass
class PRef:
    id: str
    param: Param
    text: str
    default: str
    access: str


@dataclass
class Context:
    """Modulinstanz (oder Applikationsebene)."""

    instance: str | None = None  # z. B. ..._MD-2_M-1
    module_def: str | None = None
    numeric: dict[str, int] = field(default_factory=dict)
    text: dict[str, str] = field(default_factory=dict)  # Argumentname → Wert


@dataclass
class Value:
    ref_id: str
    instance_id: str  # Schlüssel wie im ETS-Projekt (ParameterInstanceRef)
    path: tuple[str, ...]
    text: str
    raw: int | bytes | None
    value: object  # dekodiert (Zahl, Text, Aufzählungswert)
    display: str
    default: str
    from_device: bool
    uncertain: bool
    internal: bool = False  # versteckter Hilfsparameter (kein Text / nicht editierbar)
    untouched: bool = False  # Speicher = Standardbild der Produktdatei, Wert ≠ Parameter-Standard
    from_project: bool = False  # Wert aus dem Projekt (Editor-Modus) statt aus dem Gerät

    @property
    def param_key(self) -> str:
        """Instanz-Id ohne Ref-Suffix: verschiedene Refs desselben Parameters teilen den Speicher."""
        return re.sub(r"_R-\d+$", "", self.instance_id)

    @property
    def is_default(self) -> bool:
        return _same(self.value, self.default)


def _same(value: object, text: str) -> bool:
    if value is None:
        return False
    if isinstance(value, float):
        try:
            return abs(value - float(text)) < 1e-3 * max(1.0, abs(value))
        except ValueError:
            return False
    return str(value).strip().lower() == str(text).strip().lower()


class AppParams:
    """Parameterteil einer Applikation, aus der Produktdatei nachgeladen."""

    def __init__(self, app: Application) -> None:
        self.app = app
        xml = _read_member(app.source_path, app.member)
        root = ET.fromstring(xml)
        self.tr = _translations(root)
        self.prog = next(_children(root, "ApplicationProgram"))
        self.segments = self._segments()
        self.types = self._types()
        self.params, self.refs = self._params()
        self.module_defs = {md.get("Id"): md for md in _children(self.prog, "ModuleDef")}
        self.arg_names = {
            a.get("Id"): a.get("Name") for md in self.module_defs.values() for a in _children(md, "Argument")
        }
        self.dynamic = next((d for d in self.prog if _local(d.tag) == "Dynamic"), None)
        self.co_refs = self._co_refs()

    # --- Aufbau ---------------------------------------------------------------

    def _t(self, el_id: str, attrs, name: str) -> str:
        return self.tr.get(el_id, {}).get(name) or attrs.get(name, "")

    def _segments(self) -> dict[str, Segment]:
        out = {}
        for seg in _children(self.prog, "AbsoluteSegment"):
            out[seg.get("Id")] = Segment(seg.get("Id"), True, int(seg.get("Address", "0")), None,
                                         int(seg.get("Size", "0")), _data(seg))
        for seg in _children(self.prog, "RelativeSegment"):
            out[seg.get("Id")] = Segment(seg.get("Id"), False, 0, int(seg.get("LoadStateMachine", "0")),
                                         int(seg.get("Size", "0")), _data(seg))
        return out

    def _types(self) -> dict[str, PType]:
        out = {}
        for pt in _children(self.prog, "ParameterType"):
            t = next(iter(pt), None)
            if t is None:
                continue
            kind = _local(t.tag)
            bits = int(t.get("SizeInBit", "0"))
            ui = t.get("UIHint", "")
            lo = float(t.get("minInclusive")) if t.get("minInclusive") is not None else None
            hi = float(t.get("maxInclusive")) if t.get("maxInclusive") is not None else None
            if kind == "TypeRestriction":
                items = []
                for e in t:
                    if _local(e.tag) == "Enumeration" and e.get("Value") is not None:
                        order = int(e.get("DisplayOrder", len(items)))
                        items.append((order, int(e.get("Value")), self._t(e.get("Id", ""), e.attrib, "Text")))
                enums = {v: text for _, v, text in sorted(items, key=lambda x: x[0])}
                out[pt.get("Id")] = PType("enum", bits, enums=enums, ui=ui)
            elif kind == "TypeNumber":
                out[pt.get("Id")] = PType("number", bits, signed=t.get("Type") == "signedInt", minv=lo, maxv=hi, ui=ui)
            elif kind == "TypeFloat":
                enc = t.get("Encoding", "")
                out[pt.get("Id")] = PType("float", 16 if enc == "DPT 9" else 32, encoding=enc, minv=lo, maxv=hi)
            elif kind == "TypeText":
                out[pt.get("Id")] = PType("text", bits)
            elif kind == "TypeTime":
                out[pt.get("Id")] = PType("time", bits, signed=t.get("Type") == "signedInt", unit=t.get("Unit", ""),
                                          minv=lo, maxv=hi, ui=ui)
            elif kind == "TypeColor":
                out[pt.get("Id")] = PType("color", 24)
            else:
                out[pt.get("Id")] = PType("none", 0)
        return out

    def _params(self) -> tuple[dict[str, Param], dict[str, PRef]]:
        params: dict[str, Param] = {}

        def location(el, extra_bit: int = 0) -> Location | None:
            for c in el:
                tag = _local(c.tag)
                if tag == "Memory":
                    bit = int(c.get("Offset", "0")) * 8 + int(c.get("BitOffset", "0")) + extra_bit
                    return Location(c.get("CodeSegment"), bit, c.get("BaseOffset"))
                if tag == "Property":
                    bit = int(c.get("Offset", "0")) * 8 + int(c.get("BitOffset", "0")) + extra_bit
                    return Location(None, bit, None, int(c.get("ObjectIndex", "0")), int(c.get("PropertyId", "0")))
            return None

        def add(p, loc: Location | None) -> None:
            ptype = self.types.get(p.get("ParameterType"), PType("none", 0))
            params[p.get("Id")] = Param(
                id=p.get("Id"), text=self._t(p.get("Id"), p.attrib, "Text"), ptype=ptype,
                default=p.get("Value", ""), access=p.get("Access", "ReadWrite"), loc=loc,
                suffix=self._t(p.get("Id"), p.attrib, "SuffixText"),
            )

        for el in _children(self.prog, "Parameter"):
            if el.get("Id") not in params:
                add(el, location(el))
        for union in _children(self.prog, "Union"):
            base = location(union)
            for p in union:
                if _local(p.tag) != "Parameter":
                    continue
                loc = None
                if base is not None:
                    extra = int(p.get("Offset", "0")) * 8 + int(p.get("BitOffset", "0"))
                    loc = Location(base.segment, base.bit + extra, base.base_arg, base.obj, base.pid)
                add(p, loc)

        refs = {}
        for r in _children(self.prog, "ParameterRef"):
            p = params.get(r.get("RefId"))
            if p is None:
                continue
            refs[r.get("Id")] = PRef(
                id=r.get("Id"), param=p, text=self._t(r.get("Id"), r.attrib, "Text") or p.text,
                default=r.get("Value", p.default), access=r.get("Access", p.access),
            )
        return params, refs

    def _co_refs(self) -> dict[str, str]:
        """ComObjectRef-Id → ComObject-Id; dazu ComObject-Id → (Nummer, Basisargument)."""
        self.co_numbers = {
            co.get("Id"): (int(co.get("Number", "0")), co.get("BaseNumber"))
            for co in _children(self.prog, "ComObject")
        }
        return {r.get("Id"): r.get("RefId") for r in _children(self.prog, "ComObjectRef")}

    # --- Auswertung -------------------------------------------------------------

    def evaluate(self, mem: MemFn, prop: PropFn, rel_base: Callable[[int], int | None],
                 linked: set[int] | None = None) -> Evaluation:
        """linked: Objektnummern mit GA-Verknüpfung am Bus – nur aktive Objekte lassen sich verknüpfen."""
        ev = Evaluation(self, mem, prop, rel_base, linked or set())
        if self.dynamic is not None:
            ev.walk(self.dynamic, Context(), (), False)
        return ev

    def evaluate_values(self, values: dict[str, str]) -> Evaluation:
        """Auswertung aus Projektwerten (Editor): values = {ParameterInstanceRef-Id: Wert}.

        Fehlende Parameter haben ihren Standardwert. Kein Speicherzugriff, keine Ableitung.
        """
        keyed = {re.sub(r"_R-\d+$", "", k): v for k, v in values.items()}
        ev = Evaluation(self, lambda a, n: None, lambda o, p: None, lambda l: None, set(), keyed)
        if self.dynamic is not None:
            ev.walk(self.dynamic, Context(), (), False)
        return ev

    def module_args(self, instance_id: str) -> list[tuple[str, str]] | None:
        """Argumente einer Modulinstanz aus den Produktdaten."""
        for m in _children(self.prog, "Module"):
            if m.get("Id") == instance_id:
                return [(a.get("RefId"), a.get("Value", "")) for a in m if _local(a.tag) in ("NumericArg", "TextArg")]
        return None

    def needed_ranges(self) -> list[tuple[str, int, int]]:
        """(Segment-Id, Start, Länge) aller Parameterbytes je Segment – unabhängig von Sichtbarkeit.

        Modulparameter: über alle Modulinstanzen (Basisargument) aufgespannt.
        """
        spans: dict[str, list[int]] = {}
        bases: dict[str, set[int]] = {}
        for inst in _children(self.prog, "Module"):
            for a in inst:
                if _local(a.tag) == "NumericArg":
                    bases.setdefault(a.get("RefId"), set()).add(int(a.get("Value", "0")))
        for p in self.params.values():
            loc = p.loc
            if loc is None or loc.segment is None or p.ptype.kind == "none":
                continue
            offsets = bases.get(loc.base_arg, {0}) if loc.base_arg else {0}
            for off in offsets:
                start = off + loc.bit // 8
                end = off + (loc.bit + max(p.ptype.bits, 1) - 1) // 8 + 1
                lo_hi = spans.setdefault(loc.segment, [start, end])
                lo_hi[0], lo_hi[1] = min(lo_hi[0], start), max(lo_hi[1], end)
        return [(seg, lo, hi - lo) for seg, (lo, hi) in spans.items()]


class Evaluation:
    def __init__(self, ap: AppParams, mem: MemFn, prop: PropFn, rel_base, linked: set[int] | None = None,
                 values: dict[str, str] | None = None) -> None:
        self.ap = ap
        self.linked = linked or set()
        self.project_values = values  # Editor-Modus: Werte aus dem Projekt statt aus dem Speicher
        self.mem = mem
        self.prop = prop
        self.rel_base = rel_base
        self.values: list[Value] = []
        self.active_cos: dict[int, str] = {}  # Objektnummer → aktive Variante ("Instanz|Ref-Id" bzw. "Ref-Id")
        self.unreadable: set[str] = set()  # Segmente ohne Daten
        self._cache: dict[tuple[str, str | None], Value] = {}
        self._seen: set[str] = set()
        self.inferred: list[str] = []  # Bedingungen, deren ETS-only-Wert aus dem Speicher erschlossen wurde
        self.inferred_keys: list[tuple[str, str, str]] = []  # (Parameter-Schlüssel, gewählter test, Standard)
        self.modules: list[tuple[str, list[tuple[str, str]]]] = []  # aktive Modulinstanzen: (Id, [(Arg-RefId, Wert)])

    def address(self, loc: Location, ctx: Context) -> tuple[int, int] | None:
        """(Byteadresse, Bitrest) im Gerät."""
        seg = self.ap.segments.get(loc.segment)
        if seg is None:
            return None
        base = seg.address if seg.absolute else self.rel_base(seg.lsm)
        if base is None:
            self.unreadable.add(seg.id)
            return None
        off = ctx.numeric.get(loc.base_arg, 0) if loc.base_arg else 0
        return base + off + loc.bit // 8, loc.bit % 8

    def read_raw(self, p: Param, ctx: Context) -> int | bytes | None:
        loc, t = p.loc, p.ptype
        if loc is None or t.kind == "none" or t.bits == 0:
            return None
        nbytes = (loc.bit % 8 + t.bits + 7) // 8
        if loc.segment is None:
            data = self.prop(loc.obj, loc.pid)
            if data is None:
                return None
            start = loc.bit // 8
            chunk, bit = data[start : start + nbytes], loc.bit % 8
            if len(chunk) < nbytes:
                return None
        else:
            a = self.address(loc, ctx)
            if a is None:
                return None
            addr, bit = a
            chunk = self.mem(addr, nbytes)
            if chunk is None:
                self.unreadable.add(loc.segment)
                return None
        if t.kind in ("text", "color"):
            return chunk[: t.bits // 8]
        value = int.from_bytes(chunk, "big")
        value >>= nbytes * 8 - bit - t.bits
        return value & ((1 << t.bits) - 1)

    def _matches_image(self, p: Param, ctx: Context) -> bool:
        """Liegen im Gerät noch die Bytes des Standardbilds? Dann hat die ETS hier nie geschrieben."""
        loc = p.loc
        if loc is None or loc.segment is None:
            return False
        seg = self.ap.segments.get(loc.segment)
        a = self.address(loc, ctx)
        if seg is None or a is None:
            return False
        off = a[0] - (seg.address if seg.absolute else self.rel_base(seg.lsm) or 0)
        n = max(1, (loc.bit % 8 + p.ptype.bits + 7) // 8)
        image = seg.data[off : off + n]
        return len(image) == n and self.mem(a[0], n) == image

    def resolve(self, ref_id: str, ctx: Context, path: tuple[str, ...] = (), uncertain: bool = False) -> Value | None:
        key = (ref_id, ctx.instance)
        if key in self._cache:
            return self._cache[key]
        ref = self.ap.refs.get(ref_id)
        if ref is None:
            return None
        instance_id = ref_id
        if ctx.instance and ctx.module_def and ref_id.startswith(ctx.module_def + "_"):
            instance_id = f"{ctx.instance}_MI-1_{ref_id[len(ctx.module_def) + 1:]}"
        from_project = False
        if self.project_values is not None:
            raw = None
            stored = self.project_values.get(re.sub(r"_R-\d+$", "", instance_id))
            if stored is not None:
                value, display = parse_value(ref, stored)
                from_project = True
            else:
                value, display = _default(ref)
            from_device = False
        else:
            raw = self.read_raw(ref.param, ctx)
            value, display = decode_value(ref.param.ptype, raw, ref.param.suffix)
            from_device = raw is not None
            if not from_device:
                value, display = _default(ref)
        internal = not ref.text.strip() or ref.access in ("None", "Read")
        untouched = from_device and not _same(value, ref.default) and self._matches_image(ref.param, ctx)
        editor = self.project_values is not None
        v = Value(ref_id, instance_id, path, _fill(ref.text, ctx.text), raw, value, display, ref.default,
                  from_device, (not editor) and (uncertain or not from_device or untouched), internal, untouched,
                  from_project)
        self._cache[key] = v
        return v

    def walk(self, elem, ctx: Context, path: tuple[str, ...], uncertain: bool) -> None:
        for child in elem:
            tag = _local(child.tag)
            if tag in ("ParameterBlock", "Channel", "ChannelIndependentBlock"):
                label = _fill(self.ap._t(child.get("Id", ""), child.attrib, "Text"), ctx.text)
                self.walk(child, ctx, path + ((label,) if label else ()), uncertain)
            elif tag == "ParameterRefRef":
                v = self.resolve(child.get("RefId"), ctx, path, uncertain)
                if v is not None and v.param_key not in self._seen:
                    self._seen.add(v.param_key)
                    self.values.append(v)
            elif tag == "ComObjectRefRef":
                co_id = self.ap.co_refs.get(child.get("RefId"))
                number = self._co_number(co_id, ctx)
                if number is not None:
                    ref = child.get("RefId")
                    self.active_cos[number] = f"{ctx.instance}|{ref}" if ctx.instance else ref
            elif tag == "choose":
                v = self.resolve(child.get("ParamRefId"), ctx, path, uncertain)
                matched = False
                whens = [w for w in child if _local(w.tag) == "when"]
                if v is not None and not v.from_device and self.project_values is None:
                    best = self._infer_branch(whens, ctx)
                    if best is not None:
                        self.inferred.append(f"{v.text or v.ref_id} → Zweig {best.get('test') or 'default'}")
                        self.inferred_keys.append((v.param_key, best.get("test") or "", v.default))
                        self.walk(best, ctx, path, True)
                        continue
                for w in whens:
                    if w.get("default") != "true" and v is not None and _test(w.get("test", ""), v.value):
                        matched = True
                        self.walk(w, ctx, path, uncertain or (v is not None and v.uncertain))
                if not matched:
                    for w in whens:
                        if w.get("default") == "true":
                            self.walk(w, ctx, path, uncertain or v is None or v.uncertain)
            elif tag == "Module":
                md_id = child.get("RefId")
                md = self.ap.module_defs.get(md_id)
                if md is None:
                    continue
                sub = Context(
                    instance=child.get("Id"), module_def=md_id,
                    numeric={a.get("RefId"): int(a.get("Value", "0")) for a in child if _local(a.tag) == "NumericArg"},
                    text={self.ap.arg_names.get(a.get("RefId"), ""): a.get("Value", "")
                          for a in child if _local(a.tag) == "TextArg"},
                )
                args = [(a.get("RefId"), a.get("Value", "")) for a in child if _local(a.tag) in ("NumericArg", "TextArg")]
                if child.get("Id") not in {m[0] for m in self.modules}:
                    self.modules.append((child.get("Id"), args))
                dyn = next((d for d in md if _local(d.tag) == "Dynamic"), None)
                if dyn is not None:
                    self.walk(dyn, sub, path, uncertain)
            elif tag in ("Assign", "Rename", "ParameterSeparator", "ParameterCalculations", "ParameterCalculation"):
                continue
            else:
                self.walk(child, ctx, path, uncertain)

    def _infer_branch(self, whens, ctx: Context):
        """Zweig wählen, dessen versteckte Parameter zum Speicher passen (für ETS-only-Bedingungen).

        Versteckte Refs zeigen in jedem Zweig einen festen Standardwert, den die ETS
        mit herunterlädt. Widerspricht der Speicher, ist der Zweig nicht aktiv.
        """
        # Beleg aus Verknüpfungen nur, wenn das verknüpfte Objekt allein in diesem Zweig vorkommt
        numbers = []
        for w in whens:
            found = set()
            for el in _co_refrefs(w):
                n = self._co_number(self.ap.co_refs.get(el.get("RefId")), ctx)
                if n is not None and n in self.linked:
                    found.add(n)
            numbers.append(found)
        scores = []
        for w, found in zip(whens, numbers):
            others = set().union(*(f for f in numbers if f is not found)) if len(numbers) > 1 else set()
            good = 2 * len(found - others)
            bad = 0
            for el in _direct_refs(w):
                ref = self.ap.refs.get(el.get("RefId"))
                # Beleg nur durch Werte, die die ETS fest vorgibt (nicht editierbar)
                if ref is None or ref.access not in ("None", "Read"):
                    continue
                raw = self.read_raw(ref.param, ctx)
                if raw is None:
                    continue
                value, _ = decode_value(ref.param.ptype, raw)
                if _same(value, ref.default):
                    good += 1
                else:
                    bad += 1
            scores.append((bad, -good, w))
        # nur Zweige ohne Widerspruch UND mit mindestens einem positiven Beleg
        fitting = [s for s in scores if s[0] == 0 and s[1] < 0]
        if len(scores) > 1 and len(fitting) == 1:
            return fitting[0][2]
        if len(fitting) > 1:
            best = min(s[1] for s in fitting)
            top = [s for s in fitting if s[1] == best]
            if len(top) == 1:
                return top[0][2]
        return None

    def _co_number(self, co_id: str | None, ctx: Context) -> int | None:
        if co_id not in self.ap.co_numbers:
            return None
        n, base = self.ap.co_numbers[co_id]
        return n + ctx.numeric.get(base, 0) if base else n


def _co_refrefs(elem):
    """ComObjectRefRefs eines Zweigs, auch in verschachtelten Blöcken (nicht in Modulen)."""
    for c in elem:
        tag = _local(c.tag)
        if tag == "ComObjectRefRef":
            yield c
        elif tag != "Module":
            yield from _co_refrefs(c)


def _direct_refs(elem):
    """ParameterRefRefs eines Zweigs ohne verschachtelte choose-Blöcke."""
    for c in elem:
        tag = _local(c.tag)
        if tag == "ParameterRefRef":
            yield c
        elif tag not in ("choose", "Module"):
            yield from _direct_refs(c)


def _test(test: str, value: object) -> bool:
    """when-Bedingung: '1 2 3', '!=0', '<5', '>=2' …"""
    test = test.strip()
    try:
        num = float(value) if not isinstance(value, (bytes, str)) else float(str(value))
    except (TypeError, ValueError):
        return test == str(value)
    for op in ("!=", "<=", ">=", "<", ">", "="):
        if test.startswith(op):
            ref = float(test[len(op):])
            return {"!=": num != ref, "<=": num <= ref, ">=": num >= ref, "<": num < ref,
                    ">": num > ref, "=": num == ref}[op]
    return any(num == float(t) for t in test.split() if _is_number(t))


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def decode_value(t: PType, raw, suffix: str = "") -> tuple[object, str]:
    if raw is None:
        return None, ""
    unit = f" {suffix}" if suffix else ""
    if t.kind == "enum":
        return raw, t.enums.get(raw, f"? ({raw})")
    if t.kind == "number":
        if t.signed and raw >= 1 << (t.bits - 1):
            raw -= 1 << t.bits
        return raw, f"{raw}{unit}"
    if t.kind == "time":
        if t.unit == "PackedDaysHoursMinutesAndSeconds":
            # Bytes: Sekunden, Minuten, Stunden[, Tage] – Wert in Sekunden (wie im ETS-Projekt)
            b = raw.to_bytes(t.bits // 8, "big")
            secs = b[0] + 60 * b[1] + 3600 * b[2] + (86400 * b[3] if len(b) > 3 else 0)
            h, rest = divmod(secs, 3600)
            return secs, f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}"
        return raw, f"{raw} {t.unit}".strip()
    if t.kind == "float":
        if t.encoding == "DPT 9":
            sign = -1 if raw & 0x8000 else 1
            exp = (raw >> 11) & 0x0F
            mant = raw & 0x07FF
            if sign < 0:
                mant -= 2048
            value = round(0.01 * mant * (1 << exp), 2)
        else:
            value = round(struct.unpack(">f", raw.to_bytes(4, "big"))[0], 4)
        return value, f"{value}{unit}"
    if t.kind == "text":
        # bis zum Nullbyte, Leerzeichen bleiben erhalten (gehören zum Wert)
        s = raw.split(b"\x00", 1)[0].decode("latin-1")
        return s, s
    if t.kind == "color":
        return raw.hex(), "#" + raw.hex()
    return raw, str(raw)


def encode_value(t: PType, value: object) -> int | bytes:
    """Umkehrung von decode_value: Wert → Rohwert wie read_raw ihn liefert (int bzw. bytes)."""
    if t.kind in ("enum", "number"):
        v = int(value)
        return v & ((1 << t.bits) - 1)  # vorzeichenbehaftet: Zweierkomplement
    if t.kind == "time":
        v = int(value)
        if t.unit == "PackedDaysHoursMinutesAndSeconds":
            days, rest = divmod(v, 86400)
            h, rest = divmod(rest, 3600)
            m, sec = divmod(rest, 60)
            parts = [sec, m, h] + ([days] if t.bits > 24 else [])
            return int.from_bytes(bytes(parts), "big")
        return v & ((1 << t.bits) - 1)
    if t.kind == "float":
        v = float(value)
        if t.encoding == "DPT 9":
            return _encode_dpt9(v)
        return int.from_bytes(struct.pack(">f", v), "big")
    if t.kind == "text":
        n = t.bits // 8
        data = str(value).encode("latin-1", errors="replace")[:n]
        return data + b"\x00" * (n - len(data))
    if t.kind == "color":
        return bytes.fromhex(str(value).lstrip("#"))
    raise ValueError(f"Typ {t.kind} nicht kodierbar")


def _encode_dpt9(v: float) -> int:
    """2-Byte-Gleitkomma (KNX DPT 9): M·0,01·2^E, Mantisse 12 Bit Zweierkomplement."""
    mant = round(v * 100)
    exp = 0
    while not -2048 <= mant <= 2047:
        exp += 1
        mant = round(v * 100 / (1 << exp))
        if exp > 15:
            raise ValueError("Wert außerhalb DPT 9")
    sign = 0x8000 if mant < 0 else 0
    return sign | (exp << 11) | (mant & 0x07FF)


def validate(ref: PRef, text: str) -> tuple[object, str]:
    """Eingabe prüfen → (Wert, Text für das Projekt). ValueError mit verständlicher Meldung."""
    t = ref.param.ptype
    text = str(text).strip()
    if ref.access in ("None", "Read"):
        raise ValueError("Parameter ist nicht änderbar")
    if t.kind == "enum":
        v = int(float(text))
        if v not in t.enums:
            raise ValueError(f"Kein gültiger Wert: {text}")
        return v, str(v)
    if t.kind in ("number", "time"):
        try:
            v = int(float(text))
        except ValueError:
            raise ValueError("Ganze Zahl erwartet") from None
        lo = t.minv if t.minv is not None else (-(1 << (t.bits - 1)) if t.signed else 0)
        hi = t.maxv if t.maxv is not None else ((1 << (t.bits - 1)) - 1 if t.signed else (1 << t.bits) - 1)
        if not lo <= v <= hi:
            raise ValueError(f"Erlaubt: {lo:g} … {hi:g}")
        return v, str(v)
    if t.kind == "float":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            raise ValueError("Zahl erwartet") from None
        if (t.minv is not None and v < t.minv) or (t.maxv is not None and v > t.maxv):
            raise ValueError(f"Erlaubt: {t.minv:g} … {t.maxv:g}")
        return v, repr(v)
    if t.kind == "text":
        if len(text.encode("latin-1", errors="replace")) > t.bits // 8:
            raise ValueError(f"Höchstens {t.bits // 8} Zeichen")
        return text, text
    if t.kind == "color":
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
            raise ValueError("Farbe als #RRGGBB")
        return text.lstrip("#").upper(), text.lstrip("#").upper()
    raise ValueError("Parametertyp nicht bearbeitbar")


def parse_value(ref: PRef, text: str) -> tuple[object, str]:
    """Wert aus dem Projekt (Text wie in ParameterInstanceRef) → (Wert, Anzeige)."""
    t = ref.param.ptype
    unit = f" {ref.param.suffix}" if ref.param.suffix else ""
    try:
        if t.kind == "enum":
            v = int(float(text))
            return v, t.enums.get(v, f"? ({v})")
        if t.kind == "number":
            v = int(float(text))
            return v, f"{v}{unit}"
        if t.kind == "time":
            v = int(float(text))
            if t.unit == "PackedDaysHoursMinutesAndSeconds":
                h, rest = divmod(v, 3600)
                return v, f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}"
            return v, f"{v} {t.unit}".strip()
        if t.kind == "float":
            v = float(text)
            return v, f"{round(v, 4)}{unit}"
    except ValueError:
        pass
    return text, text


def _default(ref: PRef) -> tuple[object, str]:
    t = ref.param.ptype
    d = ref.default
    if t.kind == "enum" and _is_number(d):
        return int(float(d)), t.enums.get(int(float(d)), d)
    if t.kind in ("number", "time") and _is_number(d):
        return int(float(d)), d
    if t.kind == "float" and _is_number(d):
        return float(d), d
    return d, d


def _fill(text: str, args: dict[str, str]) -> str:
    for name, value in args.items():
        text = text.replace("{{" + name + "}}", value)
    return re.sub(r"\s*\{\{[^}]*\}\}\s*", " ", text).strip(" :")


def _data(seg) -> bytes:
    import base64

    d = next((c for c in seg if _local(c.tag) == "Data"), None)
    return base64.b64decode(d.text) if d is not None and d.text else b""


def _read_member(source_path: str, member: str) -> bytes:
    path = Path(source_path)
    if path.is_dir():
        return (path / member).read_bytes()
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(member)
        if info.file_size > MAX_MEMBER:
            raise ValueError(f"{member} zu groß")
        return z.read(info)



# --- Lesen (Bus) ----------------------------------------------------------------

PID_TABLE_REFERENCE = 7
MAX_PARAM_BYTES = 8192


async def read(reader, ap: AppParams) -> dict:
    """Parameterbereiche eines Geräts lesen (nur MemoryRead/PropertyValueRead über den Guard)."""
    summary: dict = {"segments": {}, "properties": []}
    total = 0
    for seg_id, start, length in ap.needed_ranges():
        seg = ap.segments[seg_id]
        if seg.absolute:
            base = seg.address
        else:
            ref = await reader.prop(seg.lsm, PID_TABLE_REFERENCE)
            base = int.from_bytes(ref, "big") if ref else None
        if base is None:
            summary["segments"][seg_id] = "Basisadresse unbekannt"
            continue
        if total + length > MAX_PARAM_BYTES:
            summary["segments"][seg_id] = f"übersprungen ({length} Byte, Grenze erreicht)"
            continue
        data = await reader.mem(base + start, length)
        total += length
        summary["segments"][seg_id] = f"{base + start:#06x}: {len(data or b'')}/{length} Byte"
    for obj, pid in sorted({(p.loc.obj, p.loc.pid) for p in ap.params.values() if p.loc and p.loc.segment is None}):
        data = await reader.prop(obj, pid)
        summary["properties"].append(f"{obj}/{pid}: {'ok' if data else 'nicht lesbar'}")
    return summary


def evaluate_from_store(ap: AppParams, raw, linked: set[int] | None = None) -> Evaluation:
    """Auswertung mit den gespeicherten Rohdaten (decode.Raw); linked = verknüpfte Objektnummern."""

    def rel_base(lsm: int) -> int | None:
        ref = raw.prop(lsm, PID_TABLE_REFERENCE)
        return int.from_bytes(ref, "big") if ref else None

    return ap.evaluate(raw.mem, lambda obj, pid: raw.prop(obj, pid), rel_base, linked)
