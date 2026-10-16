"""Einzelne Speicherbereiche lesen (nur lesend, über den Guard)."""
import asyncio, sys
from xknx.telegram import IndividualAddress
from knxlens import bus
from knxlens.device import Reader
from knxlens.store import Store

async def main(gw, pa, ranges):
    store = Store("data/knxlens.sqlite")
    async with bus.connect(gw) as xknx:
        store.start_session(gw, str(xknx.current_address), f"peek {pa}")
        async with xknx.management.connection(IndividualAddress(pa)) as conn:
            r = Reader(conn, store)
            for rg in ranges:
                if rg.startswith("p"):  # p<obj>.<pid>
                    obj, pid = (int(x, 0) for x in rg[1:].split("."))
                    d = await r.prop(obj, pid)
                    print(f"obj {obj} pid {pid}:", d.hex(" ") if d else d)
                    continue
                a, n = (int(x, 0) for x in rg.split(":"))
                d = await r.mem(a, n)
                print(f"0x{a:04X}+{n}:", d.hex(" ") if d else d)

asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
