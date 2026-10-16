"""Busverbindung mit ReadOnlyGuard.

Jedes Telegramm, das xknx sendet, läuft durch `CEMIHandler.send_telegram`
(Management, Gruppentelegramme, Broadcasts). Diese Methode wird auf der
XKNX-Instanz durch eine geprüfte Variante ersetzt. Zusätzlich prüft eine
zweite Sperre eine Ebene tiefer (`knxip_interface.send_cemi`) den fertigen
cEMI-Frame – falls künftige xknx-Versionen einen anderen Weg nehmen.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import Telegram, apci, tpci

log = logging.getLogger(__name__)


class WriteBlocked(RuntimeError):
    """Ein nicht erlaubter Dienst sollte gesendet werden."""


# Lesende Anwendungsdienste, immer erlaubt.
ALLOWED_APCI: tuple[type[apci.APCI], ...] = (
    apci.DeviceDescriptorRead,
    apci.PropertyValueRead,
    apci.PropertyDescriptionRead,
    apci.MemoryRead,
    apci.MemoryExtendedRead,
    apci.ADCRead,
)
# Erlaubt nur, wenn ausdrücklich freigeschaltet (siehe KONZEPT.md).
OPTIONAL_APCI: dict[str, tuple[type[apci.APCI], ...]] = {
    "authorize": (apci.AuthorizeRequest,),
    "group_read": (apci.GroupValueRead,),
}
# Reine Transportsteuerung ohne Nutzdaten.
ALLOWED_CONTROL_TPCI = (tpci.TConnect, tpci.TDisconnect, tpci.TAck, tpci.TNak)


class ReadOnlyGuard:
    def __init__(self, allow: set[str] | None = None) -> None:
        allow = allow or set()
        unknown = allow - OPTIONAL_APCI.keys()
        if unknown:
            raise ValueError(f"Unbekannte Freigabe: {unknown}")
        self.allowed = ALLOWED_APCI + tuple(
            cls for key in allow for cls in OPTIONAL_APCI[key]
        )
        self.sent = 0

    def check(self, t_pci: tpci.TPCI, payload: apci.APCI | None) -> None:
        if payload is None:
            if isinstance(t_pci, ALLOWED_CONTROL_TPCI):
                return
            raise WriteBlocked(f"Steuer-TPCI ohne Payload nicht erlaubt: {t_pci}")
        if isinstance(t_pci, ALLOWED_CONTROL_TPCI):
            raise WriteBlocked(f"Steuer-TPCI mit Payload: {t_pci} {payload}")
        # isinstance genügt nicht: Write-Klassen erben nicht von Read-Klassen,
        # aber wir verlangen trotzdem exakte Typgleichheit.
        if type(payload) not in self.allowed:
            raise WriteBlocked(f"Dienst nicht erlaubt: {type(payload).__name__}")

    def check_telegram(self, telegram: Telegram) -> None:
        self.check(telegram.tpci, telegram.payload)

    def install(self, xknx: XKNX) -> None:
        """Hängt geprüfte Unterklassen ein (xknx nutzt __slots__, daher Klassentausch)."""
        guard = self
        handler = xknx.cemi_handler
        iface = xknx.knxip_interface

        class GuardedCEMIHandler(type(handler)):  # type: ignore[misc]
            __slots__ = ()

            async def send_telegram(self, telegram: Telegram) -> None:
                guard.check_telegram(telegram)
                guard.sent += 1
                await super().send_telegram(telegram)

        class GuardedInterface(type(iface)):  # type: ignore[misc]
            __slots__ = ()

            async def send_cemi(self, cemi):  # noqa: ANN001
                guard.check(cemi.data.tpci, cemi.data.payload)
                return await super().send_cemi(cemi)

        handler.__class__ = GuardedCEMIHandler
        iface.__class__ = GuardedInterface


@asynccontextmanager
async def connect(
    gateway_ip: str,
    *,
    allow: set[str] | None = None,
    telegram_cb=None,  # noqa: ANN001
) -> AsyncIterator[XKNX]:
    """Tunnelverbindung (UDP) mit installiertem Guard."""
    if not gateway_ip:
        raise SystemExit("Busbefehl braucht -g <IP des Interfaces>")
    xknx = XKNX(
        connection_config=ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=gateway_ip,
        ),
        telegram_received_cb=telegram_cb,
        state_updater=False,
    )
    guard = ReadOnlyGuard(allow)
    guard.install(xknx)
    await xknx.start()
    log.info("Verbunden über %s als %s", gateway_ip, xknx.current_address)
    try:
        yield xknx
    finally:
        await xknx.stop()
        log.info("Getrennt, %d Telegramme gesendet", guard.sent)
