"""Busverbindung mit ReadOnlyGuard.

Jedes Telegramm, das xknx sendet, läuft durch `CEMIHandler.send_telegram`
(Management, Gruppentelegramme, Broadcasts). Diese Methode wird auf der
XKNX-Instanz durch eine geprüfte Variante ersetzt. Zusätzlich prüft eine
zweite Sperre eine Ebene tiefer (`knxip_interface.send_cemi`) den fertigen
cEMI-Frame – falls künftige xknx-Versionen einen anderen Weg nehmen.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager

from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import IndividualAddress, Telegram, apci, tpci

log = logging.getLogger(__name__)


#: Guard of each open connection (key: id of the XKNX instance), so code sharing a
#: connection can use guard.permit(). Entries are removed when the connection closes.
GUARDS: dict[int, ReadOnlyGuard] = {}


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
    # Broadcast read: only devices in programming mode answer with their address.
    # The writing counterparts (IndividualAddressWrite, …SerialNumberWrite) stay blocked.
    "progmode": (apci.IndividualAddressRead,),
}
# Writing services of a download. Never in `allowed`: only via permit_download(pa), and then
# only connection-oriented to that one individual address (IndividualAddressWrite stays blocked).
DOWNLOAD_APCI: tuple[type[apci.APCI], ...] = (apci.MemoryWrite, apci.PropertyValueWrite, apci.Restart)
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
        self.write_target: IndividualAddress | None = None  # set only inside permit_download()

    def check(self, t_pci: tpci.TPCI, payload: apci.APCI | None, dst=None) -> None:  # noqa: ANN001
        if payload is None:
            if isinstance(t_pci, ALLOWED_CONTROL_TPCI):
                return
            raise WriteBlocked(f"Steuer-TPCI ohne Payload nicht erlaubt: {t_pci}")
        if isinstance(t_pci, ALLOWED_CONTROL_TPCI):
            raise WriteBlocked(f"Steuer-TPCI mit Payload: {t_pci} {payload}")
        # Download: writing services only connection-oriented and only to the one released device
        if (type(payload) in DOWNLOAD_APCI and self.write_target is not None
                and isinstance(t_pci, tpci.TDataConnected) and dst == self.write_target):
            return
        # isinstance genügt nicht: Write-Klassen erben nicht von Read-Klassen,
        # aber wir verlangen trotzdem exakte Typgleichheit.
        if type(payload) not in self.allowed:
            raise WriteBlocked(f"Dienst nicht erlaubt: {type(payload).__name__}")

    @contextmanager
    def permit_download(self, pa: str):  # noqa: ANN201
        """Temporarily allow memory/property writes and restart, to this one device only."""
        if self.write_target is not None:
            raise RuntimeError("Es läuft bereits ein Download")
        self.write_target = IndividualAddress(pa)
        try:
            yield
        finally:
            self.write_target = None

    @contextmanager
    def permit(self, *keys: str):  # noqa: ANN201
        """Temporarily allow optional services on an existing connection (e.g. the monitor's)."""
        unknown = set(keys) - OPTIONAL_APCI.keys()
        if unknown:
            raise ValueError(f"Unbekannte Freigabe: {unknown}")
        before = self.allowed
        self.allowed = before + tuple(cls for key in keys for cls in OPTIONAL_APCI[key])
        try:
            yield
        finally:
            self.allowed = before

    def check_telegram(self, telegram: Telegram) -> None:
        self.check(telegram.tpci, telegram.payload, telegram.destination_address)

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
                guard.check(cemi.data.tpci, cemi.data.payload, getattr(cemi.data, "dst_addr", None))
                return await super().send_cemi(cemi)

        handler.__class__ = GuardedCEMIHandler
        iface.__class__ = GuardedInterface


#: Route-Back (NAT-Modus): Im CONNECT_REQUEST steht als Rückadresse 0.0.0.0:0,
#: das Interface antwortet an die Absenderadresse des UDP-Pakets. Nötig, wenn
#: zwischen Rechner und Interface NAT liegt – etwa ein Tailscale-Subnetz-Router
#: (Source-NAT): Mit der eigenen IP im HPAI schickt das Interface seine Antworten
#: an eine Adresse, die es nicht erreicht.
#:
#: Ohne weitere Angabe entscheidet die Automatik (needs_route_back). Erzwingen
#: lässt es sich für den ganzen Prozess per `knxlens --route-back` oder
#: KNXLENS_ROUTE_BACK=1, je Verbindung über das Argument route_back=True.
#: Abschalten lässt es sich nicht: xknx schaltet Route-Back ohnehin selbst ein,
#: wenn keine eigene Schnittstelle im Subnetz des Interface liegt.
ROUTE_BACK: bool = os.environ.get("KNXLENS_ROUTE_BACK", "").strip().lower() in {"1", "true", "yes", "ja", "on", "an"}


def _local_ip_for(gateway_ip: str) -> str | None:
    """Eigene Adresse, über die das System das Interface erreicht (sendet nichts)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((gateway_ip, 3671))
            return s.getsockname()[0]
    except OSError:
        return None


def _local_networks() -> list[ipaddress.IPv4Interface]:
    import ifaddr  # Abhängigkeit von xknx
    return [ipaddress.IPv4Interface(f"{ip.ip}/{ip.network_prefix}")
            for adapter in ifaddr.get_adapters() for ip in adapter.ips if ip.is_IPv4]


def needs_route_back(gateway_ip: str) -> tuple[bool, str]:
    """Automatik: Route-Back, wenn das Interface nicht im Subnetz der eigenen Absenderadresse liegt.

    Im eigenen LAN liegt es dort (direkt, wie bisher). Über einen Subnetz-Router,
    VPN oder sonstiges Routing nicht – dann antwortet es nur per Route-Back zuverlässig.
    """
    try:
        gw = ipaddress.IPv4Address(socket.gethostbyname(gateway_ip))
    except (OSError, ValueError):
        return True, "Adresse nicht auflösbar"
    local = _local_ip_for(str(gw))
    if local is None:
        return True, "kein Weg zum Interface bekannt"
    for net in _local_networks():
        if str(net.ip) == local:
            if gw in net.network:
                return False, f"im eigenen Netz {net.network}"
            return True, f"nicht im eigenen Netz {net.network}"
    return True, f"Absenderadresse {local} keinem Netz zuzuordnen"


def resolve_route_back(gateway_ip: str, route_back: bool | None = None) -> tuple[bool, str]:
    """Wirksame Einstellung und Begründung. route_back=True erzwingt, sonst Voreinstellung bzw. Automatik."""
    if route_back:
        return True, "erzwungen"
    if ROUTE_BACK:
        return True, "erzwungen (Voreinstellung)"
    on, why = needs_route_back(gateway_ip)
    return on, f"automatisch, {why}"


def connection_config(gateway_ip: str, route_back: bool | None = None) -> ConnectionConfig:
    """UDP-Tunnel zum Interface; route_back=True erzwingt Route-Back, sonst Automatik."""
    return ConnectionConfig(
        connection_type=ConnectionType.TUNNELING,
        gateway_ip=gateway_ip,
        route_back=resolve_route_back(gateway_ip, route_back)[0],
    )


@asynccontextmanager
async def connect(
    gateway_ip: str,
    *,
    allow: set[str] | None = None,
    telegram_cb=None,  # noqa: ANN001
    route_back: bool | None = None,
) -> AsyncIterator[XKNX]:
    """Tunnelverbindung (UDP) mit installiertem Guard."""
    if not gateway_ip:
        raise SystemExit("Busbefehl braucht -g <IP des Interfaces>")
    on, why = resolve_route_back(gateway_ip, route_back)
    log.info("Route-Back %s (%s)", "an – HPAI 0.0.0.0:0" if on else "aus", why)
    xknx = XKNX(
        connection_config=ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=gateway_ip,
            route_back=on,
        ),
        telegram_received_cb=telegram_cb,
        state_updater=False,
    )
    guard = ReadOnlyGuard(allow)
    guard.install(xknx)
    await xknx.start()
    GUARDS[id(xknx)] = guard  # only once connected, so a failed start leaves nothing behind
    log.info("Verbunden über %s als %s", gateway_ip, xknx.current_address)
    try:
        yield xknx
    finally:
        GUARDS.pop(id(xknx), None)
        await xknx.stop()
        log.info("Getrennt, %d Telegramme gesendet", guard.sent)
