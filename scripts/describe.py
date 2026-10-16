"""KNXnet/IP Description-Request an ein Interface (nur lesend, kein Bus-Verkehr)."""
import asyncio
import sys

from xknx.io.self_description import request_description


async def main(ip: str) -> None:
    gw = await request_description(ip)
    print(f"Name:              {gw.name}")
    print(f"Phys. Adresse:     {gw.individual_address}")
    print(f"Seriennummer:      {gw.serial_number}")
    print(f"MAC:               {gw.mac_address}")
    print(f"Core-Version:      {gw.core_version}")
    print(f"Tunneling UDP/TCP: {gw.supports_tunnelling}/{gw.supports_tunnelling_tcp}")
    print(f"Routing:           {gw.supports_routing}")
    print(f"Secure:            {gw.supports_secure}")
    for addr, slot in (getattr(gw, "tunnelling_slots", None) or {}).items():
        print(f"  Tunnel-Slot {addr}: {slot}")


asyncio.run(main(sys.argv[1]))
