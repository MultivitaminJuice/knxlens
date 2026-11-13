"""Gerät: Details, Parameter, verwaiste Verknüpfungen, Befehle, Rückgängig."""

from __future__ import annotations

from fastapi import HTTPException

from ..project.edit import (
    EditError,
    orphans,
    param_change_effect,
    rebind_variants,
    set_param,
    unlink_orphans,
)
from ..project.ops import (
    OpError,
)
from .state import OPS, OpIn, ParamIn, UidsIn, device_detail


def register(app, ctx) -> None:
    state = ctx.state

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
