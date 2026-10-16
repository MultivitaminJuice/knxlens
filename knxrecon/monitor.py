"""Passiver Telegrammmitschnitt: nur empfangen, nichts senden.

Die Verbindung läuft über bus.connect (Guard aktiv). Gespeichert werden alle
Gruppentelegramme, die das Interface weiterreicht, roh in der Tabelle telegram.
"""

from __future__ import annotations

import asyncio
import logging
import time

from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram, apci

from . import bus
from .store import Store

log = logging.getLogger(__name__)


def row(telegram: Telegram, ts: float) -> tuple:
    """Telegramm → (ts, src, dst, service, payload, bits)."""
    p = telegram.payload
    payload = bits = None
    if isinstance(p, apci.GroupValueWrite | apci.GroupValueResponse):
        if isinstance(p.value, DPTBinary):
            bits = p.value.value
        elif isinstance(p.value, DPTArray):
            payload = bytes(p.value.value)
    return (ts, str(telegram.source_address), str(telegram.destination_address), type(p).__name__, payload, bits)


async def run(gateway: str, store: Store, duration: float | None = None, status_every: float = 60) -> int:
    buffer: list[tuple] = []
    total = 0

    def received(telegram: Telegram) -> None:
        buffer.append(row(telegram, time.time()))

    async with bus.connect(gateway, telegram_cb=received) as xknx:
        store.start_session(gateway, str(xknx.current_address), "monitor")
        log.info("Mitschnitt läuft über %s (eigene Adresse %s) – Strg-C beendet", gateway, xknx.current_address)
        start = last_status = time.monotonic()
        try:
            while duration is None or time.monotonic() - start < duration:
                await asyncio.sleep(1)
                if buffer:
                    batch, buffer[:] = buffer[:], []
                    store.add_telegrams(batch)
                    total += len(batch)
                if time.monotonic() - last_status >= status_every:
                    last_status = time.monotonic()
                    log.info("%d Telegramme bisher", total)
        except asyncio.CancelledError:
            pass
        finally:
            if buffer:
                store.add_telegrams(buffer)
                total += len(buffer)
    log.info("Mitschnitt beendet, %d Telegramme gespeichert", total)
    return total
