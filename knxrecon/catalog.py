"""Produktdaten (.knxprod, auch der Produktteil einer .knxproj) lesen und zuordnen.

Zuordnung über die Applikations-ID vom Bus: Gerät meldet z. B. M-0008_A-2032-12,
die Produktdatei enthält ApplicationProgram Id="M-0008_A-2032-12-ABCD" (Hash-Suffix).

Objekte: Eine Objektnummer kann mehrere ComObjectRefs haben (Text/DPT je nach
Parametrierung). Ohne Parameterdekodierung bleiben das Kandidaten; Größe und
Flags vom Bus grenzen sie ein. Objekte in ModuleDefs (z. B. MDT) werden noch nicht
aufgelöst – die Applikation wird dann markiert.

Dateien aus dem Netz sind nicht vertrauenswürdig: defusedxml, Größengrenzen je ZIP-Eintrag.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as ET

from .model import Device, GroupObject

MAX_MEMBER = 64 * 1024 * 1024  # größte erlaubte XML-Datei in einem Archiv
APP_FILE = re.compile(r"^M-[0-9A-F]{4}/M-[0-9A-F]{4}_A-[0-9A-F]{4}-[0-9A-F]{2}-[0-9A-F]{4}[^/]*\.xml$")
APP_KEY = re.compile(r"^(M-[0-9A-F]{4}_A-[0-9A-F]{4}-[0-9A-F]{2})")
DPST = re.compile(r"DPS?T-(\d+)(?:-(\d+))?")
LANG = "de-DE"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem, name: str):
    return (c for c in elem.iter() if _local(c.tag) == name)


def parse_dpt(value: str | None) -> dict | None:
    """'DPT-5 DPST-5-1' → {'main': 5, 'sub': 1}; genauester Eintrag gewinnt."""
    if not value:
        return None
    best = None
    for m in DPST.finditer(value):
        d = {"main": int(m[1]), "sub": int(m[2]) if m[2] else None}
        if best is None or (best["sub"] is None and d["sub"] is not None):
            best = d
    return best


@dataclass
class ObjectVariant:
    text: str
    function: str
    dpt: dict | None
    size: str | None
    flags: dict[str, bool]

    @property
    def label(self) -> str:
        return " – ".join(x for x in (self.text, self.function) if x)

    def matches(self, obj: GroupObject) -> bool:
        """Passt diese Variante zu Größe/Flags, die wir vom Bus gelesen haben?"""
        if self.size and obj.size and self.size != obj.size:
            return False
        return all(
            self.flags.get(k) is None or self.flags[k] == getattr(obj, k)
            for k in ("communication", "read", "write", "transmit")
        )


@dataclass
class Application:
    key: str  # M-XXXX_A-XXXX-XX
    full_id: str
    name: str
    mask: str
    has_modules: bool
    objects: dict[int, list[ObjectVariant]] = field(default_factory=dict)
    products: list[tuple[str, str]] = field(default_factory=list)  # (Name, Bestellnummer)
    source: str = ""

    def variants(self, number: int, obj: GroupObject | None = None) -> list[ObjectVariant]:
        """Kandidaten für eine Objektnummer, mit Bus-Daten eingegrenzt (falls vorhanden)."""
        all_ = self.objects.get(number, [])
        if obj is None:
            return all_
        fitting = [v for v in all_ if v.matches(obj)]
        return fitting or all_

    def object_dpt(self, number: int, obj: GroupObject | None = None) -> dict | None:
        """DPT nur, wenn alle passenden Varianten übereinstimmen."""
        dpts = {(d["main"], d["sub"]) for v in self.variants(number, obj) if (d := v.dpt)}
        if len(dpts) == 1:
            main, sub = dpts.pop()
            return {"main": main, "sub": sub}
        return None


FLAG_ATTRS = {
    "communication": "CommunicationFlag",
    "read": "ReadFlag",
    "write": "WriteFlag",
    "transmit": "TransmitFlag",
    "update": "UpdateFlag",
}


def _flags(attrs: dict) -> dict[str, bool]:
    return {k: attrs[a] == "Enabled" for k, a in FLAG_ATTRS.items() if a in attrs}


def _translations(root) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for lang in _children(root, "Language"):
        if lang.get("Identifier") != LANG:
            continue
        for el in _children(lang, "TranslationElement"):
            ref = el.get("RefId")
            for tr in el:
                if _local(tr.tag) == "Translation":
                    out.setdefault(ref, {})[tr.get("AttributeName")] = tr.get("Text", "")
    return out


def parse_application(xml: bytes, source: str = "") -> Application | None:
    root = ET.fromstring(xml)
    prog = next(_children(root, "ApplicationProgram"), None)
    if prog is None:
        return None
    full_id = prog.get("Id", "")
    m = APP_KEY.match(full_id)
    if not m:
        return None
    tr = _translations(root)

    def text(el_id: str, attrs: dict, name: str) -> str:
        return tr.get(el_id, {}).get(name) or attrs.get(name, "")

    base: dict[str, dict] = {}
    for co in _children(prog, "ComObject"):
        a = dict(co.attrib)
        a["Text"] = text(a["Id"], a, "Text")
        a["FunctionText"] = text(a["Id"], a, "FunctionText")
        base[a["Id"]] = a

    app = Application(
        key=m[1], full_id=full_id, name=text(full_id, prog.attrib, "Name"),
        mask=prog.get("MaskVersion", ""), has_modules=next(_children(prog, "ModuleDef"), None) is not None,
        source=source,
    )
    seen = set()
    for ref in _children(prog, "ComObjectRef"):
        parent = base.get(ref.get("RefId"))
        if parent is None or "Number" not in parent:
            continue  # z. B. Modulobjekt ohne feste Nummer
        merged = {**parent, **{k: v for k, v in ref.attrib.items() if k not in ("Id", "RefId")}}
        variant = ObjectVariant(
            text=text(ref.get("Id"), ref.attrib, "Text") or parent["Text"],
            function=text(ref.get("Id"), ref.attrib, "FunctionText") or parent["FunctionText"],
            dpt=parse_dpt(merged.get("DatapointType")),
            size=merged.get("ObjectSize"),
            flags=_flags(merged),
        )
        number = int(parent["Number"])
        sig = (number, variant.label, str(variant.dpt), variant.size, tuple(sorted(variant.flags.items())))
        if sig not in seen:
            seen.add(sig)
            app.objects.setdefault(number, []).append(variant)
    return app


def parse_hardware(xml: bytes) -> dict[str, list[tuple[str, str]]]:
    """App-Key → [(Produktname, Bestellnummer)]"""
    root = ET.fromstring(xml)
    tr = _translations(root)
    out: dict[str, list[tuple[str, str]]] = {}
    # Achtung: Die Liste heißt <Hardware>, jedes Gerät darin ebenfalls – nur Geräte haben eine Id.
    for hw in (h for h in _children(root, "Hardware") if h.get("Id")):
        products = [
            (tr.get(p.get("Id"), {}).get("Text") or p.get("Text", ""), p.get("OrderNumber", ""))
            for p in _children(hw, "Product")
        ]
        for ref in _children(hw, "ApplicationProgramRef"):
            m = APP_KEY.match(ref.get("RefId", ""))
            if m:
                out.setdefault(m[1], []).extend(p for p in products if p not in out.get(m[1], []))
    return out


class Catalog:
    def __init__(self) -> None:
        self.apps: dict[str, Application] = {}
        self.sources: list[str] = []
        self.skipped: list[str] = []

    def add(self, path: str | Path) -> int:
        """.knxprod/.knxproj (ZIP) oder entpackter Ordner. Liefert Anzahl neuer Applikationen."""
        path = Path(path)
        before = len(self.apps)
        hardware: dict[str, list[tuple[str, str]]] = {}
        for name, read in self._members(path):
            if name.endswith("/Hardware.xml"):
                for k, v in parse_hardware(read()).items():
                    hardware.setdefault(k, []).extend(v)
            elif APP_FILE.match(name):
                app = parse_application(read(), source=f"{path.name}:{name}")
                if app and app.key not in self.apps:
                    self.apps[app.key] = app
        for key, products in hardware.items():
            if key in self.apps:
                for p in products:
                    if p not in self.apps[key].products:
                        self.apps[key].products.append(p)
        self.sources.append(str(path))
        return len(self.apps) - before

    def _members(self, path: Path):
        if path.is_dir():
            for f in sorted(path.rglob("*.xml")):
                rel = f.relative_to(path).as_posix()
                yield rel, f.read_bytes
            return
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if not info.filename.endswith(".xml"):
                    continue
                if info.file_size > MAX_MEMBER:
                    self.skipped.append(f"{info.filename} ({info.file_size} Byte, zu groß)")
                    continue
                if info.flag_bits & 0x1:
                    self.skipped.append(f"{info.filename} (verschlüsselt)")
                    continue
                yield info.filename, (lambda i=info: z.read(i))

    def lookup(self, dev: Device) -> Application | None:
        return self.apps.get(dev.app_id or "")


def ga_dpts(devices: list[Device], catalog: Catalog) -> dict[str, dict]:
    """DPT je GA aus den verknüpften Objekten – nur wenn alle Objekte denselben Haupttyp haben.

    Untertyp: der genaueste gemeinsame; bei Widerspruch im Untertyp nur der Haupttyp.
    """
    found: dict[str, list[dict]] = {}
    for d in devices:
        app = catalog.lookup(d)
        if app is None:
            continue
        objs = {o.number: o for o in d.objects}
        for num, ga in d.links:
            if (dpt := app.object_dpt(num, objs.get(num))) is not None:
                found.setdefault(ga, []).append(dpt)
    out = {}
    for ga, dpts in found.items():
        mains = {x["main"] for x in dpts}
        if len(mains) != 1:
            continue
        subs = {x["sub"] for x in dpts if x["sub"] is not None}
        out[ga] = {"main": mains.pop(), "sub": subs.pop() if len(subs) == 1 else None}
    return out
