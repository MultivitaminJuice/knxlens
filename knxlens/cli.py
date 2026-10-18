"""Kommandozeile: python -m knxlens <befehl> ..."""

from __future__ import annotations

import argparse
import asyncio
import logging

import json
from pathlib import Path

from xknx.telegram import IndividualAddress

from . import bus, compare, decode, device, monitor, reference, report, scan
from .store import Store

DEFAULT_DB = "data/knxlens.sqlite"


async def cmd_scan(args: argparse.Namespace) -> None:
    store = Store(args.db)
    area, line = (int(x) for x in args.line.split("."))
    async with bus.connect(args.gateway) as xknx:
        store.start_session(args.gateway, str(xknx.current_address), f"scan {args.line}")
        found = await scan.scan_line(xknx, store, area, line, args.start, args.end, args.parallel)
    print(f"{len(found)} Adressen antworten auf Linie {args.line}:")
    for pa, mask in found:
        print(f"  {pa:9} {f'0x{mask:04X}' if mask is not None else 'refused'}")


async def cmd_probe(args: argparse.Namespace) -> None:
    store = Store(args.db)
    async with bus.connect(args.gateway) as xknx:
        store.start_session(args.gateway, str(xknx.current_address), f"probe {args.pa}")
        result, mask = await scan.probe(xknx, IndividualAddress(args.pa))
    print(args.pa, result, f"0x{mask:04X}" if mask is not None else "")


async def cmd_read(args: argparse.Namespace) -> None:
    store = Store(args.db)
    masks = dict(store.found_devices())
    targets = args.pa or [pa for pa, mask in masks.items() if device.supported(mask)]
    async with bus.connect(args.gateway) as xknx:
        store.start_session(args.gateway, str(xknx.current_address), f"read {len(targets)}")
        for pa in targets:
            try:
                summary = await device.read_device(xknx, store, pa, masks.get(pa))
            except Exception as err:  # noqa: BLE001 - ein Gerät darf den Lauf nicht abbrechen
                logging.warning("%s: %s", pa, err)
                continue
            print(json.dumps(summary, ensure_ascii=False))


def cmd_compare(args: argparse.Namespace) -> None:
    store = Store(args.db)
    ref = reference.load(args.reference)
    print("## Anwesenheit nach Adresse\n")
    print(compare.compare_presence(store, ref))
    print("\n## Inhaltliche Zuordnung\n")
    print(compare.compare_links(_decoded(store), ref))


def _decoded(store: Store) -> list:
    devices = [decode.decode_device(store, pa, mask) for pa, mask in store.found_devices() if device.supported(mask)]
    return [d for d in devices if d.app_id or d.links]


def cmd_report(args: argparse.Namespace) -> None:
    text = report.inventory(_decoded(Store(args.db)))
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"geschrieben: {args.out}")
    else:
        print(text)


async def cmd_read_params(args: argparse.Namespace) -> None:
    from . import catalog, params

    store = Store(args.db)
    cat = catalog.Catalog()
    for path in args.products:
        cat.add(path)
    devices = {d.pa: d for d in _decoded(store)}
    targets = args.pa or sorted(devices, key=lambda pa: [int(x) for x in pa.split(".")])
    async with bus.connect(args.gateway) as xknx:
        store.start_session(args.gateway, str(xknx.current_address), f"read-params {len(targets)}")
        for pa in targets:
            app = cat.lookup(devices[pa]) if pa in devices else None
            if app is None:
                print(f"{pa}: keine Produktdaten")
                continue
            ap = params.AppParams(app)
            async with xknx.management.connection(IndividualAddress(pa)) as conn:
                summary = await params.read(device.Reader(conn, store), ap)
            print(pa, json.dumps(summary, ensure_ascii=False))


def cmd_project(args: argparse.Namespace) -> None:
    from . import catalog
    from .project import Project
    from .project.knxproj_io import export_knxproj, import_knxproj
    from .project.reconstruct import from_store

    if args.action == "import":
        p = import_knxproj(args.source)
        p.save(args.out)
        print(f"{args.out}: {_project_summary(p)}")
    elif args.action == "reconstruct":
        cat = catalog.Catalog()
        for path in args.products or []:
            cat.add(path)
        p, stats = from_store(Store(args.db), cat, args.name)
        p.save(args.out)
        print(f"{args.out}: {_project_summary(p)}")
        print("geschätzte Objektvarianten:", stats["objects_guessed"], "| ohne Produktdaten:", stats["without_product"])
    elif args.action == "export":
        p = Project.load(args.source)
        stats = export_knxproj(p, args.out)
        print(f"{args.out}: {json.dumps(stats, ensure_ascii=False)} – unsigniert, von der ETS nicht importierbar")
    elif args.action == "info":
        print(_project_summary(Project.load(args.source)))


def _project_summary(p) -> str:
    return (f"„{p.name}“ · {len(p.devices)} Geräte · {len(p.group_addresses)} GAs · "
            f"{sum(len(l.gas) for d in p.devices for l in d.links)} Verknüpfungen · "
            f"{sum(len(d.params) for d in p.devices)} Parameterwerte · {len(p.spaces)} Räume/Bereiche")


async def cmd_monitor(args: argparse.Namespace) -> None:
    await monitor.run(args.gateway, Store(args.db), args.duration)


