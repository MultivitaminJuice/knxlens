"""Editor: JSON-API für die Vue-Oberfläche plus Auslieferung der gebauten Oberfläche.

Die gesamte KNX-Logik bleibt hier im Kern (Sichtbarkeit, Objekte, später Kodierung);
die Oberfläche zeigt nur an und schickt Änderungen. Kein Buszugriff.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import params
from ..catalog import Application, Catalog
from ..project import Device, Project
from ..project.edit import (EditError, editor_item, orphans, param_change_effect, rebind_variants, set_param,
                            unlink_orphans)
from ..project.ops import MEDIA, SPACE_CHILDREN, SPACE_TYPES, Editor, OpError, load_state, needs_download
from ..project.reconstruct import catalog_key as _catalog_key, rel_ref
from ..project.ops import ga_raw as GroupAddressRaw
from ..traffic import format_value
from .busmon import SERVICES, BusMonitor
from ..project.products import (MAX_PRODUCT_FILE, ProductError, add_products, app_for, manufacturer_names, product_texts,
                                project_catalog)

STATIC = Path(__file__).parent.parent / "web" / "static" / "editor"
MEDIUM = MEDIA


@dataclass
class EditorState:
    path: Path
    project: Project
    dirty: bool = False
    ops: Editor | None = None
    _catalog: Catalog | None = None
    _app_params: dict[str, params.AppParams] = field(default_factory=dict)
    _product_texts: dict[str, str] | None = None

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = project_catalog(self.project)
        return self._catalog

    def app(self, d: Device) -> Application | None:
        return app_for(self.catalog, d.app_id)

    def app_params(self, app: Application) -> params.AppParams:
        if app.key not in self._app_params:
            self._app_params[app.key] = params.AppParams(app)
        return self._app_params[app.key]

    def switch(self, path: Path, project: Project) -> None:
        """Anderes Projekt öffnen: Katalog, Parameterdaten und Rückgängig-Verlauf neu."""
        self.path, self.project, self.dirty = path, project, False
        self.ops = Editor(project)
        self._catalog = None
        self._app_params.clear()
        self._product_texts = None
        _remember(path)

    def device(self, uid: str) -> Device:
        d = next((d for d in self.project.devices if d.uid == uid), None)
        if d is None:
            raise HTTPException(404, f"Gerät {uid} nicht im Projekt")
        return d

    def product_name(self, d: Device) -> str:
        app = self.app(d)
        if app is None:
            if self._product_texts is None:
                self._product_texts = product_texts(self.project)
            return self._product_texts.get(d.product_ref) or f"Unbekanntes Produkt ({d.product_ref[:6]})"
        for (pid, _), (name, order) in zip(app.product_refs, app.products):
            if pid == d.product_ref:
                return name
        return app.products[0][0] if app.products else app.name


def _flag_text(flags: dict) -> str:
    return "".join(c if flags.get(k) else "-" for c, k in zip("KLSÜA", ("communication", "read", "write", "transmit", "update")))


def _dpt_text(dpt: dict | None) -> str:
    if not dpt:
        return ""
    return f"{dpt['main']}.{dpt['sub']:03d}" if dpt.get("sub") is not None else str(dpt["main"])


def device_detail(state: EditorState, d: Device) -> dict:
    app = state.app(d)
    ga_names = {g.address: g.name for g in state.project.group_addresses.values()}
    out = {
        "uid": d.uid, "pa": d.pa, "name": d.name, "description": d.description, "origin": d.origin,
        "product": state.product_name(d), "app_id": d.app_id, "has_products": app is not None,
        "load": load_state(d),
        "objects": [], "params": [],
    }
    if app is None:
        return out
    ev = state.app_params(app).evaluate_values(d.params)

    # Objekte: alle laut Parametern aktiven, dazu verknüpfte (auch falls inaktiv)
    linked: dict[str, list[str]] = {}
    for link in d.links:
        linked.setdefault(_catalog_key(app, link.ref), []).extend(link.gas)
    keys = dict.fromkeys(list(ev.active_cos.values()) + list(linked))
    objects = []
    for key in keys:
        v = app.variant_by_ref.get(key)
        number = app.ref_number.get(key)
        if v is None or number is None:
            continue
        objects.append({
            "ref": rel_ref(app, key), "number": number, "text": v.text, "function": v.function, "size": v.size,
            "dpt": f"{v.dpt['main']}.{v.dpt['sub']:03d}" if v.dpt and v.dpt.get("sub") is not None
            else (str(v.dpt["main"]) if v.dpt else ""),
            "flags": _flag_text(v.flags),
            "priority": {"Low": "Niedrig", "High": "Hoch", "Alert": "Alarm"}.get(v.priority, v.priority),
            "active": key in ev.active_cos.values(),
            "gas": [{"address": g, "name": ga_names.get(g, "")} for g in linked.get(key, [])],
        })
    out["objects"] = sorted(objects, key=lambda o: o["number"])

    ap = state.app_params(app)
    groups: dict[tuple, list] = {}
    for v in ev.values:
        if v.internal:
            continue
        groups.setdefault(v.path, []).append(editor_item(ap, v))
    out["params"] = [{"path": list(path), "items": items} for path, items in groups.items()]
    return out


class OpIn(BaseModel):
    op: str
    args: dict = {}


# Erlaubte Befehle der Oberfläche (Name → Editor-Methode)
OPS = {name: getattr(Editor, name) for name in (
    "range_add", "range_rename", "range_delete",
    "ga_add", "ga_update", "ga_readdress", "ga_move", "ga_copy", "ga_delete",
    "link", "unlink", "set_sending",
    "device_update", "device_set_pa", "device_move", "device_copy", "device_delete", "device_insert",
    "area_add", "area_rename", "area_delete", "line_add", "line_update", "line_delete",
    "space_add", "space_update", "space_delete", "space_move", "space_assign", "space_unassign",
)}


class ParamIn(BaseModel):
    id: str
    value: str | None = None  # None = auf Standard zurücksetzen
    orphans: str = "ask"  # Verknüpfungen, deren Objekt wegfällt: ask (Rückfrage), keep, unlink


class UidsIn(BaseModel):
    uids: list[str] | None = None  # None = alle Geräte


RECENT = Path.home() / ".config" / "knxlens" / "recent.json"


def _remember(path: Path) -> None:
    """Zuletzt geöffnete Projekte (für „Öffnen“)."""
    import json
    try:
        items = json.loads(RECENT.read_text()) if RECENT.exists() else []
        items = [str(path.resolve())] + [p for p in items if p != str(path.resolve())]
        RECENT.parent.mkdir(parents=True, exist_ok=True)
        RECENT.write_text(json.dumps(items[:12], indent=1))
    except (OSError, ValueError):
        pass  # nur Komfort


def _recent() -> list[Path]:
    import json
    try:
        return [Path(p) for p in json.loads(RECENT.read_text())] if RECENT.exists() else []
    except (OSError, ValueError):
        return []


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip().replace(" ", "_")
    return s[:60] or "Projekt"


class NameIn(BaseModel):
    name: str


class PathIn(BaseModel):
    path: str
    force: bool = False


class CommentIn(BaseModel):
    comment: str = ""


class MonitorIn(BaseModel):
    gateway: str


def _dpt(text: str) -> dict | None:
    """'DPST-9-1' / 'DPT-9' → {'main': 9, 'sub': 1} für die Wertanzeige."""
    import re
    m = re.match(r"DPS?T-(\d+)(?:-(\d+))?", text or "")
    return {"main": int(m[1]), "sub": int(m[2]) if m[2] else None} if m else None


def find_store(project: Path) -> Path:
    """Messdatenbank (gelesene Gerätedaten): im Projektordner oder eine Ebene darüber, sonst neu daneben."""
    for folder in (project.parent, project.parent.parent):
        if (folder / "knxlens.sqlite").exists():
            return folder / "knxlens.sqlite"
    return project.parent / "knxlens.sqlite"


class ReadIn(BaseModel):
    gateway: str = ""


def create_editor_app(project_path: str, preload: bool = True, gateway: str = "", db: str | None = None) -> FastAPI:
    path = Path(project_path)
    db_path = Path(db) if db else find_store(path)
    state = EditorState(path, Project.load(path))
    state.ops = Editor(state.project)
    _remember(path)
    if preload:
        # Produktdaten vorab einlesen (einige Sekunden) – sonst wartet die erste Anfrage darauf
        _ = state.catalog
        for d in state.project.devices:  # Parameterteil der verwendeten Applikationen
            if (app_ := state.app(d)) is not None:
                state.app_params(app_)
    monitor = BusMonitor()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await monitor.stop()  # Tunnelverbindung beim Beenden sauber abbauen

    app = FastAPI(title="knxlens Editor", docs_url=None, redoc_url=None, lifespan=lifespan)

    # --- Download-Vorschau: Plan, Abgleich mit dem Gerät, Gerät lesen (nur lesend) -------------

    def _mask(d: Device, app_) -> int | None:
        from ..store import Store
        if db_path.exists():
            found = dict(Store(db_path).found_devices())
            if d.pa in found:
                return found[d.pa]
        m = app_.mask if app_ else ""
        return int(m[3:], 16) if m.startswith("MV-") else None

    @app.get("/api/device/{uid}/plan")
    def device_plan_api(uid: str, kind: str = "all"):
        from ..loadplan import device_plan
        from ..project.reconstruct import object_number
        d = state.device(uid)
        app_ = state.app(d)
        if app_ is None:
            raise HTTPException(400, "Ohne Produktdaten kein Download-Plan")
        if kind not in ("all", "par", "grp"):
            raise HTTPException(400, "kind: all, par oder grp")
        steps = device_plan(state.app_params(app_), d, state.project.products.get("knx_master.xml"), kind,
                            lambda l: object_number(app_, l.ref))
        return {"kind": kind, "total": sum(s.size for s in steps if s.writes and s.data),
                "steps": [{"op": s.op, "text": s.text, "writes": s.writes, "size": s.size if s.data else 0,
                           "address": s.address, "blocks": len(s.chunks)} for s in steps]}

    @app.get("/api/device/{uid}/check")
    def device_check(uid: str):
        """Berechnetes Abbild gegen den zuletzt gelesenen Gerätespeicher."""
        from ..image import check_against_store
        from ..project.reconstruct import object_number
        from ..store import Store
        d = state.device(uid)
        app_ = state.app(d)
        if app_ is None:
            raise HTTPException(400, "Ohne Produktdaten kein Abgleich")
        if not db_path.exists():
            return {"read": False}
        st = Store(db_path)
        last = st.db.execute("SELECT MAX(ts) FROM read WHERE pa=?", (d.pa,)).fetchone()[0]
        if not last:
            return {"read": False}
        c = check_against_store(state.app_params(app_), d, st, _mask(d, app_), lambda l: object_number(app_, l.ref))
        return {"read": True, "last_read": last, "param_equal": c.param_equal, "param_diff": len(c.param_diff),
                "diffs": [{"segment": sid.rsplit("_", 1)[-1], "offset": off, "device": dv, "computed": cv, "params": own[:2]}
                          for sid, off, dv, cv, own in c.param_diff[:30]],
                "address_table": c.address_table, "association_table": c.association_table,
                "object_table": list(c.object_table) if c.object_table else None,
                "ets_image": [c.ets_equal, c.ets_equal + len(c.ets_diff)] if (c.ets_equal or c.ets_diff) else None}

    @app.post("/api/device/{uid}/read")
    async def device_read(uid: str, body: ReadIn):
        """Gerät auslesen – nur lesende Dienste (ReadOnlyGuard), danach Abgleich."""
        from xknx.telegram import IndividualAddress
        from .. import bus, device as devmod
        from ..params import read as read_params
        from ..store import Store
        d = state.device(uid)
        app_ = state.app(d)
        gw = (body.gateway or monitor.gateway or gateway).strip()
        if not gw:
            raise HTTPException(422, "IP-Adresse des KNX-IP-Interfaces fehlt")
        if d.pa is None:
            raise HTTPException(422, "Gerät hat keine physikalische Adresse")
        st = Store(db_path)
        mask = _mask(d, app_)
        try:
            async with bus.connect(gw) as xknx:
                st.start_session(gw, str(xknx.current_address), f"editor read {d.pa}")
                await devmod.read_device(xknx, st, d.pa, mask)
                if app_ is not None:
                    ap = state.app_params(app_)
                    async with xknx.management.connection(IndividualAddress(d.pa)) as conn:
                        r = devmod.Reader(conn, st)
                        await read_params(r, ap)
                        if ap.co_table:  # System 7: Gruppenobjekttabelle
                            seg = ap.segments[ap.co_table[0]]
                            n = seg.data[ap.co_table[1]] if len(seg.data) > ap.co_table[1] else 0
                            await r.mem(seg.address + ap.co_table[1], 3 + 4 * max(n, 1))
        except Exception as e:  # noqa: BLE001 - Busfehler gehören in die Oberfläche
            raise HTTPException(502, f"Lesen fehlgeschlagen: {e}") from None
        return device_check(uid)

    @app.get("/api/monitor")
    def monitor_rows(since: int = 0, limit: int = 2000):
        p = state.project
        names = {d.pa: d.name or state.product_name(d) for d in p.devices if d.pa}
        obj_dpt: dict[str, dict] = {}  # GA ohne eigenen DPT: Typ eines verknüpften Objekts
        for d in p.devices:
            app_ = state.app(d)
            for link in d.links:
                own = _dpt(link.extra.get("DatapointType", ""))
                v = app_.variant_by_ref.get(_catalog_key(app_, link.ref)) if app_ and not own else None
                found = own or (v.dpt if v and v.dpt else None)
                for g in link.gas:
                    if found:
                        obj_dpt.setdefault(g, found)
        rows = []
        for r in monitor.since(since)[-limit:]:
            g = p.group_addresses.get(r["dst"])
            rows.append({
                "id": r["id"], "ts": r["ts"], "src": r["src"], "src_name": names.get(r["src"], ""),
                "dst": r["dst"], "dst_name": g.name if g else "", "dpt": g.dpt if g else "",
                "service": SERVICES.get(r["service"], r["service"]),
                "raw": r["payload"].hex(" ") if r["payload"] is not None else ("" if r["bits"] is None else str(r["bits"])),
                "value": format_value(r["payload"], r["bits"], (_dpt(g.dpt) if g else None) or obj_dpt.get(r["dst"])),
                "known": g is not None,
            })
        return {"state": monitor.state, "gateway": monitor.gateway or gateway, "own": monitor.own,
                "error": monitor.error, "rows": rows}

    @app.post("/api/monitor/start")
    async def monitor_start(body: MonitorIn):
        if not body.gateway.strip():
            raise HTTPException(422, "IP-Adresse des KNX-IP-Interfaces fehlt")
        await monitor.start(body.gateway.strip())
        return {"state": monitor.state}

    @app.post("/api/monitor/stop")
    async def monitor_stop():
        await monitor.stop()
        return {"state": monitor.state}

    @app.post("/api/monitor/clear")
    def monitor_clear():
        monitor.clear()
        return {"ok": True}

    def _orphan_count() -> int:
        n = 0
        for d in state.project.devices:
            app_ = state.app(d)
            if app_ is not None and d.links:
                n += len(orphans(state.app_params(app_), d))
        return n

    @app.get("/api/project")
    def project_info():
        p = state.project
        return {"name": p.name, "file": state.path.name, "path": str(state.path), "project_id": p.project_id, "dirty": state.dirty,
                "undo": state.ops.undo_stack[-1][0] if state.ops.undo_stack else None,
                "redo": state.ops.redo_stack[-1][0] if state.ops.redo_stack else None,
                "devices": len(p.devices), "orphans": _orphan_count(),
                "group_addresses": len(p.group_addresses), "links": sum(len(l.gas) for d in p.devices for l in d.links),
                "space_types": SPACE_TYPES, "space_root": list(SPACE_CHILDREN[None]), "media": MEDIA}

    @app.get("/api/tree/topology")
    def topology():
        p = state.project
        areas: dict[int, dict] = {}
        for ln in sorted(p.lines, key=lambda l: (l.area, l.line)):
            aname = ((ln.extra or {}).get("area") or {}).get("Name", "")
            area = areas.setdefault(ln.area, {"key": f"a{ln.area}", "label": f"{ln.area} {aname or 'Bereich'}",
                                              "icon": "pi pi-sitemap", "children": [],
                                              "data": {"type": "area", "area": ln.area, "name": aname}})
            devices = [d for d in p.devices if (d.area, d.line) == (ln.area, ln.line)]
            area["children"].append({
                "key": f"l{ln.area}.{ln.line}", "label": f"{ln.area}.{ln.line} {ln.name or 'Linie'}",
                "icon": "pi pi-share-alt", "data": {"type": "line", "medium": MEDIUM.get(ln.medium, ln.medium),
                                                   "medium_id": ln.medium, "area": ln.area, "line": ln.line,
                                                   "name": ln.name},
                "children": [{
                    "key": d.uid, "label": f"{d.pa or '–'} {d.name or state.product_name(d)}",
                    "icon": "pi pi-box", "leaf": False,
                    "data": {"type": "device", "uid": d.uid, "pa": d.pa, "load": load_state(d)},
                } for d in sorted(devices, key=lambda d: (d.address is None, d.address or 0))],
            })
        # Suchordner wie in der ETS: Geräte, bei denen noch etwas zu laden ist
        pending = [d for d in p.devices if needs_download(d)]
        folder = {"key": "need", "label": "Suchordner: Programmieren notwendig", "data": {"type": "folder", "count": len(pending)},
                  "children": [{"key": f"need/{d.uid}", "label": f"{d.pa or '–'} {d.name or state.product_name(d)}",
                                "leaf": True, "data": {"type": "device", "uid": d.uid, "pa": d.pa, "load": load_state(d)}}
                               for d in sorted(pending, key=lambda d: (d.area, d.line, d.address or 0))]}
        return [folder] + list(areas.values())

    @app.get("/api/tree/buildings")
    def buildings():
        p = state.project
        by_uid = {d.uid: d for d in p.devices}
        nodes: dict[str | None, list] = {}
        for s in p.spaces:
            node = {"key": s.uid, "label": s.name, "icon": "pi pi-home" if s.type in ("Building", "BuildingPart")
                    else "pi pi-th-large", "children": [],
                    "data": {"type": "space", "uid": s.uid, "space_type": s.type, "name": s.name,
                             "type_label": SPACE_TYPES.get(s.type, s.type),
                             "child_types": list(SPACE_CHILDREN.get(s.type, ())),
                             "holds_devices": s.type not in ("Building", "BuildingPart")}}
            nodes.setdefault(s.parent, []).append(node)
            node["_devices"] = [by_uid[u] for u in s.devices if u in by_uid]

        def build(parent):
            out = []
            for n in nodes.get(parent, []):
                devs = n.pop("_devices")
                n["children"] = build(n["key"]) + [{
                    "key": f"{n['key']}/{d.uid}", "label": f"{d.pa or '–'} {d.name or state.product_name(d)}",
                    "icon": "pi pi-box", "leaf": True, "data": {"type": "device", "uid": d.uid, "space": n["key"]}}
                    for d in devs]
                out.append(n)
            return out

        return build(None)

    @app.get("/api/tree/groupaddresses")
    def group_addresses():
        p = state.project
        linked_count: dict[str, int] = {}
        for d in p.devices:
            for link in d.links:
                for g in link.gas:
                    linked_count[g] = linked_count.get(g, 0) + 1
        children: dict[str | None, list] = {}
        for r in p.ranges:
            level = "middle" if r.parent else "main"
            prefix = f"{r.end >> 11}/{(r.end >> 8) & 7}" if r.parent else f"{r.end >> 11}"
            children.setdefault(r.parent, []).append(
                {"key": r.uid, "label": f"{prefix} {r.name}", "icon": "pi pi-folder",
                 "data": {"type": "range", "level": level, "uid": r.uid, "name": r.name}, "children": []})
        for g in p.group_addresses.values():
            children.setdefault(g.range_uid, []).append({
                "key": f"ga:{g.address}", "label": f"{g.address} {g.name}", "icon": "pi pi-link", "leaf": True,
                "data": {"type": "ga", "address": g.address, "dpt": g.dpt, "name": g.name,
                         "linked": linked_count.get(g.address, 0)}})

        def sort_key(n):
            # Anzeige wie in der ETS nach Adresse; die Reihenfolge im Projekt bleibt unverändert
            if n["data"]["type"] == "range":
                return (0, next(r.start for r in p.ranges if r.uid == n["key"]))
            return (1, GroupAddressRaw(n["data"]["address"]))

        def build(parent):
            out = []
            for n in sorted(children.get(parent, []), key=sort_key):
                if n["data"]["type"] == "range":
                    n["children"] = build(n["key"])
                out.append(n)
            return out

        return build(None)

    @app.get("/api/device/{uid}")
    def device(uid: str):
        return device_detail(state, state.device(uid))

    @app.put("/api/device/{uid}/param")
    def put_param(uid: str, body: ParamIn):
        d = state.device(uid)
        app_ = state.app(d)
        if app_ is None:
            raise HTTPException(400, "Ohne Produktdaten nicht bearbeitbar")
        ap = state.app_params(app_)
        if body.orphans not in ("ask", "keep", "unlink"):
            raise HTTPException(400, "orphans: ask, keep oder unlink")
        try:
            effect = param_change_effect(ap, d, body.id, body.value)
        except EditError as e:
            raise HTTPException(422, str(e)) from None
        if effect and body.orphans == "ask":
            n = sum(len(o["gas"]) for o in effect)
            raise HTTPException(409, {"message": f"{len(effect)} Objekt(e) mit {n} Gruppenadress-Zuordnung(en) fallen weg",
                                      "orphans": effect})

        def do():
            set_param(ap, d, body.id, body.value)
            rebind_variants(ap, d)
            if body.orphans == "unlink":
                gone = {o["ref"] for o in effect}
                d.links = [l for l in d.links if l.ref not in gone]

        state.ops.run("Parameter ändern", do)
        state.dirty = True
        return device_detail(state, state.device(uid))

    @app.get("/api/orphans")
    def orphan_list():
        """Verknüpfungen an Objekten, die es bei den aktuellen Parametern nicht gibt."""
        out = []
        for d in state.project.devices:
            app_ = state.app(d)
            if app_ is None:
                continue
            found = orphans(state.app_params(app_), d)
            if found:
                out.append({"uid": d.uid, "pa": d.pa, "name": d.name or state.product_name(d), "orphans": found})
        return out

    @app.post("/api/orphans/unlink")
    def orphan_unlink(body: UidsIn):
        targets = [d for d in state.project.devices if body.uids is None or d.uid in body.uids]

        def do():
            n = 0
            for d in targets:
                app_ = state.app(d)
                if app_ is not None:
                    n += unlink_orphans(state.app_params(app_), d)
            return n

        n = state.ops.run("Verwaiste Verknüpfungen lösen", do)
        state.dirty = True
        return {"unlinked": n}

    @app.post("/api/op")
    def op(body: OpIn):
        fn = OPS.get(body.op)
        if fn is None:
            raise HTTPException(400, f"Unbekannter Befehl: {body.op}")
        try:
            result = fn(state.ops, **body.args)
        except OpError as e:
            raise HTTPException(422, str(e)) from None
        except TypeError as e:
            raise HTTPException(400, f"Falsche Argumente: {e}") from None
        state.dirty = True
        return {"result": result, "undo": state.ops.undo_stack[-1][0] if state.ops.undo_stack else None}

    @app.post("/api/undo")
    def undo():
        try:
            name = state.ops.undo()
        except OpError as e:
            raise HTTPException(422, str(e)) from None
        state.dirty = True
        return {"undone": name}

    @app.post("/api/redo")
    def redo():
        try:
            name = state.ops.redo()
        except OpError as e:
            raise HTTPException(422, str(e)) from None
        state.dirty = True
        return {"redone": name}

    @app.get("/api/catalog")
    def catalog_list():
        out = []
        names = manufacturer_names(state.project)
        for app_ in state.catalog.apps.values():
            for (pid, h2p), (name, order) in zip(app_.product_refs, app_.products):
                out.append({"product_ref": pid, "h2p_ref": h2p, "app_id": app_.full_id, "name": name,
                            "order": order, "manufacturer": names.get(app_.key[:6], app_.key[:6]),
                            "manufacturer_id": app_.key[:6], "application": app_.name})
        return sorted(out, key=lambda x: (x["manufacturer"], x["name"]))

    @app.post("/api/products")
    async def products_add(request: Request, name: str = ""):
        """Rohdaten einer .knxprod im Body (kein Multipart nötig)."""
        size = int(request.headers.get("content-length") or 0)
        if size > MAX_PRODUCT_FILE:
            raise HTTPException(413, "Produktdatei ist zu groß")
        data = await request.body()
        try:
            result = add_products(state.project, data, name)
        except ProductError as e:
            raise HTTPException(422, str(e)) from None
        state._catalog = None  # beim nächsten Zugriff mit den neuen Produkten aufbauen
        state._app_params.clear()
        state._product_texts = None
        _ = state.catalog
        state.dirty = True
        return result

    # --- Projekte: öffnen, neu, speichern unter, Import/Export ---------------------------------

    def _target(name: str) -> Path:
        target = state.path.parent / f"{_slug(name)}.knxlens"
        if target.exists():
            raise HTTPException(409, f"{target.name} gibt es schon in {target.parent}")
        return target

    def _guard_dirty(force: bool) -> None:
        if state.dirty and not force:
            raise HTTPException(409, "Ungespeicherte Änderungen – erst speichern oder verwerfen")

    def _open(target: Path) -> dict:
        try:
            project = Project.load(target)
        except Exception as e:  # noqa: BLE001 - kaputte/fremde Datei
            raise HTTPException(422, f"{target.name} lässt sich nicht öffnen: {e}") from None
        state.switch(target, project)
        _ = state.catalog
        return project_info()

    @app.get("/api/files")
    def files():
        folder = state.path.parent
        seen, out = set(), []
        for f in sorted(folder.glob("*.knxlens"), key=lambda x: -x.stat().st_mtime) + _recent():
            r = f.resolve()
            if r in seen or not f.exists() or f.suffix != ".knxlens":
                continue
            seen.add(r)
            out.append({"path": str(r), "name": f.stem, "folder": str(f.parent), "mtime": f.stat().st_mtime,
                        "current": r == state.path.resolve()})
        return {"folder": str(folder), "files": out}

    @app.post("/api/project/open")
    def open_project(body: PathIn):
        target = Path(body.path).expanduser()
        if target.suffix != ".knxlens" or not target.is_file():
            raise HTTPException(422, "Nur vorhandene *.knxlens-Dateien")
        _guard_dirty(body.force)
        return _open(target)

    @app.post("/api/project/new")
    def new_project(body: NameIn, force: bool = False):
        import secrets
        from ..project import Line
        _guard_dirty(force)
        target = _target(body.name)
        p = Project(name=body.name.strip() or "Neues Projekt", project_id=f"P-{secrets.randbelow(0xFFFF):04X}",
                    lines=[Line(1, 0), Line(1, 1)])
        p.save(target)
        return _open(target)

    @app.post("/api/project/save_as")
    def save_as(body: NameIn):
        target = _target(body.name)
        state.project.name = body.name.strip() or state.project.name
        state.project.save(target)
        from ..project.versions import copy_into
        import sqlite3
        with sqlite3.connect(target) as db:  # Versionsverlauf mitnehmen
            copy_into(state.path, db)
        state.path, state.dirty = target, False
        _remember(target)
        return project_info()

    @app.post("/api/project/import")
    async def import_project(request: Request, name: str = "", force: bool = False):
        """ETS-Projekt (.knxproj, Rohdaten im Body) als neues knxlens-Projekt anlegen und öffnen."""
        import tempfile
        from ..project.knxproj_io import import_knxproj
        _guard_dirty(force)
        data = await request.body()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "import.knxproj"
            src.write_bytes(data)
            try:
                p = import_knxproj(src)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, f"Kein lesbares ETS-Projekt: {e}") from None
        target = _target(Path(name).stem or p.name)
        p.save(target)
        return _open(target)

    @app.get("/api/project/export")
    def export_project():
        import tempfile
        from ..project.knxproj_io import export_knxproj
        out = Path(tempfile.mkdtemp()) / f"{_slug(state.project.name)}.knxproj"
        export_knxproj(state.project, str(out))
        return FileResponse(out, filename=out.name, media_type="application/octet-stream")

    # --- Versionen ----------------------------------------------------------------------

    @app.get("/api/versions")
    def version_list():
        from ..project import versions
        return versions.listing(state.path)

    @app.post("/api/versions")
    def version_add(body: CommentIn):
        """Speichert und legt eine Version an (wie ein Commit)."""
        from ..project import versions
        state.project.save(state.path)
        state.dirty = False
        vid = versions.add(state.path, state.project, body.comment or "ohne Kommentar")
        return {"id": vid}

    @app.get("/api/versions/{vid}/diff")
    def version_diff(vid: int):
        from ..project import versions
        try:
            old = versions.get(state.path, vid, state.project.products)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        return versions.diff(old, state.project)

    @app.post("/api/versions/{vid}/restore")
    def version_restore(vid: int):
        from ..project import versions
        try:
            old = versions.get(state.path, vid, state.project.products)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None

        def do():
            for f in ("name", "lines", "devices", "ranges", "group_addresses", "spaces"):
                setattr(state.project, f, getattr(old, f))

        state.ops.run(f"Version {vid} wiederherstellen", do)
        state.dirty = True
        return project_info()

    @app.post("/api/project/save")
    def save():
        state.project.save(state.path)
        state.dirty = False
        return {"saved": str(state.path.name)}

    @app.get("/api/ga/{main}/{middle}/{sub}")
    def ga(main: int, middle: int, sub: int):
        address = f"{main}/{middle}/{sub}"
        g = state.project.group_addresses.get(address)
        if g is None:
            raise HTTPException(404, f"Gruppenadresse {address} nicht im Projekt")
        users = []
        for d in state.project.devices:
            for link in d.links:
                if address in link.gas:
                    app = state.app(d)
                    key = _catalog_key(app, link.ref) if app else None
                    v = app.variant_by_ref.get(key) if app and key else None
                    users.append({"uid": d.uid, "pa": d.pa, "device": d.name or state.product_name(d), "ref": link.ref,
                                  "number": app.ref_number.get(key) if app and key else None,
                                  "object": " – ".join(x for x in (v.text, v.function) if x) if v else link.ref,
                                  "sending": link.gas[0] == address,
                                  "dpt": _dpt_text(_dpt(link.extra.get("DatapointType", "")) or (v.dpt if v else None)),
                                  "size": v.size if v else "",
                                  "flags": _flag_text(v.flags) if v else "",
                                  "priority": {"Low": "Niedrig", "High": "Hoch", "Alert": "Alarm"}.get(v.priority, v.priority) if v else ""})
        return {"address": address, "name": g.name, "dpt": g.dpt, "description": g.description,
                "central": g.extra.get("Central") == "true", "users": users}

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(STATIC / "index.html")

    return app
