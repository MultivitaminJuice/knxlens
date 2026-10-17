"""Editor: JSON-API für die Vue-Oberfläche plus Auslieferung der gebauten Oberfläche.

Die gesamte KNX-Logik bleibt hier im Kern (Sichtbarkeit, Objekte, später Kodierung);
die Oberfläche zeigt nur an und schickt Änderungen. Kein Buszugriff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import params
from ..catalog import Application, Catalog
from ..project import Device, Project
from ..project.products import app_for, project_catalog

STATIC = Path(__file__).parent.parent / "web" / "static" / "editor"
MEDIUM = {"MT-0": "TP", "MT-5": "IP", "MT-1": "PL", "MT-2": "RF"}


@dataclass
class EditorState:
    path: Path
    project: Project
    _catalog: Catalog | None = None
    _app_params: dict[str, params.AppParams] = field(default_factory=dict)

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

    def device(self, uid: str) -> Device:
        d = next((d for d in self.project.devices if d.uid == uid), None)
        if d is None:
            raise HTTPException(404, f"Gerät {uid} nicht im Projekt")
        return d

    def product_name(self, d: Device) -> str:
        app = self.app(d)
        if app is None:
            return d.product_ref
        for (pid, _), (name, order) in zip(app.product_refs, app.products):
            if pid == d.product_ref:
                return name
        return app.products[0][0] if app.products else app.name


def _catalog_key(app: Application, rel_ref: str) -> str:
    """Relative ComObjectRef (wie im Projekt) → Schlüssel in Application.variant_by_ref."""
    full = app.full_id
    if "_MI-" in rel_ref:
        inst, rest = rel_ref.split("_MI-1_", 1)
        md = inst.rsplit("_M-", 1)[0]
        return f"{full}_{inst}|{full}_{md}_{rest}"
    return f"{full}_{rel_ref}"


def device_detail(state: EditorState, d: Device) -> dict:
    app = state.app(d)
    ga_names = {g.address: g.name for g in state.project.group_addresses.values()}
    out = {
        "uid": d.uid, "pa": d.pa, "name": d.name, "description": d.description, "origin": d.origin,
        "product": state.product_name(d), "app_id": d.app_id, "has_products": app is not None,
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
            "number": number, "text": v.text, "function": v.function, "size": v.size,
            "dpt": f"{v.dpt['main']}.{v.dpt['sub']:03d}" if v.dpt and v.dpt.get("sub") is not None
            else (str(v.dpt["main"]) if v.dpt else ""),
            "flags": "".join(c if v.flags.get(k) else "-" for c, k in zip(
                "KLSÜA", ("communication", "read", "write", "transmit", "update"))),
            "active": key in ev.active_cos.values(),
            "gas": [{"address": g, "name": ga_names.get(g, "")} for g in linked.get(key, [])],
        })
    out["objects"] = sorted(objects, key=lambda o: o["number"])

    groups: dict[tuple, list] = {}
    for v in ev.values:
        if v.internal:
            continue
        groups.setdefault(v.path, []).append({
            "id": v.instance_id, "text": v.text, "display": v.display, "changed": v.from_project and not v.is_default,
            "from_project": v.from_project,
        })
    out["params"] = [{"path": list(path), "items": items} for path, items in groups.items()]
    return out


def create_editor_app(project_path: str) -> FastAPI:
    path = Path(project_path)
    state = EditorState(path, Project.load(path))
    app = FastAPI(title="knxlens Editor", docs_url=None, redoc_url=None)

    @app.get("/api/project")
    def project_info():
        p = state.project
        return {"name": p.name, "file": path.name, "project_id": p.project_id, "devices": len(p.devices),
                "group_addresses": len(p.group_addresses), "links": sum(len(l.gas) for d in p.devices for l in d.links)}

    @app.get("/api/tree/topology")
    def topology():
        p = state.project
        areas: dict[int, dict] = {}
        for ln in sorted(p.lines, key=lambda l: (l.area, l.line)):
            area = areas.setdefault(ln.area, {"key": f"a{ln.area}", "label": f"{ln.area} Bereich",
                                              "icon": "pi pi-sitemap", "data": {"type": "area"}, "children": []})
            devices = [d for d in p.devices if (d.area, d.line) == (ln.area, ln.line)]
            area["children"].append({
                "key": f"l{ln.area}.{ln.line}", "label": f"{ln.area}.{ln.line} {ln.name or 'Linie'}",
                "icon": "pi pi-share-alt", "data": {"type": "line", "medium": MEDIUM.get(ln.medium, ln.medium)},
                "children": [{
                    "key": d.uid, "label": f"{d.pa or '–'} {d.name or state.product_name(d)}",
                    "icon": "pi pi-box", "leaf": True, "data": {"type": "device", "uid": d.uid},
                } for d in sorted(devices, key=lambda d: (d.address is None, d.address or 0))],
            })
        return list(areas.values())

    @app.get("/api/tree/buildings")
    def buildings():
        p = state.project
        by_uid = {d.uid: d for d in p.devices}
        nodes: dict[str | None, list] = {}
        for s in p.spaces:
            node = {"key": s.uid, "label": s.name, "icon": "pi pi-home" if s.type in ("Building", "BuildingPart")
                    else "pi pi-th-large", "data": {"type": "space", "space_type": s.type}, "children": []}
            nodes.setdefault(s.parent, []).append(node)
            node["_devices"] = [by_uid[u] for u in s.devices if u in by_uid]

        def build(parent):
            out = []
            for n in nodes.get(parent, []):
                devs = n.pop("_devices")
                n["children"] = build(n["key"]) + [{
                    "key": f"{n['key']}/{d.uid}", "label": f"{d.pa or '–'} {d.name or state.product_name(d)}",
                    "icon": "pi pi-box", "leaf": True, "data": {"type": "device", "uid": d.uid}} for d in devs]
                out.append(n)
            return out

        return build(None)

    @app.get("/api/tree/groupaddresses")
    def group_addresses():
        p = state.project
        children: dict[str | None, list] = {}
        for r in p.ranges:
            children.setdefault(r.parent, []).append(
                {"key": r.uid, "label": r.name, "icon": "pi pi-folder", "data": {"type": "range"}, "children": []})
        for g in p.group_addresses.values():
            children.setdefault(g.range_uid, []).append({
                "key": f"ga:{g.address}", "label": f"{g.address} {g.name}", "icon": "pi pi-link", "leaf": True,
                "data": {"type": "ga", "address": g.address, "dpt": g.dpt}})

        def build(parent):
            out = []
            for n in children.get(parent, []):
                if n["data"]["type"] == "range":
                    n["children"] = build(n["key"])
                out.append(n)
            return out

        return build(None)

    @app.get("/api/device/{uid}")
    def device(uid: str):
        return device_detail(state, state.device(uid))

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
                    users.append({"uid": d.uid, "pa": d.pa, "device": d.name or state.product_name(d),
                                  "number": app.ref_number.get(key) if app and key else None,
                                  "object": " – ".join(x for x in (v.text, v.function) if x) if v else link.ref,
                                  "sending": link.gas[0] == address})
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
