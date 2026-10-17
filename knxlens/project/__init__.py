"""Projektmodell und Projektdatei (*.knxlens, SQLite).

Ein Projekt ist in sich vollständig: Topologie, Geräte (Produkt, Parameter, Verknüpfungen),
Gruppenadressen, Gebäudestruktur – und die verwendeten Produktdaten als Kopie.

Verlustfreiheit gegenüber der ETS: Was das Modell ausdrücklich kennt, hat eigene Felder;
alle übrigen Attribute (`extra`) und unbekannten Unterelemente (`extra_xml`) werden
wörtlich mitgeführt und beim Export unverändert zurückgeschrieben.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from xknx.telegram import GroupAddress as _GA

SCHEMA_VERSION = 1


@dataclass
class Line:
    area: int
    line: int
    name: str = ""
    medium: str = "MT-0"  # MT-0 = TP, MT-5 = IP
    extra: dict = field(default_factory=dict)  # Attribute von Area/Line/Segment


@dataclass
class ComLink:
    ref: str  # ComObjectRef relativ zur Applikation, z. B. "O-5_R-9" oder "MD-2_M-1_MI-1_O-2-0_R-1"
    gas: list[str]  # "1/1/201", in ETS-Reihenfolge (erste = sendende GA)
    number: int | None = None  # Objektnummer (für Anzeige/Abgleich)
    extra: dict = field(default_factory=dict)  # ChannelId, DatapointType-Überschreibung …


@dataclass
class ModuleInstance:
    id: str  # relativ, z. B. "MD-2_M-1_MI-1"
    ref: str  # relativ, z. B. "MD-2_M-1"
    args: list[tuple[str, str]] = field(default_factory=list)  # (Argument-RefId relativ, Wert)
    extra: dict = field(default_factory=dict)


@dataclass
class Device:
    uid: str  # stabile Id im Projekt
    area: int
    line: int
    address: int | None  # Geräteteil der PA; None = noch nicht vergeben
    product_ref: str
    h2p_ref: str
    app_id: str = ""  # volle Applikations-Id (M-…_A-…-…-HASH), aus den Produktdaten
    name: str = ""
    description: str = ""
    comment: str = ""
    serial: str = ""  # hex
    origin: str = ""  # "ets", "rekonstruiert", "neu"
    params: dict[str, str] = field(default_factory=dict)  # ParameterInstanceRef-Id (voll) → Wert
    modules: list[ModuleInstance] = field(default_factory=list)
    links: list[ComLink] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    extra_xml: list[str] = field(default_factory=list)  # unbekannte Unterelemente, wörtlich

    @property
    def pa(self) -> str | None:
        return f"{self.area}.{self.line}.{self.address}" if self.address is not None else None


@dataclass
class GroupRange:
    uid: str
    name: str
    start: int
    end: int
    parent: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class GroupAddress:
    address: str  # "1/1/201"
    name: str = ""
    dpt: str = ""  # "DPST-1-1" / "DPT-9"
    description: str = ""
    range_uid: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def raw(self) -> int:
        return _GA(self.address).raw


@dataclass
class Space:
    uid: str
    type: str  # Building, BuildingPart, Floor, Room, Corridor, Stairway, DistributionBoard …
    name: str
    parent: str | None = None
    devices: list[str] = field(default_factory=list)  # Device.uid
    extra: dict = field(default_factory=dict)


@dataclass
class Project:
    name: str
    project_id: str = "P-0E7A"  # Projekt-Id im .knxproj-Sinn
    info: dict = field(default_factory=dict)  # weitere ProjectInformation-Attribute
    installation: dict = field(default_factory=dict)  # Attribute der Installation
    namespace: str = "http://knx.org/xml/project/23"
    lines: list[Line] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    ranges: list[GroupRange] = field(default_factory=list)
    group_addresses: dict[str, GroupAddress] = field(default_factory=dict)
    spaces: list[Space] = field(default_factory=list)
    products: dict[str, bytes] = field(default_factory=dict)  # Archivpfad → Inhalt (M-xxxx/…, knx_master.xml)

    def device(self, pa: str) -> Device | None:
        return next((d for d in self.devices if d.pa == pa), None)

    # --- Projektdatei -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.unlink(missing_ok=True)
        db = sqlite3.connect(tmp)
        db.executescript(_SCHEMA)
        j = json.dumps
        meta = {
            "schema_version": SCHEMA_VERSION, "name": self.name, "project_id": self.project_id,
            "info": self.info, "installation": self.installation, "namespace": self.namespace,
        }
        db.executemany("INSERT INTO meta VALUES (?,?)", [(k, j(v)) for k, v in meta.items()])
        # Produktdaten komprimiert (XML schrumpft etwa auf ein Zehntel)
        db.executemany("INSERT INTO product_file VALUES (?,?)",
                       [(k, zlib.compress(v, 6)) for k, v in self.products.items()])
        db.executemany("INSERT INTO line VALUES (?,?,?,?,?)",
                       [(l.area, l.line, l.name, l.medium, j(l.extra)) for l in self.lines])
        for d in self.devices:
            db.execute(
                "INSERT INTO device VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.uid, d.area, d.line, d.address, d.product_ref, d.h2p_ref, d.app_id, d.name, d.description,
                 d.comment, d.serial, d.origin, j(d.extra), j(d.extra_xml)),
            )
            db.executemany("INSERT INTO param_value VALUES (?,?,?)", [(d.uid, k, v) for k, v in d.params.items()])
            db.executemany("INSERT INTO module_instance VALUES (?,?,?,?,?,?)",
                           [(d.uid, i, m.id, m.ref, j(m.args), j(m.extra)) for i, m in enumerate(d.modules)])
            db.executemany("INSERT INTO com_link VALUES (?,?,?,?,?,?)",
                           [(d.uid, i, c.ref, j(c.gas), c.number, j(c.extra)) for i, c in enumerate(d.links)])
        db.executemany("INSERT INTO group_range VALUES (?,?,?,?,?,?)",
                       [(r.uid, r.parent, r.name, r.start, r.end, j(r.extra)) for r in self.ranges])
        db.executemany("INSERT INTO group_address VALUES (?,?,?,?,?,?,?,?)",
                       [(g.address, i, g.raw, g.name, g.dpt, g.description, g.range_uid, j(g.extra))
                        for i, g in enumerate(self.group_addresses.values())])
        for i, s in enumerate(self.spaces):
            db.execute("INSERT INTO space VALUES (?,?,?,?,?,?)", (s.uid, i, s.parent, s.type, s.name, j(s.extra)))
            db.executemany("INSERT INTO space_device VALUES (?,?,?)", [(s.uid, k, u) for k, u in enumerate(s.devices)])
        db.commit()
        db.close()
        tmp.replace(path)  # atomar: halb geschriebene Projektdateien gibt es nicht

    @classmethod
    def load(cls, path: str | Path) -> Project:
        db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        jl = json.loads
        meta = {k: jl(v) for k, v in db.execute("SELECT key, value FROM meta")}
        if meta.get("schema_version", 0) > SCHEMA_VERSION:
            raise ValueError(f"Projektdatei ist neuer (Schema {meta['schema_version']}) als dieses knxlens")
        p = cls(name=meta["name"], project_id=meta["project_id"], info=meta["info"],
                installation=meta["installation"], namespace=meta["namespace"])
        p.products = {k: zlib.decompress(v) for k, v in db.execute("SELECT name, data FROM product_file")}
        p.lines = [Line(a, l, n, m, jl(e)) for a, l, n, m, e in db.execute("SELECT * FROM line ORDER BY area, line")]
        for row in db.execute("SELECT * FROM device ORDER BY rowid"):
            uid, area, line, addr, pref, h2p, app, name, desc, comment, serial, origin, extra, extra_xml = row
            d = Device(uid, area, line, addr, pref, h2p, app, name, desc, comment, serial, origin,
                       extra=jl(extra), extra_xml=jl(extra_xml))
            d.params = dict(db.execute("SELECT ref, value FROM param_value WHERE device=? ORDER BY rowid", (uid,)))
            d.modules = [ModuleInstance(i, r, [tuple(a) for a in jl(args)], jl(e)) for i, r, args, e in db.execute(
                "SELECT id, ref, args, extra FROM module_instance WHERE device=? ORDER BY pos", (uid,))]
            d.links = [ComLink(r, jl(g), n, jl(e)) for r, g, n, e in db.execute(
                "SELECT ref, gas, number, extra FROM com_link WHERE device=? ORDER BY pos", (uid,))]
            p.devices.append(d)
        p.ranges = [GroupRange(u, n, s, e, par, jl(x)) for u, par, n, s, e, x in db.execute(
            "SELECT * FROM group_range ORDER BY rowid")]
        for addr, _, _, name, dpt, desc, rng, extra in db.execute("SELECT * FROM group_address ORDER BY pos"):
            p.group_addresses[addr] = GroupAddress(addr, name, dpt, desc, rng, jl(extra))
        for uid, _, parent, typ, name, extra in db.execute("SELECT * FROM space ORDER BY pos"):
            devs = [u for (u,) in db.execute("SELECT device FROM space_device WHERE space=? ORDER BY pos", (uid,))]
            p.spaces.append(Space(uid, typ, name, parent, devs, jl(extra)))
        db.close()
        return p


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE product_file (name TEXT PRIMARY KEY, data BLOB);
CREATE TABLE line (area INTEGER, line INTEGER, name TEXT, medium TEXT, extra TEXT, PRIMARY KEY (area, line));
CREATE TABLE device (
    uid TEXT PRIMARY KEY, area INTEGER, line INTEGER, address INTEGER, product_ref TEXT, h2p_ref TEXT,
    app_id TEXT, name TEXT, description TEXT, comment TEXT, serial TEXT, origin TEXT, extra TEXT, extra_xml TEXT
);
CREATE TABLE param_value (device TEXT, ref TEXT, value TEXT, PRIMARY KEY (device, ref));
CREATE TABLE module_instance (device TEXT, pos INTEGER, id TEXT, ref TEXT, args TEXT, extra TEXT);
CREATE TABLE com_link (device TEXT, pos INTEGER, ref TEXT, gas TEXT, number INTEGER, extra TEXT);
CREATE TABLE group_range (uid TEXT PRIMARY KEY, parent TEXT, name TEXT, start INTEGER, "end" INTEGER, extra TEXT);
CREATE TABLE group_address (
    address TEXT PRIMARY KEY, pos INTEGER, raw INTEGER, name TEXT, dpt TEXT, description TEXT, range_uid TEXT, extra TEXT
);
CREATE TABLE space (uid TEXT PRIMARY KEY, pos INTEGER, parent TEXT, type TEXT, name TEXT, extra TEXT);
CREATE TABLE space_device (space TEXT, pos INTEGER, device TEXT);
"""
