"""Route-Back (NAT-Modus) für die Tunnelverbindung: Automatik, Voreinstellung, Auswahl."""

import ipaddress
import os
import subprocess
import sys

import pytest

from knxlens import bus


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch):
    """Rechner mit 192.168.158.109/24 im LAN und 100.101.88.38/32 auf Tailscale."""
    nets = [ipaddress.IPv4Interface("127.0.0.1/8"), ipaddress.IPv4Interface("192.168.158.109/24"),
            ipaddress.IPv4Interface("100.101.88.38/32")]
    monkeypatch.setattr(bus, "_local_networks", lambda: nets)
    monkeypatch.setattr(bus, "ROUTE_BACK", False)
    return monkeypatch


def via(lan: pytest.MonkeyPatch, local: str | None) -> None:
    lan.setattr(bus, "_local_ip_for", lambda gw: local)


def test_auto_off_in_own_lan(lan) -> None:
    via(lan, "192.168.158.109")
    on, why = bus.resolve_route_back("192.168.158.20")
    assert on is False and "192.168.158.0/24" in why
    assert bus.connection_config("192.168.158.20").route_back is False


def test_auto_on_through_subnet_router(lan) -> None:
    via(lan, "100.101.88.38")  # Weg über Tailscale (utun)
    on, why = bus.resolve_route_back("192.168.158.20")
    assert on is True and why.startswith("automatisch")
    assert bus.connection_config("192.168.158.20").route_back is True


def test_auto_on_when_source_unknown(lan) -> None:
    via(lan, "10.9.9.9")
    assert bus.resolve_route_back("192.168.158.20")[0] is True
    via(lan, None)
    assert bus.resolve_route_back("192.168.158.20")[0] is True


def test_process_default_forces_in_own_lan(lan) -> None:
    via(lan, "192.168.158.109")
    lan.setattr(bus, "ROUTE_BACK", True)
    assert bus.resolve_route_back("192.168.158.20") == (True, "erzwungen (Voreinstellung)")


def test_force_per_connection(lan) -> None:
    """Fall Subnetz-Kollision: Automatik sähe das eigene LAN, Route-Back wird erzwungen."""
    via(lan, "192.168.158.109")
    assert bus.resolve_route_back("192.168.158.20", True) == (True, "erzwungen")
    assert bus.connection_config("192.168.158.20", route_back=True).route_back is True


def test_false_means_automatic(lan) -> None:
    """Abschalten gibt es nicht: False verhält sich wie None (Automatik)."""
    via(lan, "100.101.88.38")
    assert bus.resolve_route_back("192.168.158.20", False)[0] is True
    via(lan, "192.168.158.109")
    assert bus.resolve_route_back("192.168.158.20", False)[0] is False


def test_real_loopback_is_own_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Attrappen: 127.0.0.1 liegt im eigenen Netz des Loopback-Interface."""
    assert bus.needs_route_back("127.0.0.1")[0] is False


@pytest.mark.parametrize(("value", "expected"),
                         [("1", True), ("ja", True), ("an", True), ("0", False), ("aus", False), ("", False)])
def test_environment_variable(value: str, expected: bool) -> None:
    env = {**os.environ, "KNXLENS_ROUTE_BACK": value}
    out = subprocess.run(
        [sys.executable, "-c", "from knxlens import bus; print(bus.ROUTE_BACK)"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == str(expected)


@pytest.mark.parametrize(("argv", "expected"), [([], False), (["--route-back"], True)])
def test_cli_flags(monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: bool) -> None:
    from knxlens import cli

    seen = {}
    monkeypatch.setattr(bus, "ROUTE_BACK", False)
    monkeypatch.setattr(cli, "cmd_report", lambda args: seen.setdefault("rb", bus.ROUTE_BACK))
    monkeypatch.setattr(sys, "argv", ["knxlens", *argv, "report"])
    cli.main()
    assert seen["rb"] is expected


def test_busmonitor_keeps_choice(lan) -> None:
    """Der Monitor merkt sich die wirksame Einstellung samt Begründung für die Oberfläche."""
    import asyncio

    from knxlens.editor.busmon import BusMonitor

    via(lan, "100.101.88.38")
    used = {}

    async def fake_run(self, gateway, route_back):  # noqa: ANN001
        used["rb"] = route_back

    lan.setattr(BusMonitor, "_run", fake_run)

    async def go(choice):  # noqa: ANN001
        m = BusMonitor()
        await m.start("192.168.158.20", choice)
        await m.task
        return m

    m = asyncio.run(go(None))
    assert (m.route_back, used["rb"]) == (True, True) and m.route_back_why.startswith("automatisch")
    via(lan, "192.168.158.109")
    m = asyncio.run(go(None))
    assert (m.route_back, used["rb"]) == (False, False)
    m = asyncio.run(go(True))
    assert (m.route_back, used["rb"], m.route_back_why) == (True, True, "erzwungen")
