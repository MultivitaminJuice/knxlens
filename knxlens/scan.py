"""Linienscan: welche physikalischen Adressen antworten?

Pro Adresse: T_Connect, A_DeviceDescriptor_Read(0), T_Disconnect.
Vorhandene Geräte antworten in ~300 ms. Leere Adressen erkennt man erst am
Timeout des Transport-ACKs (3 s + eine Wiederholung): Der Gira X1 bestätigt
jedes Telegramm positiv per L_Data.con, auch ohne Empfänger. Darum parallel.
Antwortet eine Adresse mit T_Disconnect ('refused'), ist sie belegt, aber ohne
Verbindungsdienst – typisch für die virtuellen Tunneladressen des Interfaces.
"""

from __future__ import annotations

import asyncio
import logging
import time

from xknx import XKNX
from xknx.exceptions import (
    ManagementConnectionError,
    ManagementConnectionRefused,
    ManagementConnectionTimeout,
)
from xknx.telegram import IndividualAddress, apci

from . import bus
from .store import Store

log = logging.getLogger(__name__)


async def probe(xknx: XKNX, pa: IndividualAddress) -> tuple[str, int | None]:
    """Ergebnis: 'device' (mit Maske), 'refused' (belegt, kein Verbindungsdienst), 'none'."""
    try:
        async with xknx.management.connection(pa) as conn:
            resp = await conn.request(apci.DeviceDescriptorRead(descriptor=0))
            return "device", resp.payload.value
    except ManagementConnectionRefused:
        return "refused", None
    except ManagementConnectionTimeout:
        return "none", None
    except ManagementConnectionError as err:
        log.debug("%s: %s", pa, err)
        return "none", None


async def progmode(xknx: XKNX, timeout: float = 3.0) -> list[str]:
    """Individual addresses of all devices currently in programming mode.

    Sends one broadcast A_IndividualAddress_Read (read-only; only devices with the
    programming LED on answer) and collects answers for `timeout` seconds. The service
    is allowed on this connection only for the duration of the call.
    """
    from xknx.management.procedures import nm_individual_address_read

    guard = bus.GUARDS.get(id(xknx))
    if guard is None:
        raise RuntimeError("Verbindung ohne ReadOnlyGuard – nur über bus.connect verbinden")
    with guard.permit("progmode"):
        found = await nm_individual_address_read(xknx, timeout=timeout)
    return sorted({str(a) for a in found}, key=lambda a: [int(x) for x in a.split(".")])


async def scan_line(
    xknx: XKNX,
    store: Store,
    area: int,
    line: int,
    start: int = 0,
    end: int = 255,
    parallel: int = 4,
) -> list[tuple[str, int | None]]:
    """Mehrere Adressen gleichzeitig prüfen: leere Adressen kosten je ~6 s Timeout."""
    own = str(xknx.current_address)
    sem = asyncio.Semaphore(parallel)
    found: list[tuple[str, int | None]] = []

    async def one(dev: int) -> None:
        pa = IndividualAddress(f"{area}.{line}.{dev}")
        if str(pa) == own:
            return
        async with sem:
            t0 = time.monotonic()
            result, mask = await probe(xknx, pa)
            ms = int((time.monotonic() - t0) * 1000)
        store.probe(str(pa), result, mask, ms)
        if result != "none":
            found.append((str(pa), mask))
            log.info("%-9s %-7s mask=%s  (%d ms)", pa, result, f"0x{mask:04X}" if mask is not None else "-", ms)

    await asyncio.gather(*(one(d) for d in range(start, end + 1)))
    return sorted(found, key=lambda r: [int(x) for x in r[0].split(".")])
