"""Projekt: Kopfdaten, öffnen, neu, speichern (unter), Import/Export, Versionen."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from ..project import Project
from ..project.edit import (
    orphans,
)
from ..project.ops import (
    MEDIA,
    SPACE_CHILDREN,
    SPACE_TYPES,
)
from .state import CommentIn, NameIn, PathIn, _recent, _remember, _slug


def register(app, ctx) -> None:
    state = ctx.state

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
        import sqlite3

        from ..project.versions import copy_into
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
