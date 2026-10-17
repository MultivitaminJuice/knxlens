"""Versionen eines Projekts – Momentaufnahmen mit Kommentar in derselben Projektdatei.

Eine Version enthält das ganze Projekt ohne Produktdaten (die ändern sich selten und
bleiben in der Datei). Sie wird wie ein Commit bewusst angelegt; Speichern allein legt
keine Version an. Wiederherstellen lädt die Version als neuen Arbeitsstand (rückgängig
machbar), die Versionsliste bleibt unverändert.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
import zlib
from pathlib import Path

from . import ComLink, Device, GroupAddress, GroupRange, Line, ModuleInstance, Project, Space

TABLE = "CREATE TABLE IF NOT EXISTS version (id INTEGER PRIMARY KEY, ts REAL, comment TEXT, stats TEXT, data BLOB)"


def stats(p: Project) -> dict:
    return {"devices": len(p.devices), "group_addresses": len(p.group_addresses),
            "links": sum(len(l.gas) for d in p.devices for l in d.links), "lines": len(p.lines),
            "spaces": len(p.spaces), "params": sum(len(d.params) for d in p.devices)}


def snapshot(p: Project) -> bytes:
    data = {f.name: getattr(p, f.name) for f in dataclasses.fields(p) if f.name != "products"}
    data = json.loads(json.dumps(data, default=lambda o: dataclasses.asdict(o)))
    data["group_addresses"] = list(data["group_addresses"].values())  # Reihenfolge bleibt erhalten
    return zlib.compress(json.dumps(data, ensure_ascii=False).encode(), 6)


def restore(blob: bytes, products: dict[str, bytes]) -> Project:
    d = json.loads(zlib.decompress(blob))
    p = Project(name=d["name"], project_id=d["project_id"], info=d["info"], installation=d["installation"],
                namespace=d["namespace"], products=products)
    p.lines = [Line(**x) for x in d["lines"]]
    for x in d["devices"]:
        x = dict(x)
        x["modules"] = [ModuleInstance(m["id"], m["ref"], [tuple(a) for a in m["args"]], m["extra"]) for m in x["modules"]]
        x["links"] = [ComLink(**l) for l in x["links"]]
        p.devices.append(Device(**x))
    p.ranges = [GroupRange(**x) for x in d["ranges"]]
    p.group_addresses = {x["address"]: GroupAddress(**x) for x in d["group_addresses"]}
    p.spaces = [Space(**x) for x in d["spaces"]]
    return p


def _db(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path))
    db.execute(TABLE)
    return db


def add(path: str | Path, p: Project, comment: str) -> int:
    with _db(path) as db:
        cur = db.execute("INSERT INTO version (ts, comment, stats, data) VALUES (?,?,?,?)",
                         (time.time(), comment.strip(), json.dumps(stats(p)), snapshot(p)))
        return cur.lastrowid


def listing(path: str | Path) -> list[dict]:
    with _db(path) as db:
        rows = db.execute("SELECT id, ts, comment, stats FROM version ORDER BY id DESC").fetchall()
    return [{"id": i, "ts": ts, "comment": c, "stats": json.loads(s)} for i, ts, c, s in rows]


def get(path: str | Path, version_id: int, products: dict[str, bytes]) -> Project:
    with _db(path) as db:
        row = db.execute("SELECT data FROM version WHERE id=?", (version_id,)).fetchone()
    if row is None:
        raise KeyError(f"Version {version_id} nicht gefunden")
    return restore(row[0], products)


def copy_into(old: Path, new_db: sqlite3.Connection) -> None:
    """Beim Speichern (neue Datei) die Versionen der alten Datei übernehmen."""
    if not old.exists():
        return
    new_db.execute(TABLE)
    new_db.execute("ATTACH DATABASE ? AS old", (f"file:{old}?mode=ro",))
    try:
        if new_db.execute("SELECT 1 FROM old.sqlite_master WHERE name='version'").fetchone():
            new_db.execute("INSERT INTO version SELECT * FROM old.version")
    finally:
        new_db.commit()
        new_db.execute("DETACH DATABASE old")


def diff(a: Project, b: Project) -> dict:
    """Was ist von a nach b anders? Kurzfassung für die Oberfläche."""
    da, db_ = {d.uid: d for d in a.devices}, {d.uid: d for d in b.devices}
    label = lambda d: f"{d.pa or '–'} {d.name}".strip()  # noqa: E731
    changed = []
    for uid in da.keys() & db_.keys():
        x, y = da[uid], db_[uid]
        what = []
        if x.pa != y.pa:
            what.append(f"Adresse {x.pa} → {y.pa}")
        if x.name != y.name:
            what.append("Name")
        if x.params != y.params:
            keys = set(x.params) ^ set(y.params) | {k for k in x.params.keys() & y.params.keys() if x.params[k] != y.params[k]}
            what.append(f"{len(keys)} Parameter")
        lx = {(l.ref, g) for l in x.links for g in l.gas}
        ly = {(l.ref, g) for l in y.links for g in l.gas}
        if lx != ly:
            what.append(f"Verknüpfungen +{len(ly - lx)} −{len(lx - ly)}")
        if what:
            changed.append(f"{label(y)}: {', '.join(what)}")
    ga, gb = a.group_addresses, b.group_addresses
    return {
        "devices_added": [label(db_[u]) for u in db_.keys() - da.keys()],
        "devices_removed": [label(da[u]) for u in da.keys() - db_.keys()],
        "devices_changed": sorted(changed),
        "gas_added": sorted(gb.keys() - ga.keys()),
        "gas_removed": sorted(ga.keys() - gb.keys()),
        "gas_renamed": sorted(k for k in ga.keys() & gb.keys() if ga[k].name != gb[k].name),
        "lines": {"added": sorted(f"{l.area}.{l.line}" for l in b.lines if (l.area, l.line) not in {(m.area, m.line) for m in a.lines}),
                  "removed": sorted(f"{l.area}.{l.line}" for l in a.lines if (l.area, l.line) not in {(m.area, m.line) for m in b.lines})},
        "spaces_changed": [s.name for s in b.spaces if s not in a.spaces] + [s.name for s in a.spaces if s.uid not in {t.uid for t in b.spaces}],
    }
