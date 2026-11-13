"""Bäume (Topologie, Gebäude, Gruppenadressen), Gruppenadresse, Katalog und Produkte."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..project.ops import (
    SPACE_CHILDREN,
    SPACE_TYPES,
    load_state,
    needs_download,
)
from ..project.ops import ga_raw as GroupAddressRaw
from ..project.products import (
    MAX_PRODUCT_FILE,
    ProductError,
    add_products,
    manufacturer_names,
)
from ..project.reconstruct import catalog_key as _catalog_key
from .state import MEDIUM, _dpt, _dpt_text, _flag_text


def register(app, ctx) -> None:
    state = ctx.state

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
