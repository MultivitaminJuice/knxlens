"""Download-Vorschau: Plan, Abgleich mit dem Gerät, Gerät lesen (nur lesend)."""

from __future__ import annotations

from fastapi import HTTPException

from ..project import Device
from .busmon import shared_connection
from .state import ReadIn


def register(app, ctx) -> None:
    state, monitor, gateway, db_path, locked = ctx.state, ctx.monitor, ctx.gateway, ctx.db_path, ctx.locked

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
        from ..download import DownloadError, simulate_against_store
        from ..loadplan import device_plan
        try:
            steps = device_plan(state.app_params(app_), d, state.project.products.get("knx_master.xml"), "all",
                                lambda l: object_number(app_, l.ref))
            _, sim_eq, sim_ne = simulate_against_store(steps, st, d.pa)
            simulation = {"equal": sim_eq, "diff": sim_ne, "error": None}
        except DownloadError as e:
            simulation = {"equal": 0, "diff": 0, "error": str(e)}
        return {"read": True, "simulation": simulation, "last_read": last, "param_equal": c.param_equal, "param_diff": len(c.param_diff),
                "diffs": [{"segment": sid.rsplit("_", 1)[-1], "offset": off, "device": dv, "computed": cv, "params": own[:2]}
                          for sid, off, dv, cv, own in c.param_diff[:30]],
                "address_table": c.address_table, "association_table": c.association_table,
                "object_table": list(c.object_table) if c.object_table else None,
                "ets_image": [c.ets_equal, c.ets_equal + len(c.ets_diff)] if (c.ets_equal or c.ets_diff) else None}

    @app.post("/api/device/{uid}/read")
    async def device_read(uid: str, body: ReadIn):
        """Gerät auslesen – nur lesende Dienste (ReadOnlyGuard), danach Abgleich."""
        from xknx.telegram import IndividualAddress

        from .. import device as devmod
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
            async with shared_connection(monitor, gw, body.route_back) as xknx:
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
        async with locked():  # Abgleich liest das Projekt – wie die übrigen Anfragen gesperrt
            return device_check(uid)
