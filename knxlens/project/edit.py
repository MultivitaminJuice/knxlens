"""Bearbeiten eines Projekts. Alle Regeln hier im Kern, die Oberfläche ruft nur auf."""

from __future__ import annotations


from .. import params
from . import Device


class EditError(ValueError):
    """Ungültige Bearbeitung – Meldung ist für den Nutzer gedacht."""


def set_param(ap: params.AppParams, device: Device, instance_id: str, text: str | None) -> bool:
    """Parameter setzen (text=None: auf Standard zurücksetzen). Liefert True, wenn sich etwas geändert hat.

    Wie die ETS speichert das Projekt nur vom Standard abweichende Werte, und zwar an der Ref:
    andere Refs desselben Parameters behalten ihre eigenen Werte.
    """
    ev = ap.evaluate_values(device.params)
    visible = next((v for v in ev.values if v.instance_id == instance_id), None)
    if visible is None:
        raise EditError("Parameter ist derzeit nicht sichtbar")
    if visible.internal:
        raise EditError("Parameter ist nicht änderbar")
    ref = ap.refs[visible.ref_id]
    before = dict(device.params)
    device.params.pop(instance_id, None)
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


# --- Verknüpfungen an inaktiven Objekten ("verwaist") ------------------------------------------


def _key(ap: params.AppParams, rel: str) -> str:
    from .reconstruct import catalog_key
    return catalog_key(ap.app, rel)


def orphans(ap: params.AppParams, device: Device, ev: params.Evaluation | None = None) -> list[dict]:
    """Verknüpfungen, deren Objekt bei den aktuellen Parametern nicht existiert."""
    ev = ev or ap.evaluate_values(device.params)
    active_numbers = set(ev.active_cos)
    out = []
    for link in device.links:
        number = ap.app.ref_number.get(_key(ap, link.ref), link.number)
        if number is None or number in active_numbers or not link.gas:
            continue
        v = ap.app.variant_by_ref.get(_key(ap, link.ref))
        out.append({"ref": link.ref, "number": number, "gas": list(link.gas),
                    "text": " – ".join(x for x in ((v.text, v.function) if v else (link.ref,)) if x)})
    return out


def rebind_variants(ap: params.AppParams, device: Device, ev: params.Evaluation | None = None) -> int:
    """Hängt Verknüpfungen auf die aktive Variante desselben Objekts um (z. B. andere Größe/DPT).

    Gibt die Zahl der umgehängten Verknüpfungen zurück. Die Gruppenadressen bleiben erhalten.
    """
    from .reconstruct import rel_ref
    ev = ev or ap.evaluate_values(device.params)
    moved = 0
    for link in list(device.links):
        number = ap.app.ref_number.get(_key(ap, link.ref), link.number)
        active = ev.active_cos.get(number) if number is not None else None
        if active is None or _key(ap, link.ref) == active:
            continue
        target = rel_ref(ap.app, active)
        other = next((l for l in device.links if l.ref == target), None)
        if other is None:
            link.ref = target
        else:
            other.gas += [g for g in link.gas if g not in other.gas]
            device.links.remove(link)
        moved += 1
    return moved


def unlink_orphans(ap: params.AppParams, device: Device) -> int:
    """Alle Verknüpfungen an inaktiven Objekten lösen; Anzahl gelöster GA-Zuordnungen."""
    gone = {o["ref"] for o in orphans(ap, device)}
    n = sum(len(l.gas) for l in device.links if l.ref in gone)
    device.links = [l for l in device.links if l.ref not in gone]
    return n


def param_change_effect(ap: params.AppParams, device: Device, instance_id: str, text: str | None) -> list[dict]:
    """Welche Verknüpfungen würden durch die Änderung neu verwaisen? (ohne etwas zu ändern)"""
    import copy
    trial = copy.deepcopy(device)
    set_param(ap, trial, instance_id, text)
    rebind_variants(ap, trial)
    before = {o["ref"] for o in orphans(ap, device)}
    return [o for o in orphans(ap, trial) if o["ref"] not in before]
