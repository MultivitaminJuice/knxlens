"""Regressionsprüfung gegen die echten Geräte (`knxlens verify`).

Misst je Gerät, wie gut die Berechnung zum gelesenen Gerätespeicher passt, und vergleicht mit
gespeicherten Sollwerten. Schlägt fehl, sobald ein Gerät in einer Kennzahl schlechter wird –
auch wenn die Summe gleich bleibt. Grundlage sind die Rohdaten der Messdatenbank (data/,
nicht im Repo); deshalb läuft das lokal und ergänzt die Unit-Tests, ersetzt sie nicht.

Kennzahlen (je Gerät, „gleich“ darf nicht sinken, „abweichend“ nicht steigen):
  params    Parameterbytes Abbild ↔ Gerät
  tables    Adress-, Assoziations-, Objekttabelle gleich
  ets       Abbild ↔ ETS-Ladeabbild (wo vorhanden)
  sim       simulierter Komplett-Download ↔ Gerät (geschriebene Bytes)
  read      Leseseite: aus dem Gerät gelesene Parameter ↔ Projektwerte
"""

from __future__ import annotations

import json
from pathlib import Path

# Kennzahl → (Schlüssel „gut“, Schlüssel „schlecht“)
METRICS = {
    "params": ("equal", "diff"),
    "tables": ("equal", "diff"),
    "ets": ("equal", "diff"),
    "sim": ("equal", "diff"),
    "read": ("equal", "diff"),
}


def measure(project_path: str | Path, db_path: str | Path, only: list[str] | None = None) -> dict[str, dict]:
    """Kennzahlen je physikalischer Adresse."""
    from .decode import Raw
    from .download import DownloadError, simulate_against_store
    from .image import check_against_store
    from .loadplan import device_plan
    from .param_compare import check
    from .params import AppParams, evaluate_from_store
    from .project import Project
    from .project.products import app_for, project_catalog
    from .project.reconstruct import object_number
    from .store import Store

    p = Project.load(project_path)
    cat = project_catalog(p)
    store = Store(db_path)
    masks = dict(store.found_devices())
    master = p.products.get("knx_master.xml")
    aps: dict[str, AppParams] = {}
    out: dict[str, dict] = {}
    for d in p.devices:
        if d.pa is None or d.pa not in masks or (only and d.pa not in only):
            continue
        app = app_for(cat, d.app_id)
        if app is None:
            continue
        ap = aps.get(app.key) or aps.setdefault(app.key, AppParams(app))
        number_of = (lambda link, a=app: object_number(a, link.ref))
        c = check_against_store(ap, d, store, masks[d.pa], number_of)
        tables = [x for x in (c.address_table, c.association_table) if x is not None]
        if c.object_table:
            tables.append(c.object_table[0] == c.object_table[1])
        m = {
            "params": {"equal": c.param_equal, "diff": len(c.param_diff)},
            "tables": {"equal": sum(tables), "diff": len(tables) - sum(tables)},
        }
        if c.ets_equal or c.ets_diff:
            m["ets"] = {"equal": c.ets_equal, "diff": len(c.ets_diff)}
        try:
            _, eq, ne = simulate_against_store(device_plan(ap, d, master, "all", number_of), store, d.pa)
            m["sim"] = {"equal": eq, "diff": ne}
        except DownloadError as e:
            m["sim"] = {"equal": 0, "diff": 0, "error": str(e)}
        ev = evaluate_from_store(ap, Raw(store, d.pa), {l.number for l in d.links if l.number is not None})
        r = check(ev, d.params)
        m["read"] = {"equal": r.equal, "diff": len(r.mismatches)}
        out[d.pa] = m
    return out


def totals(result: dict[str, dict]) -> dict[str, dict]:
    t: dict[str, dict] = {}
    for m in result.values():
        for name, v in m.items():
            s = t.setdefault(name, {"equal": 0, "diff": 0, "errors": 0})
            s["equal"] += v["equal"]
            s["diff"] += v["diff"]
            s["errors"] += 1 if v.get("error") else 0
    return t


def compare(old: dict[str, dict], new: dict[str, dict]) -> tuple[list[str], list[str]]:
    """→ (Verschlechterungen, Verbesserungen) als lesbare Zeilen."""
    worse, better = [], []
    for pa in sorted(set(old) | set(new), key=lambda a: [int(x) for x in a.split(".")]):
        if pa not in new:
            worse.append(f"{pa}: fehlt in der Messung")
            continue
        if pa not in old:
            better.append(f"{pa}: neu gemessen")
            continue
        for name, (good, bad) in METRICS.items():
            o, n = old[pa].get(name), new[pa].get(name)
            if o is None and n is None:
                continue
            if o is not None and n is None:
                worse.append(f"{pa} {name}: Kennzahl fehlt")
                continue
            if o is None:
                better.append(f"{pa} {name}: neu {n[good]} gleich / {n[bad]} abweichend")
                continue
            if n.get("error") and not o.get("error"):
                worse.append(f"{pa} {name}: Abbruch – {n['error']}")
            elif o.get("error") and not n.get("error"):
                better.append(f"{pa} {name}: läuft wieder durch")
            if n[good] < o[good] or n[bad] > o[bad]:
                worse.append(f"{pa} {name}: {o[good]}/{o[bad]} → {n[good]}/{n[bad]} (gleich/abweichend)")
            elif n[good] > o[good] or n[bad] < o[bad]:
                better.append(f"{pa} {name}: {o[good]}/{o[bad]} → {n[good]}/{n[bad]}")
    return worse, better


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def save(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
