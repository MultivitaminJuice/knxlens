"""Kommandozeile: python -m knxrecon <befehl> ..."""

from __future__ import annotations

import argparse
import asyncio
import logging

import json

from xknx.telegram import IndividualAddress

from . import bus, compare, decode, device, monitor, reference, report, scan
from .store import Store

DEFAULT_DB = "data/recon.sqlite"


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


async def cmd_monitor(args: argparse.Namespace) -> None:
    await monitor.run(args.gateway, Store(args.db), args.duration)


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .web import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        print(f"Achtung: Oberfläche ohne Anmeldung unter {args.host}:{args.port} erreichbar.")
    uvicorn.run(create_app(args.db, args.reference), host=args.host, port=args.port, log_level="warning")


def main() -> None:
    p = argparse.ArgumentParser(prog="knxrecon")
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

    s = sub.add_parser("monitor", help="Gruppentelegramme passiv mitschneiden (sendet nichts)")
    s.add_argument("--duration", type=float, help="Sekunden; ohne Angabe bis Strg-C")
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("serve", help="Weboberfläche (nur gespeicherte Daten, kein Buszugriff)")
    s.add_argument("--reference", help="xknxproject-JSON des Referenzprojekts (optional)")
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
