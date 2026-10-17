"""Gruppenmonitor für den Editor: nur empfangen, nichts senden.

Die Verbindung läuft über bus.connect – mit installiertem ReadOnlyGuard, der jeden
Schreibversuch abweist. Telegramme liegen nur im Speicher (Ringpuffer); die Oberfläche
holt neue Zeilen per `since(id)` ab.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from xknx.telegram import Telegram

from .. import bus
from ..monitor import row

log = logging.getLogger(__name__)

SERVICES = {"GroupValueWrite": "Schreiben", "GroupValueResponse": "Antwort", "GroupValueRead": "Lesen"}


class BusMonitor:
    def __init__(self, limit: int = 20000) -> None:
        self.rows: deque[dict] = deque(maxlen=limit)
        self.next_id = 1
        self.task: asyncio.Task | None = None
        self.gateway = ""
        self.own = ""
        self.state = "getrennt"  # getrennt, verbinde, verbunden, Fehler
        self.error = ""

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, gateway: str) -> None:
        await self.stop()
        self.gateway, self.error, self.state = gateway, "", "verbinde"
        self.task = asyncio.create_task(self._run(gateway))

    async def stop(self) -> None:
        if self.running:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        if self.state != "Fehler":
            self.state = "getrennt"

    def clear(self) -> None:
        self.rows.clear()

    def since(self, last_id: int) -> list[dict]:
        return [r for r in self.rows if r["id"] > last_id]

    def _received(self, telegram: Telegram) -> None:
        ts, src, dst, service, payload, bits = row(telegram, time.time())
        if not service.startswith("GroupValue"):
            return
        self.rows.append({"id": self.next_id, "ts": ts, "src": src, "dst": dst, "service": service,
                          "payload": payload, "bits": bits})
        self.next_id += 1

    async def _run(self, gateway: str) -> None:
        try:
            async with bus.connect(gateway, telegram_cb=self._received) as xknx:
                self.own, self.state = str(xknx.current_address), "verbunden"
                await asyncio.Event().wait()  # läuft, bis stop() abbricht
        except asyncio.CancelledError:
            raise
        except (Exception, SystemExit) as e:  # noqa: BLE001 - Verbindungsfehler gehören in die Oberfläche
            log.warning("Gruppenmonitor: %s", e)
            self.state, self.error = "Fehler", str(e) or type(e).__name__
        finally:
            self.own = ""
