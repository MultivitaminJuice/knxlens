"""Gruppenmonitor."""

from __future__ import annotations

from fastapi import HTTPException

from ..project.reconstruct import catalog_key as _catalog_key
from ..traffic import format_value
from .busmon import SERVICES
from .state import MonitorIn, _dpt


def register(app, ctx) -> None:
    state, monitor, gateway = ctx.state, ctx.monitor, ctx.gateway

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
                "error": monitor.error, "route_back": monitor.route_back,
                "route_back_why": monitor.route_back_why, "rows": rows}

    @app.post("/api/monitor/start")
    async def monitor_start(body: MonitorIn):
        if not body.gateway.strip():
            raise HTTPException(422, "IP-Adresse des KNX-IP-Interfaces fehlt")
        await monitor.start(body.gateway.strip(), body.route_back)
        return {"state": monitor.state}

    @app.post("/api/monitor/stop")
    async def monitor_stop():
        await monitor.stop()
        return {"state": monitor.state}

    @app.post("/api/monitor/clear")
    def monitor_clear():
        monitor.clear()
        return {"ok": True}
