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
from contextlib import asynccontextmanager

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
        self.route_back = False  # wirksam für die laufende/letzte Verbindung
        self.route_back_why = ""
        self.xknx = None  # laufende Verbindung – auch für andere Lesezugriffe nutzbar

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, gateway: str, route_back: bool | None = None) -> None:
        """route_back=True erzwingt Route-Back, sonst Automatik (bzw. Prozess-Voreinstellung)."""
        await self.stop()
        self.gateway, self.error, self.state = gateway, "", "verbinde"
        self.route_back, self.route_back_why = bus.resolve_route_back(gateway, route_back)
        self.task = asyncio.create_task(self._run(gateway, self.route_back))

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

    async def _run(self, gateway: str, route_back: bool) -> None:
        try:
            async with bus.connect(gateway, telegram_cb=self._received, route_back=route_back) as xknx:
                self.own, self.state, self.xknx = str(xknx.current_address), "verbunden", xknx
                await asyncio.Event().wait()  # läuft, bis stop() abbricht
        except asyncio.CancelledError:
            raise
        except (Exception, SystemExit) as e:  # noqa: BLE001 - Verbindungsfehler gehören in die Oberfläche
            log.warning("Gruppenmonitor: %s", e)
            self.state, self.error = "Fehler", str(e) or type(e).__name__
        finally:
            self.own, self.xknx = "", None


@asynccontextmanager
async def shared_connection(monitor: BusMonitor, gateway: str, route_back: bool | None = None):
    """Busverbindung für einen Lesezugriff: die des Gruppenmonitors, wenn er zum selben Interface
    verbunden ist (belegt keinen zweiten Tunnelplatz), sonst eine eigene, die danach schließt.
    In beiden Fällen gilt der ReadOnlyGuard aus bus.connect."""
    if monitor.xknx is not None and monitor.state == "verbunden" and monitor.gateway == gateway:
        yield monitor.xknx
        return
    async with bus.connect(gateway, route_back=route_back) as xknx:
        yield xknx
