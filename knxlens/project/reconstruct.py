"""Rekonstruktion (Busdaten + Produktdaten + Parameterauswertung) → Projektmodell."""

from __future__ import annotations

import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from xknx.telegram import GroupAddress as _GA

from ..catalog import Application, Catalog
from ..model import Device as BusDevice
from ..params import Evaluation
from . import ComLink, Device, GroupAddress, GroupRange, Line, ModuleInstance, Project, Space


def from_reconstruction(
    name: str,
    devices: list[BusDevice],
    catalog: Catalog,
    evaluations: dict[str, Evaluation | None],
    ga_names: dict[str, str] | None = None,
    range_names: dict[str, str] | None = None,
    dpts: dict[str, dict] | None = None,
    device_notes: dict[str, dict[str, str]] | None = None,
    project_id: str = "P-0E7A",
) -> tuple[Project, dict]:
    ga_names, range_names, dpts, device_notes = ga_names or {}, range_names or {}, dpts or {}, device_notes or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    p = Project(
        name=name, project_id=project_id, namespace=_namespace(catalog),
        info={"GroupAddressStyle": "ThreeLevel", "LastModified": now, "ProjectStart": now,
              "Comment": "Rekonstruiert mit knxlens (rein lesend vom Bus)", "Guid": str(uuid.uuid4())},
        installation={"Name": "", "BCUKey": "4294967295"},
    )
    stats = {"devices": 0, "without_product": [], "objects_guessed": 0, "objects_unknown": []}
    pid = project_id
    ordered = sorted(devices, key=lambda d: [int(x) for x in d.pa.split(".")])

    for i, d in enumerate(ordered, start=1):
        area, line, addr = (int(x) for x in d.pa.split("."))
        if not any((l.area, l.line) == (area, line) for l in p.lines):
            p.lines.append(Line(area, line))
        app = catalog.lookup(d)
        if app is None or not app.product_refs:
            stats["without_product"].append(d.pa)
            continue
        product_ref, h2p_ref = app.product_refs[0]
        note = device_notes.get(d.pa, {})
        dev = Device(uid=f"{pid}-0_DI-{i}", area=area, line=line, address=addr, product_ref=product_ref,
                     h2p_ref=h2p_ref, app_id=app.full_id, name=note.get("name", ""), serial=(d.serial or "").replace(":", ""),
                     origin="rekonstruiert")
        ev = evaluations.get(d.pa)
        if ev is not None:
            dev.params = {v.instance_id: _value(v.value) for v in ev.values
                          if v.from_device and not v.is_default and not v.untouched and not v.internal}
        by_obj: dict[int, list[str]] = {}
        for num, ga in d.links:
            by_obj.setdefault(num, []).append(ga)
        needed: set[str] = set()
        for num, gas in sorted(by_obj.items()):
            ref = co_ref(app, ev, num)
            if ref is None:
                stats["objects_unknown"].append(f"{d.pa}/{num}")
                continue
            if ev is None or num not in ev.active_cos:
                stats["objects_guessed"] += 1
            dev.links.append(ComLink(ref, sorted(gas, key=lambda g: _GA(g).raw), num))
            if "_MI-1_" in ref:
                needed.add(app.full_id + "_" + ref.split("_MI-1_")[0])
        dev.modules = _modules(app, ev, needed)
        p.devices.append(dev)
        stats["devices"] += 1

    # Gruppenadressen mit Haupt-/Mittelgruppen
    all_gas = sorted({ga for d in devices for _, ga in d.links}, key=lambda g: _GA(g).raw)
    for ga in all_gas:
        main, middle, _ = (int(x) for x in ga.split("/"))
        main_uid, mid_uid = f"{pid}-0_GR-{main}", f"{pid}-0_GR-{main}-{middle}"
        if not any(r.uid == main_uid for r in p.ranges):
            p.ranges.append(GroupRange(main_uid, range_names.get(str(main)) or f"Hauptgruppe {main}",
                                       max(1, main << 11), (main << 11) | 0x7FF))
        if not any(r.uid == mid_uid for r in p.ranges):
            start = (main << 11) | (middle << 8)
            p.ranges.append(GroupRange(mid_uid, range_names.get(f"{main}/{middle}") or f"Mittelgruppe {main}/{middle}",
                                       max(1, start), start | 0xFF, main_uid))
        dpt = dpts.get(ga)
        p.group_addresses[ga] = GroupAddress(ga, ga_names.get(ga) or f"GA {ga}", _dpst(dpt), range_uid=mid_uid)

    # Räume aus den Notizen
    rooms: dict[str, list[str]] = {}
    for dev in p.devices:
        room = device_notes.get(dev.pa, {}).get("room")
        if room:
            rooms.setdefault(room, []).append(dev.uid)
    if rooms:
        building = Space(f"{pid}-0_BP-1", "Building", name)
        p.spaces.append(building)
        for i, (room, uids) in enumerate(sorted(rooms.items()), start=2):
            p.spaces.append(Space(f"{pid}-0_BP-{i}", "Room", room, building.uid, uids))

    p.products = _product_files(catalog, p.devices)
    return p, stats


