"""Ladeablauf eines Downloads als Plan – was die ETS in welcher Reihenfolge tun würde.

Nur Beschreibung, kein Buszugriff. Quelle der Schritte:
- „ProductProcedure“: die LoadProcedure der Applikation (System 7, z. B. MDT),
- sonst die Prozedur der Maskenversion aus knx_master.xml, in die die Applikation ihre
  Bausteine über LdCtrlMerge/MergeId einsetzt (System B).
Zu Schreibschritten hängt der Plan die Bytes aus dem berechneten Abbild an (image.py).
Ausgeführt wird der Plan erst in E4 – mit eigenem Schreibschutz, Vorschau und Rücklesen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from defusedxml import ElementTree as ET

from .catalog import _local

LSM_NAMES = {1: "Adresstabelle", 2: "Assoziationstabelle", 3: "Applikation", 4: "Applikation (Parameter)",
             5: "Applikation 2"}
KIND_APPLIES = {"all": ("full",), "par": ("par",), "grp": ("grp",)}


@dataclass
class Step:
    op: str  # Connect, Unload, Load, AbsSegment, RelSegment, WriteMem, WriteRelMem, WriteProp, …
    text: str  # verständliche Beschreibung
    attrs: dict = field(default_factory=dict)
    address: int | None = None  # Schreibschritte: Zieladresse (absolut) bzw. Offset (relativ)
    data: bytes | None = None  # Schreibschritte: die zu schreibenden Bytes
    writes: bool = False  # verändert das Gerät?
    chunks: list[tuple[int, bytes]] = field(default_factory=list)  # (Offset ab address, Bytes) – was wirklich geschrieben wird

    @property
    def size(self) -> int:
        return sum(len(b) for _, b in self.chunks) if self.chunks else len(self.data or b"")


def runs(data: bytes, defined) -> list[tuple[int, bytes]]:
    """Zusammenhängende Blöcke der Bytes, für die defined(i) gilt."""
    out, start = [], None
    for i in range(len(data) + 1):
        ok = i < len(data) and defined(i)
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            out.append((start, data[start:i]))
            start = None
    return out


def _steps_of(el, applies: tuple[str, ...]):
    for c in el.iter():
        tag = _local(c.tag)
        if not tag.startswith("LdCtrl"):
            continue
        a = c.get("AppliesTo")
        if a and not set(a.split(",")) & set(applies):
            continue
        yield tag[6:], dict(c.attrib)


def raw_steps(app_xml: bytes, master_xml: bytes | None, mask: str, kind: str = "all") -> list[tuple[str, dict]]:
    """Schritte (Name, Attribute) für einen Download der Art kind (all, par, grp)."""
    root = ET.fromstring(app_xml)
    prog = next(e for e in root.iter() if _local(e.tag) == "ApplicationProgram")
    applies = KIND_APPLIES.get(kind, ("full",))
    procs = [e for e in prog.iter() if _local(e.tag) == "LoadProcedure"]
    if prog.get("LoadProcedureStyle") == "ProductProcedure" or not master_xml:
        out = []
        for p in procs:
            out += list(_steps_of(p, applies))
        return out
    merges: dict[str, list] = {}
    for p in procs:
        if p.get("MergeId"):
            merges.setdefault(p.get("MergeId"), []).extend(_steps_of(p, applies))
    m = re.search(rf'<MaskVersion Id="{re.escape(mask)}".*?</MaskVersion>'.encode(), master_xml, re.S)
    if not m:
        return []
    mv = ET.fromstring(m[0])
    sub = {"all": "all", "par": "par", "grp": "grp"}.get(kind, "all")
    proc = next((p for p in mv.iter() if _local(p.tag) == "Procedure" and p.get("ProcedureType") == "Load"
                 and p.get("ProcedureSubType") == sub), None)
    if proc is None:
        return []
    out = []
    for name, attrs in _steps_of(proc, ("full", "par", "grp")):
        if name == "Merge":
            out += merges.get(attrs.get("MergeId"), [])
        else:
            out.append((name, attrs))
    return out


def build(steps: list[tuple[str, dict]], segments: dict[int, bytes], tables: dict[int, bytes],
          rel_images: dict[int, bytes], masks: dict | None = None, props: dict | None = None) -> list[Step]:
    """Schritte mit Beschreibung und Daten versehen.

    segments: Startadresse → Bytes der absoluten Segmente (Parameter, Objekttabelle, Code-Bild)
    tables: Load State Machine/Objektindex → Tabelle (1 Adress-, 2 Assoziations-, 3 Objekttabelle)
    rel_images: Objektindex → Abbild relativer Segmente (System B, z. B. 4 = Parameter)
    """
    masks = masks or {}  # ("abs", Adresse) / ("rel", Objektindex) → defined(i)
    out: list[Step] = []
    for name, a in steps:
        lsm = int(a.get("LsmIdx", a.get("ObjIdx", "0")) or 0)
        what = LSM_NAMES.get(lsm, f"Speicherbereich {lsm}")
        if name == "Connect":
            out.append(Step(name, "Verbindung zum Gerät aufbauen", a))
        elif name == "Disconnect":
            out.append(Step(name, "Verbindung trennen", a))
        elif name == "Restart":
            out.append(Step(name, "Gerät neu starten", a, writes=True))
        elif name == "Unload":
            out.append(Step(name, f"{what} entladen (Gerät hält an)", a, writes=True))
        elif name == "Load":
            out.append(Step(name, f"{what}: Laden beginnen", a, writes=True))
        elif name == "LoadCompleted":
            out.append(Step(name, f"{what}: Laden abschließen", a, writes=True))
        elif name == "AbsSegment":
            addr, size = int(a["Address"]), int(a["Size"])
            data = segments.get(addr) if lsm not in (1, 2) else tables.get(lsm)
            data = data[:size] if data is not None else None
            chunks = runs(data, masks.get(("abs", addr), lambda i: True)) if data else []
            out.append(Step(name, f"{what}: Segment {addr:#06x} ({size} Byte) anlegen"
                                  + (" und beschreiben" if data is not None else " (ohne Inhalt)"), a,
                            addr, data, True, chunks))
        elif name == "TaskSegment":
            out.append(Step(name, f"{what}: Startadresse {int(a['Address']):#06x} setzen", a, writes=True))
        elif name == "RelSegment":
            out.append(Step(name, f"{what}: {int(a['Size'])} Byte Speicher anfordern (Füllwert {a.get('Fill', '0')})", a,
                            writes=True))
        elif name == "WriteRelMem":
            data = tables.get(lsm) if lsm in (1, 2, 3) else rel_images.get(lsm)
            chunks = runs(data, masks.get(("rel", lsm), lambda i: True)) if data else []
            step = Step(name, "", a, int(a.get("Offset", "0")), data, True, chunks)
            step.text = f"{what}: Inhalt schreiben ({step.size} Byte" + (f" in {len(chunks)} Blöcken)" if len(chunks) > 1 else ")")
            out.append(step)
        elif name == "WriteMem":
            out.append(Step(name, f"Speicher {int(a.get('Address', '0')):#06x} schreiben", a,
                            int(a.get("Address", "0")), bytes.fromhex(a.get("InlineData", "")) or None, True))
        elif name == "WriteProp":
            # Daten fest (InlineData) oder aus Property-Parametern; Objekt über Index oder Typ
            obj = ("type", int(a["ObjType"])) if a.get("ObjType") else int(a.get("ObjIdx", "0"))
            data = bytes.fromhex(a["InlineData"]) if a.get("InlineData") else (props or {}).get((obj, int(a["PropId"])))
            label = f"Objekttyp {a['ObjType']}" if a.get("ObjType") else f"Objekt {a.get('ObjIdx')}"
            note = a.get("InternalDescription", "")
            out.append(Step(name, f"Property {label}/{a.get('PropId')} schreiben" + (f" ({note})" if note else "")
                            + ("" if data is not None else " – Wert fehlt"), a, data=data, writes=True))
        elif name == "CompareProp":
            out.append(Step(name, f"Prüfen: Property {a.get('ObjIdx')}/{a.get('PropId')} = {a.get('InlineData', '')}", a))
        elif name == "LoadImageProp":
            out.append(Step(name, f"Property {a.get('ObjIdx')}/{a.get('PropId')} aus dem Abbild schreiben", a, writes=True))
        elif name == "MapError":
            out.append(Step(name, f"Fehler {a.get('OriginalError')} als {a.get('MappedError')} werten", a))
        elif name == "Delay":
            out.append(Step(name, f"{a.get('MilliSeconds', '?')} ms warten", a))
        else:
            out.append(Step(name, name, a))
    return out


def device_plan(ap, device, master_xml: bytes | None, kind: str = "all", number_of=None) -> list[Step]:
    """Plan für ein Gerät aus Projekt und Produktdaten (ap = params.AppParams)."""
    from .image import SYSTEM_B, object_table, object_table_b, param_image, tables
    from .params import _read_member

    mask_text = ap.app.mask or ""
    mask = int(mask_text.removeprefix("MV-"), 16) if mask_text.startswith("MV-") else None
    img = param_image(ap, device.params)
    ev = ap.evaluate_values(device.params)
    linked = set()
    for link in device.links:
        n = link.number if link.number is not None else (number_of(link) if number_of else None)
        if n is not None and link.gas:
            linked.add(n)
    t = tables(device, mask, number_of, set(ev.active_cos))
    tabs = {1: t.address_bytes, 2: t.association_bytes}
    segs: dict[int, bytes] = {}
    rel: dict[int, bytes] = {}
    masks: dict = {}
    for sid, buf in img.segments.items():
        seg = ap.segments[sid]
        if seg.absolute:
            # ohne Standardbild und ohne Parameter (z. B. RAM-Bereich): nur anlegen, nichts schreiben
            if seg.data or any(sg == sid for sg, _ in img.touched):
                segs[seg.address] = bytes(buf)
                masks[("abs", seg.address)] = lambda i, sid=sid: img.defined(sid, i, ap)
        elif seg.lsm is not None:
            rel[seg.lsm] = bytes(buf)
            masks[("rel", seg.lsm)] = lambda i, sid=sid: img.defined(sid, i, ap)
    if mask == SYSTEM_B:
        tabs[3] = object_table_b(ap, ev, linked)
    else:
        ot = object_table(ap, ev, linked)
        if ot is not None:
            sid, off, data = ot
            seg = ap.segments[sid]
            buf = bytearray(segs.get(seg.address, seg.data))
            buf[off:off + len(data)] = data
            segs[seg.address] = bytes(buf)
            old = masks.get(("abs", seg.address), lambda i: False)
            masks[("abs", seg.address)] = lambda i, old=old, off=off, n=len(data): off <= i < off + n or old(i)
    steps = raw_steps(_read_member(ap.app.source_path, ap.app.member), master_xml, mask_text, kind)
    return build(steps, segs, tabs, rel, masks, {k: bytes(v) for k, v in img.props.items()})
