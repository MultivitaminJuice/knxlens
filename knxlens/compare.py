"""Rekonstruktion gegen Referenzprojekt abgleichen."""

from __future__ import annotations

from .reference import RefDevice
from .store import Store


def _key(pa: str) -> list[int]:
    return [int(x) for x in pa.split(".")]


def compare_presence(store: Store, ref: dict[str, RefDevice]) -> str:
    """Markdown: welche Geräte gibt es am Bus, welche im Projekt."""
    found = dict(store.found_devices())
    refused = {
        pa for (pa,) in store.db.execute(
            """SELECT pa FROM probe p WHERE result='refused' AND ts = (
                   SELECT MAX(ts) FROM probe q WHERE q.pa = p.pa)"""
        )
    }
    rows = ["| PA | Bus | Referenz |", "|---|---|---|"]
    both = only_bus = only_ref = 0
    for pa in sorted(set(found) | set(ref) | refused, key=_key):
        on_bus = f"0x{found[pa]:04X}" if pa in found else ("belegt (Tunnel?)" if pa in refused else "–")
        in_ref = ref[pa].name[:45] if pa in ref else "–"
        if pa in found and pa in ref:
            both += 1
        elif pa in found:
            only_bus += 1
        elif pa in ref:
            only_ref += 1
        rows.append(f"| {pa} | {on_bus} | {in_ref} |")
    head = (
        f"**Am Bus und im Projekt:** {both} · **nur am Bus:** {only_bus} · "
        f"**nur im Projekt:** {only_ref} · belegt ohne Verbindungsdienst: {len(refused)}\n\n"
    )
    return head + "\n".join(rows)


def match_devices(bus: list, ref: dict[str, RefDevice]) -> list[tuple]:
    """Busgeräte inhaltlich Referenzgeräten zuordnen (Applikation + GA-Überschneidung).

    Rückgabe: Liste (bus_device, ref_device | None, score). Gierig nach bestem Score,
    jedes Referenzgerät höchstens einmal.
    """
    pairs = []
    for b in bus:
        b_gas = {ga for _, ga in b.links} | set(b.group_addresses)
        for r in ref.values():
            if b.app_manufacturer is None or (r.manufacturer, r.app_number) != (b.app_manufacturer, b.app_number):
                continue
            r_gas = {ga for _, ga in r.links}
            union = b_gas | r_gas
            score = len(b_gas & r_gas) / len(union) if union else 0.0
            # gleiche PA als schwacher Gleichstand-Brecher
            pairs.append((score + (0.001 if r.pa == b.pa else 0), b.pa, r.pa))
    pairs.sort(reverse=True)
    used_b, used_r, assign = set(), set(), {}
    for score, bpa, rpa in pairs:
        if bpa in used_b or rpa in used_r:
            continue
        used_b.add(bpa)
        used_r.add(rpa)
        assign[bpa] = (rpa, score)
    out = []
    for b in bus:
        rpa, score = assign.get(b.pa, (None, 0.0))
        out.append((b, ref.get(rpa) if rpa else None, score))
    return out


def compare_links(bus: list, ref: dict[str, RefDevice]) -> str:
    """Markdown-Bericht: Zuordnung und Verknüpfungs-Treffer pro Gerät."""
    rows = [
        "| Bus-PA | Applikation | Referenz-PA | Referenzgerät | Treffer | nur Bus | nur Ref | Hinweise |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tp = fp = fn = 0
    matched_ref = set()
    for b, r, _ in sorted(match_devices(bus, ref), key=lambda x: _key(x[0].pa)):
        if r is None:
            rows.append(f"| {b.pa} | {b.app_id or '?'} | – | – | – | {len(b.links)} | – | {'; '.join(b.problems)} |")
            continue
        matched_ref.add(r.pa)
        t, f, n = len(b.links & r.links), len(b.links - r.links), len(r.links - b.links)
        tp, fp, fn = tp + t, fp + f, fn + n
        moved = "" if r.pa == b.pa else "umadressiert"
        version = "" if r.app_version == b.app_version else f"Version {r.app_version:02X}→{b.app_version:02X}"
        notes = "; ".join(x for x in (moved, version, *b.problems) if x)
        rows.append(f"| {b.pa} | {b.app_id} | {r.pa} | {r.name[:35]} | {t} | {f} | {n} | {notes} |")
    missing = [r for pa, r in ref.items() if pa not in matched_ref]
    total = tp + fn
    head = (
        f"**Verknüpfungen (zugeordnete Geräte):** {tp} von {total} der Referenz gefunden "
        f"({100 * tp / total:.1f} %), {fp} zusätzlich am Bus.\n\n"
        f"**Referenzgeräte ohne Gegenstück am Bus:** "
        + (", ".join(f"{r.pa} {r.name[:30]}" for r in missing) or "keine")
        + "\n\n"
    ) if total else ""
    return head + "\n".join(rows)