def co_ref(app: Application, ev: Evaluation | None, number: int) -> str | None:
    """Relative ComObjectRef-Id (ohne Applikations-Präfix), bevorzugt die aktive Variante.

    Ohne bestimmbare aktive Variante die erste dieser Nummer – die am Bus vorhandene
    Verknüpfung ist wichtiger als die genaue Variante.
    """
    key = ev.active_cos.get(number) if ev is not None else None
    if key is None:
        keys = sorted(k for k, n in app.ref_number.items() if n == number)
        if not keys:
            return None
        key = keys[0]
    return rel_ref(app, key)


def rel_ref(app: Application, key: str) -> str:
    """Katalog-Schlüssel ("Instanz|Ref" bzw. "Ref") → relative Ref-Id wie im Projekt."""
    instance, _, ref = key.rpartition("|")
    prefix = app.full_id + "_"
    rel = ref.removeprefix(prefix)
    if instance:
        # Modulobjekt: MD-2_O-2-0_R-1 in Instanz MD-2_M-1 → MD-2_M-1_MI-1_O-2-0_R-1
        inst_rel = instance.removeprefix(prefix)
        md = inst_rel.rsplit("_M-", 1)[0]
        rel = f"{inst_rel}_MI-1_{rel[len(md) + 1:]}" if rel.startswith(md + "_") else rel
    return rel


def _modules(app: Application, ev: Evaluation | None, needed: set[str]) -> list[ModuleInstance]:
    instances = dict(ev.modules) if ev is not None else {}
    for inst_id in needed - instances.keys():
        args = ev.ap.module_args(inst_id) if ev is not None else None
        if args is not None:
            instances[inst_id] = args
    prefix = app.full_id + "_"
    return [ModuleInstance(f"{inst.removeprefix(prefix)}_MI-1", inst.removeprefix(prefix),
                           [(r.removeprefix(prefix), v) for r, v in args])
            for inst, args in instances.items()]


def _product_files(catalog: Catalog, devices: list[Device]) -> dict[str, bytes]:
    """Herstellerordner (samt Signatur) und knx_master.xml unverändert aus der Quelle."""
    sources: dict[str, str] = {}
    for d in devices:
        app = next((a for a in catalog.apps.values() if a.full_id == d.app_id), None)
        if app:
            sources.setdefault(app.key[:6], app.source_path)
    files: dict[str, bytes] = {}
    for mfr, source in sources.items():
        path = Path(source)
        if path.is_dir():
            for f in path.rglob("*"):
                rel = f.relative_to(path).as_posix()
                if f.is_file() and (rel.startswith(mfr + "/") or rel in (mfr + ".signature", "knx_master.xml")):
                    files.setdefault(rel, f.read_bytes())
            continue
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                if n.startswith(mfr + "/") or n == mfr + ".signature" or n == "knx_master.xml":
                    files.setdefault(n, z.read(n))
    return files


def _namespace(catalog: Catalog) -> str:
    for app in catalog.apps.values():
        try:
            path = Path(app.source_path)
            head = ((path / app.member).read_bytes() if path.is_dir() else zipfile.ZipFile(path).read(app.member))[:600]
            m = re.search(rb'xmlns="(http://knx\.org/xml/project/\d+)"', head)
            if m:
                return m[1].decode()
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
    return "http://knx.org/xml/project/23"


def _dpst(dpt: dict | None) -> str:
    if not dpt or not dpt.get("main"):
        return ""
    return f"DPST-{dpt['main']}-{dpt['sub']}" if dpt.get("sub") is not None else f"DPT-{dpt['main']}"


def _value(value: object) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def from_store(store, catalog: Catalog, name: str = "Rekonstruktion", project_id: str = "P-0E7A") -> tuple[Project, dict]:
    """Alles aus der Rekonstruktions-Datenbank: Geräte, Parameterauswertung, Notizen."""
    from .. import catalog as catalog_mod
    from .. import decode, device, params

    devices = [d for pa, m in store.found_devices() if device.supported(m)
               for d in [decode.decode_device(store, pa, m)] if d.app_id or d.links]
    evaluations, cache = {}, {}
    for d in devices:
        app = catalog.lookup(d)
        if app is None or not app.source_path:
            continue
        ap = cache.get(app.key) or cache.setdefault(app.key, params.AppParams(app))
        ev = params.evaluate_from_store(ap, decode.Raw(store, d.pa), {n for n, _ in d.links})
        evaluations[d.pa] = ev if any(v.from_device for v in ev.values) else None
    return from_reconstruction(
        name, devices, catalog, evaluations,
        ga_names={k: v.get("name", "") for k, v in store.notes("ga").items()},
        range_names={k: v.get("name", "") for k, v in store.notes("range").items()},
        dpts=catalog_mod.ga_dpts(devices, catalog), device_notes=store.notes("device"), project_id=project_id,
    )