def cmd_edit(args: argparse.Namespace) -> None:
    import uvicorn

    from .editor import STATIC, create_editor_app

    if not (STATIC / "index.html").exists():
        raise SystemExit("Editor-Oberfläche nicht gebaut: cd frontend && npm install && npm run build")
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"Achtung: Editor ohne Anmeldung unter {args.host}:{args.port} erreichbar.")
    print(f"Projekt {args.project}: Produktdaten werden geladen …", flush=True)
    app = create_editor_app(args.project, gateway=args.gateway or "")
    print(f"Editor: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def cmd_image(args: argparse.Namespace) -> None:
    """Download-Abbild berechnen und gegen die gelesenen Geräte prüfen (kein Buszugriff)."""
    from .image import check_against_store
    from .params import AppParams
    from .project import Project
    from .project.products import app_for, project_catalog
    from .project.reconstruct import object_number

    p = Project.load(args.project)
    cat = project_catalog(p)
    store = Store(args.db)
    masks = dict(store.found_devices())
    aps: dict[str, AppParams] = {}
    total_eq = total_diff = tables_ok = tables_all = 0
    for d in p.devices:
        if d.pa is None or (args.pa and d.pa not in args.pa) or d.pa not in masks:
            continue
        app = app_for(cat, d.app_id)
        ap = (aps.get(app.key) or aps.setdefault(app.key, AppParams(app))) if app else None
        c = check_against_store(ap, d, store, masks[d.pa], (lambda l, a=app: object_number(a, l.ref)) if app else None)
        total_eq += c.param_equal
        total_diff += len(c.param_diff)
        flags = []
        for name, ok in (("Adressen", c.address_table), ("Assoziationen", c.association_table)):
            if ok is not None:
                tables_all += 1
                tables_ok += ok
                flags.append(f"{name} {'=' if ok else '≠'}")
        if c.ets_equal or c.ets_diff:
            flags.append(f"ETS-Abbild {c.ets_equal}/{c.ets_equal + len(c.ets_diff)} gleich")
        print(f"{d.pa:10} Parameterbytes {c.param_equal:5} gleich, {len(c.param_diff):4} abweichend  {'  '.join(flags)}")
        for sid, off, dev, calc, owners in c.param_diff[: args.details]:
            print(f"    {sid.rsplit('_', 1)[-1]}+{off:#06x}  Gerät {dev:08b}  berechnet {calc:08b}  {'; '.join(owners[:2])}")
        for prob in c.problems[:5]:
            print(f"    ! {prob}")
    print(f"\nGesamt: Parameterbytes {total_eq} gleich, {total_diff} abweichend; Tabellen {tables_ok}/{tables_all} gleich")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .web import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        print(f"Achtung: Oberfläche ohne Anmeldung unter {args.host}:{args.port} erreichbar.")
    folder = Path(args.products_dir)
    products = sorted(str(p) for pattern in ("*.knxprod", "*.vd?") for p in folder.glob(pattern)) + (args.products or [])
    if products:
        print(f"Produktdaten: {len(products)} Datei(en)")
    uvicorn.run(create_app(args.db, args.reference, products), host=args.host, port=args.port, log_level="warning")


def main() -> None:
    p = argparse.ArgumentParser(prog="knxlens")
    p.add_argument("-g", "--gateway", help="IP des KNX-IP-Interfaces (für Busbefehle)")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="eine Adresse prüfen")
    s.add_argument("pa")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("scan", help="Linie scannen")
    s.add_argument("line", help="z. B. 1.1")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--end", type=int, default=255)
    s.add_argument("--parallel", type=int, default=4)
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("read", help="Geräte auslesen (ohne PA: alle aus dem Scan)")
    s.add_argument("pa", nargs="*")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("compare", help="gegen Referenz (xknxproject-JSON) abgleichen")
    s.add_argument("reference")
    s.set_defaults(func=cmd_compare)

    s = sub.add_parser("report", help="Inventar als Markdown (ohne Referenz)")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("read-params", help="Parameterspeicher lesen (braucht Produktdaten)")
    s.add_argument("pa", nargs="*")
    s.add_argument("--products", action="append", required=True)
    s.set_defaults(func=cmd_read_params)

    s = sub.add_parser("project", help="Projektdatei (*.knxlens): import, reconstruct, export, info")
    s.add_argument("action", choices=["import", "reconstruct", "export", "info"])
    s.add_argument("source", nargs="?", help="import: .knxproj · export/info: .knxlens")
    s.add_argument("-o", "--out", help="Zieldatei")
    s.add_argument("--products", action="append", help="reconstruct: Produktdaten (.knxprod/.knxproj)")
    s.add_argument("--name", default="Rekonstruktion")
    s.set_defaults(func=cmd_project)

    s = sub.add_parser("monitor", help="Gruppentelegramme passiv mitschneiden (sendet nichts)")
    s.add_argument("--duration", type=float, help="Sekunden; ohne Angabe bis Strg-C")
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("edit", help="Projekt im Editor öffnen (Browser)")
    s.add_argument("project", help="Projektdatei *.knxlens")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8770)
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("image", help="Download-Abbild berechnen und gegen gelesene Geräte prüfen")
    s.add_argument("project", help="Projektdatei *.knxlens")
    s.add_argument("pa", nargs="*", help="nur diese Geräte")
    s.add_argument("--details", type=int, default=0, help="so viele abweichende Bytes je Gerät zeigen")
    s.set_defaults(func=cmd_image)

    s = sub.add_parser("serve", help="Weboberfläche (nur gespeicherte Daten, kein Buszugriff)")
    s.add_argument("--reference", help="xknxproject-JSON des Referenzprojekts (optional)")
    s.add_argument("--products", action="append", help=".knxprod/.knxproj mit Produktdaten (mehrfach möglich)")
    s.add_argument("--products-dir", default="data/produkte", help="Ordner, dessen *.knxprod/*.vd? automatisch geladen werden")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("xknx").setLevel(logging.WARNING)
    result = args.func(args)
    if asyncio.iscoroutine(result):
        try:
            asyncio.run(result)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
