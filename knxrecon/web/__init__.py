"""Weboberfläche: zeigt gespeicherte Rekonstruktionsdaten, ergänzt Namen.

Greift nie auf den Bus zu. Scans und Lesevorgänge laufen über die CLI.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from xknx.telegram import GroupAddress

from .. import compare, decode, device, reference, traffic
from ..model import Device
from ..report import MASK_NAMES
from ..store import Store

HERE = Path(__file__).parent
NOTE_FIELDS = {"ga": {"name"}, "device": {"name", "room"}}


def _pa_key(pa: str) -> list[int]:
    return [int(x) for x in pa.split(".")]


@dataclass
class Match:
    ref: reference.RefDevice | None = None
    hit: set = field(default_factory=set)
    only_bus: set = field(default_factory=set)
    only_ref: set = field(default_factory=set)


@dataclass
class State:
    db_path: str
    ref_path: str | None
    devices: dict[str, Device] = field(default_factory=dict)
    refs: dict[str, reference.RefDevice] = field(default_factory=dict)
    names: reference.RefNames = field(default_factory=reference.RefNames)
    matches: dict[str, Match] = field(default_factory=dict)

    def store(self) -> Store:
        # eigene Verbindung je Anfrage: sqlite3 erlaubt keine Thread-Wechsel
        return Store(self.db_path)

    def load(self) -> None:
        store = self.store()
        decoded = [decode.decode_device(store, pa, m) for pa, m in store.found_devices() if device.supported(m)]
        self.devices = {d.pa: d for d in decoded if d.app_id or d.links}
        self.matches = {}
        if self.ref_path:
            self.refs = reference.load(self.ref_path)
            self.names = reference.load_names(self.ref_path)
            for b, r, _ in compare.match_devices(list(self.devices.values()), self.refs):
                m = Match(ref=r)
                if r:
                    m.hit, m.only_bus, m.only_ref = b.links & r.links, b.links - r.links, r.links - b.links
                self.matches[b.pa] = m

    def ref_of(self, pa: str) -> reference.RefDevice | None:
        m = self.matches.get(pa)
        return m.ref if m else None

    def ga_index(self) -> dict[str, list[tuple[Device, int]]]:
        """GA → [(Gerät, Objektnummer)] über alle Busgeräte."""
        out: dict[str, list[tuple[Device, int]]] = {}
        for d in self.devices.values():
            for num, ga in sorted(d.links):
                out.setdefault(ga, []).append((d, num))
        return dict(sorted(out.items(), key=lambda kv: GroupAddress(kv[0]).raw))

    def dpt_for(self, ga: str, size: str | None) -> tuple[dict | None, str]:
        """DPT für die Wertanzeige: Referenz, sonst Vorschlag aus der Nutzlastgröße."""
        if ga in self.names.dpt:
            return self.names.dpt[ga], "Referenz"
        guess = traffic.SIZE_GUESS.get(size or "", ())
        return ({"main": guess[0], "sub": None}, "Vorschlag") if guess else (None, "")

    def co_text(self, pa: str, number: int) -> str:
        r = self.ref_of(pa)
        return self.names.co.get((r.pa, number), "") if r else ""


def _time(ts: float | None, with_date: bool = False) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M:%S" if with_date else "%H:%M:%S")


class NoteIn(BaseModel):
    kind: str
    key: str
    field: str
    value: str


def create_app(db_path: str, ref_path: str | None = None) -> FastAPI:
    state = State(db_path, ref_path)
    state.load()
    app = FastAPI(title="KNX-Rekonstruktion", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        mask_name=lambda m: MASK_NAMES.get(m, "?"), has_ref=bool(ref_path), time=_time, dpt_names=traffic.DPT_NAMES
    )

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        store = state.store()
        session = store.db.execute("SELECT started, gateway, note FROM session ORDER BY id DESC LIMIT 1").fetchone()
        devices = sorted(state.devices.values(), key=lambda d: _pa_key(d.pa))
        return render(
            request, "overview.html",
            devices=devices, notes=store.notes("device"), matches=state.matches,
            session=session, n_links=sum(len(d.links) for d in devices), n_gas=len(state.ga_index()),
            n_problems=sum(1 for d in devices if d.problems),
        )

    @app.get("/device/{pa}", response_class=HTMLResponse)
    def device_page(request: Request, pa: str):
        d = state.devices.get(pa)
        if d is None:
            raise HTTPException(404, f"Gerät {pa} nicht im Bestand")
        objs = {o.number: o for o in d.objects}
        by_obj: dict[int, list[str]] = {}
        for num, ga in d.links:
            by_obj.setdefault(num, []).append(ga)
        numbers = sorted(set(by_obj) | {o.number for o in d.objects if o.communication})
        rows = [
            {"number": n, "obj": objs.get(n), "gas": sorted(by_obj.get(n, []), key=lambda g: GroupAddress(g).raw),
             "text": state.co_text(pa, n)}
            for n in numbers
        ]
        return render(
            request, "device.html", d=d, rows=rows, match=state.matches.get(pa),
            note=state.store().notes("device").get(pa, {}), ga_notes=state.store().notes("ga"),
            ref_names=state.names.ga,
        )

    @app.get("/gas", response_class=HTMLResponse)
    def gas_page(request: Request, q: str = ""):
        return render(
            request, "gas.html", index=state.ga_index(), notes=state.store().notes("ga"),
            ref_names=state.names.ga, q=q, co_text=state.co_text,
        )

    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request):
        if not ref_path:
            raise HTTPException(404, "Kein Referenzprojekt geladen (--reference)")
        matched = {m.ref.pa for m in state.matches.values() if m.ref}
        missing = [r for pa, r in sorted(state.refs.items(), key=lambda kv: _pa_key(kv[0])) if pa not in matched]
        devices = sorted(state.devices.values(), key=lambda d: _pa_key(d.pa))
        totals = {
            "hit": sum(len(m.hit) for m in state.matches.values()),
            "only_bus": sum(len(m.only_bus) for m in state.matches.values()),
            "only_ref": sum(len(m.only_ref) for m in state.matches.values()),
        }
        return render(request, "compare.html", devices=devices, matches=state.matches, missing=missing,
                      totals=totals, ref_names=state.names.ga)

    @app.get("/traffic", response_class=HTMLResponse)
    def traffic_page(request: Request):
        store = state.store()
        t = traffic.analyse(store.db, state.devices)
        rows = []
        for ga, g in sorted(t.gas.items(), key=lambda kv: GroupAddress(kv[0]).raw):
            dpt, source = state.dpt_for(ga, g.size)
            rows.append({"g": g, "dpt": dpt, "dpt_source": source,
                         "value": traffic.format_value(g.last_payload, g.last_bits, dpt)})
        return render(request, "traffic.html", t=t, rows=rows, notes=store.notes("ga"), ref_names=state.names.ga,
                      devices=state.devices)

    @app.get("/api/telegrams")
    def telegrams(after: int = 0, limit: int = 50):
        """Neueste Telegramme für die Live-Liste (nur lesen aus der DB)."""
        store = state.store()
        limit = max(1, min(limit, 200))
        cur = store.db.execute(
            "SELECT id, ts, src, dst, service, payload, bits FROM telegram WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after, limit),
        )
        notes = store.notes("ga")
        out = []
        for id_, ts, src, dst, service, payload, bits in cur:
            dpt, source = state.dpt_for(dst, traffic.size_of(payload, bits, bits or 0))
            out.append({
                "id": id_, "time": _time(ts), "src": src, "dst": dst,
                "src_known": src in state.devices,
                "name": notes.get(dst, {}).get("name") or state.names.ga.get(dst, ""),
                "service": {"GroupValueWrite": "Schreiben", "GroupValueResponse": "Antwort",
                            "GroupValueRead": "Lesen"}.get(service, service),
                "value": traffic.format_value(payload, bits, dpt), "dpt_source": source,
            })
        return {"telegrams": out}

    @app.post("/api/note")
    def set_note(note: NoteIn):
        if note.field not in NOTE_FIELDS.get(note.kind, set()):
            raise HTTPException(400, "Unbekanntes Feld")
        if len(note.value) > 200:
            raise HTTPException(400, "Zu lang")
        state.store().set_note(note.kind, note.key, note.field, note.value)
        return {"ok": True}

    @app.post("/api/reference-names")
    def adopt_reference_names():
        """Leere GA-Namen mit Namen aus dem Referenzprojekt füllen. Vorhandene bleiben."""
        store = state.store()
        existing = store.notes("ga")
        n = 0
        for ga in state.ga_index():
            if ga in state.names.ga and not existing.get(ga, {}).get("name"):
                store.set_note("ga", ga, "name", state.names.ga[ga])
                n += 1
        return RedirectResponse(f"/gas?adopted={n}", status_code=303)

    @app.post("/api/reload")
    def reload():
        state.load()
        return RedirectResponse("/", status_code=303)

    @app.get("/export/gruppenadressen.csv")
    def export_gas():
        notes = state.store().notes("ga")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Gruppenadresse", "Name", "Verknüpfungen"])
        for ga, uses in state.ga_index().items():
            w.writerow([ga, notes.get(ga, {}).get("name", ""), " ".join(f"{d.pa}/{n}" for d, n in uses)])
        return Response(
            buf.getvalue().encode("utf-8-sig"),  # BOM, damit Excel Umlaute richtig liest
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="gruppenadressen.csv"'},
        )

    return app
