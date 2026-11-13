"""Zustand des Editors: geöffnetes Projekt, Katalog-Zwischenspeicher, Eingabemodelle, Hilfen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from .. import params
from ..catalog import Application, Catalog
from ..project import Device, Project
from ..project.edit import (
    editor_item,
)
from ..project.ops import (
    MEDIA,
    Editor,
    load_state,
)
from ..project.products import (
    app_for,
    product_texts,
    project_catalog,
)
from ..project.reconstruct import catalog_key as _catalog_key
from ..project.reconstruct import rel_ref

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
        for (pid, _), (name, _order) in zip(app.product_refs, app.products):
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
    route_back: bool | None = None  # True = erzwingen, sonst automatisch


def _dpt(text: str) -> dict | None:
    """'DPST-9-1' / 'DPT-9' → {'main': 9, 'sub': 1} für die Wertanzeige."""
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
    route_back: bool | None = None  # True = erzwingen, sonst automatisch



@dataclass
class Ctx:
    """Was die Routen-Module gemeinsam brauchen."""
    state: EditorState
    monitor: object  # BusMonitor
    gateway: str
    db_path: Path
    locked: object  # async Kontextmanager: Projekt-Sperre
