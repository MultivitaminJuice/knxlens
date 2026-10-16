"""Inventarbericht ohne Referenzprojekt (für die Zielanlage)."""

from __future__ import annotations

from collections import defaultdict

from xknx.telegram import GroupAddress

from .model import Device

MASK_NAMES = {
    0x0010: "BCU1", 0x0011: "BCU1", 0x0012: "BCU1", 0x0013: "BCU1",
    0x0020: "BCU2", 0x0021: "BCU2", 0x0025: "BCU2",
    0x0701: "System 7", 0x0705: "System 7", 0x07B0: "System B",
}


def _pa_key(pa: str) -> list[int]:
    return [int(x) for x in pa.split(".")]


def inventory(devices: list[Device]) -> str:
    out = ["# KNX-Inventar\n", "## Geräte\n",
           "| PA | Maske | Applikation | GAs | Verknüpfungen | Hinweise |", "|---|---|---|---|---|---|"]
    for d in sorted(devices, key=lambda d: _pa_key(d.pa)):
        mask = f"{d.mask:#06x} {MASK_NAMES.get(d.mask, '?')}" if d.mask is not None else "?"
        out.append(f"| {d.pa} | {mask} | {d.app_id or '?'} | {len(d.group_addresses)} | {len(d.links)} | {'; '.join(d.problems)} |")

    out += ["\n## Gruppenadressen\n", "| GA | Gerät/Objekt (Flags, Größe) |", "|---|---|"]
    by_ga: dict[str, list[str]] = defaultdict(list)
    for d in devices:
        objs = {o.number: o for o in d.objects}
        for num, ga in d.links:
            o = objs.get(num)
            detail = f" ({o.flags}, {o.size})" if o else ""
            by_ga[ga].append(f"{d.pa}/{num}{detail}")
    for ga in sorted(by_ga, key=lambda g: GroupAddress(g).raw):
        out.append(f"| {ga} | {'<br>'.join(sorted(by_ga[ga], key=lambda s: _pa_key(s.split('/')[0])))} |")
    return "\n".join(out) + "\n"
