"""Bearbeiten eines Projekts. Alle Regeln hier im Kern, die Oberfläche ruft nur auf."""

from __future__ import annotations

import re

from .. import params
from . import Device


class EditError(ValueError):
    """Ungültige Bearbeitung – Meldung ist für den Nutzer gedacht."""


def _param_key(instance_id: str) -> str:
    return re.sub(r"_R-\d+$", "", instance_id)


def set_param(ap: params.AppParams, device: Device, instance_id: str, text: str | None) -> bool:
    """Parameter setzen (text=None: auf Standard zurücksetzen). Liefert True, wenn sich etwas geändert hat.

    Wie die ETS speichert das Projekt nur vom Standard abweichende Werte; ältere Einträge
    desselben Parameters unter anderer Ref-Id werden ersetzt.
    """
    ev = ap.evaluate_values(device.params)
    visible = next((v for v in ev.values if v.instance_id == instance_id), None)
    if visible is None:
        raise EditError("Parameter ist derzeit nicht sichtbar")
    if visible.internal:
        raise EditError("Parameter ist nicht änderbar")
    ref = ap.refs[visible.ref_id]
    key = _param_key(instance_id)
    before = dict(device.params)
    for k in [k for k in device.params if _param_key(k) == key]:
        del device.params[k]
    if text is not None:
        try:
            value, stored = params.validate(ref, text)
        except ValueError as e:
            device.params.clear()
            device.params.update(before)
            raise EditError(str(e)) from None
        if not params._same(value, ref.default):
            device.params[instance_id] = stored
    return device.params != before


def editor_item(ap: params.AppParams, v: params.Value) -> dict:
    """Beschreibung eines sichtbaren Parameters für ein Eingabefeld."""
    ref = ap.refs[v.ref_id]
    t = ref.param.ptype
    item = {
        "id": v.instance_id, "text": v.text, "display": v.display, "value": v.value,
        "default": params._default(ref)[1], "changed": v.from_project and not v.is_default,
        "kind": t.kind, "editable": not v.internal and ref.access not in ("None", "Read") and t.kind != "none",
        "suffix": ref.param.suffix,
    }
    if t.kind == "enum":
        item["options"] = [{"value": k, "label": text} for k, text in t.enums.items()]
        item["checkbox"] = t.ui == "CheckBox" and set(t.enums) == {0, 1}
    elif t.kind in ("number", "time", "float"):
        signed_lo = -(1 << (t.bits - 1)) if t.signed else 0
        signed_hi = (1 << (t.bits - 1)) - 1 if t.signed else (1 << t.bits) - 1
        item["min"] = t.minv if t.minv is not None else (signed_lo if t.kind != "float" else None)
        item["max"] = t.maxv if t.maxv is not None else (signed_hi if t.kind != "float" else None)
        item["checkbox"] = t.ui == "CheckBox"
        if t.kind == "time":
            item["suffix"] = item["suffix"] or {"Seconds": "s", "Minutes": "min", "Hours": "h", "Milliseconds": "ms",
                                                "PackedDaysHoursMinutesAndSeconds": "s"}.get(t.unit, t.unit)
    elif t.kind == "text":
        item["maxlength"] = t.bits // 8
    return item
