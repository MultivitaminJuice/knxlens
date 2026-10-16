"""Gelesene Parameter gegen die Parameterwerte eines ETS-Projekts prüfen."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field

from defusedxml import ElementTree as ET

from .catalog import _children
from .params import Evaluation, _same


def project_values(knxproj: str) -> dict[str, dict[str, str]]:
    """PA → {ParameterInstanceRef-Id: Wert}. Das Projekt speichert nur vom Standard abweichende Werte."""
    with zipfile.ZipFile(knxproj) as z:
        name = next(n for n in z.namelist() if re.match(r"P-[0-9A-F]+/0\.xml$", n))
        root = ET.fromstring(z.read(name))
    out: dict[str, dict[str, str]] = {}
    for area in _children(root, "Area"):
        for line in (c for c in area if c.tag.endswith("Line")):
            for dev in _children(line, "DeviceInstance"):
                if dev.get("Address") is None:
                    continue
                pa = f"{area.get('Address')}.{line.get('Address')}.{dev.get('Address')}"
                out[pa] = {r.get("RefId"): r.get("Value", "") for r in _children(dev, "ParameterInstanceRef")}
    return out


@dataclass
class ParamCheck:
    visible: int = 0
    from_device: int = 0
    equal: int = 0
    only_ets: int = 0  # ohne Speicherort
    mismatches: list[tuple[str, str, str, str]] = field(default_factory=list)  # (Text, gelesen, erwartet, Id)
    project_hidden: int = 0  # Projektwert für nicht sichtbaren/unbekannten Parameter
    internal: int = 0  # versteckte Hilfsparameter, nicht verglichen
    untouched_mismatch: int = 0  # Abweichung, aber Speicher = Standardbild (als unsicher markiert)


def check(ev: Evaluation, values: dict[str, str]) -> ParamCheck:
    r = ParamCheck()
    by_param = {re.sub(r"_R-\d+$", "", k): val for k, val in values.items()}
    seen = set()
    for v in ev.values:
        seen.add(v.param_key)
        if v.internal:
            r.internal += 1
            continue
        r.visible += 1
        if not v.from_device:
            r.only_ets += 1
            continue
        r.from_device += 1
        expected = by_param.get(v.param_key, v.default)
        if _same(v.value, expected):
            r.equal += 1
        elif v.untouched:
            r.untouched_mismatch += 1
        else:
            r.mismatches.append((" / ".join(v.path[-1:] + (v.text,)), v.display or str(v.value), expected, v.instance_id))
    r.project_hidden = sum(1 for k in by_param if k not in seen)
    return r
