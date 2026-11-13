"""Editor: JSON-API für die Vue-Oberfläche plus Auslieferung der gebauten Oberfläche.

Die gesamte KNX-Logik bleibt im Kern (Sichtbarkeit, Objekte, Kodierung); die Oberfläche zeigt nur
an und schickt Änderungen. Hier: App bauen, Schutz und Sperre; die Endpunkte liegen nach Bereichen
in routes_*.py, gemeinsamer Zustand in state.py. Bus nur lesend (Monitor, Gerät lesen).
"""

from __future__ import annotations

import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import websafety
from ..project import Project
from ..project.ops import Editor
from ..websafety import LOOPBACK
from . import routes_device, routes_download, routes_monitor, routes_project, routes_tree
from .busmon import BusMonitor
from .state import STATIC, Ctx, EditorState, _remember, find_store

__all__ = ["create_editor_app", "find_store"]


def create_editor_app(project_path: str, preload: bool = True, gateway: str = "", db: str | None = None,
                      hosts: set[str] | None = LOOPBACK) -> FastAPI:
    path = Path(project_path)
    db_path = Path(db) if db else find_store(path)
    state = EditorState(path, Project.load(path))
    state.ops = Editor(state.project)
    _remember(path)
    monitor = BusMonitor()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await monitor.stop()  # Tunnelverbindung beim Beenden sauber abbauen

    app = FastAPI(title="knxlens Editor", docs_url=None, redoc_url=None, lifespan=lifespan)
    websafety.install(app, hosts)  # nur eigene Seite darf ändern, kein DNS-Rebinding

    # Projekt-Anfragen nacheinander: synchrone Endpunkte laufen in Threads und teilen sich state.
    # Ausgenommen: Gruppenmonitor (häufiges Abfragen) und das lange Lesen vom Bus.
    # Threading-Sperre, im Thread erworben: blockiert die Ereignisschleife nicht und gilt über
    # Schleifen hinweg (Testclient: eine Schleife je Anfrage).
    api_lock = threading.Lock()
    unlocked = re.compile(r"^/api/(monitor|device/[^/]+/read$)")

    if preload:
        # Produktdaten im Hintergrund einlesen (einige Sekunden): Der Server und die Seite sind
        # sofort da; Projekt-Anfragen warten an der Sperre, bis alles geladen ist.
        def load_products():
            with api_lock:
                _ = state.catalog
                for d in state.project.devices:  # Parameterteil der verwendeten Applikationen
                    if (app_ := state.app(d)) is not None:
                        state.app_params(app_)

        threading.Thread(target=load_products, name="knxlens-produktdaten", daemon=True).start()

    @asynccontextmanager
    async def locked():
        await anyio.to_thread.run_sync(api_lock.acquire)
        try:
            yield
        finally:
            api_lock.release()

    @app.middleware("http")
    async def serialize(request, call_next):
        if request.url.path.startswith("/api/") and not unlocked.match(request.url.path):
            async with locked():
                return await call_next(request)
        return await call_next(request)

    app.state.monitor = monitor
    ctx = Ctx(state, monitor, gateway, db_path, locked)
    for mod in (routes_download, routes_monitor, routes_project, routes_tree, routes_device):
        mod.register(app, ctx)

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(STATIC / "index.html")

    return app
