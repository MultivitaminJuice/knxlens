"""Der Guard muss jeden schreibenden oder steuernden Dienst abweisen."""

import inspect

import pytest
from xknx.telegram import apci, tpci

from knxlens.bus import ALLOWED_APCI, OPTIONAL_APCI, ReadOnlyGuard, WriteBlocked

DATA = tpci.TDataConnected(sequence_number=0)
ALL_APCI = [
    cls
    for _, cls in inspect.getmembers(apci, inspect.isclass)
    if issubclass(cls, apci.APCI)
    and not cls.__name__.startswith(("APCI", "_"))
    and hasattr(cls, "CODE")
]


def _instance(cls):
    try:
        return cls()
    except TypeError:
        return cls.__new__(cls)


@pytest.mark.parametrize("cls", ALL_APCI, ids=lambda c: c.__name__)
def test_only_whitelist_passes(cls):
    guard = ReadOnlyGuard()
    if cls in ALLOWED_APCI:
        guard.check(DATA, _instance(cls))
    else:
        with pytest.raises(WriteBlocked):
            guard.check(DATA, _instance(cls))


def test_whitelist_contains_no_write_or_restart():
    names = [c.__name__ for c in ALLOWED_APCI + sum(OPTIONAL_APCI.values(), ())]
    for bad in ("Write", "Restart", "Response"):
        assert not any(bad in n for n in names), names
    # Only the plain broadcast read of individual addresses, and only as an explicit option
    assert [n for n in names if "IndividualAddress" in n] == ["IndividualAddressRead"]
    assert apci.IndividualAddressRead not in ALLOWED_APCI


def test_progmode_read_needs_explicit_allow_and_writes_stay_blocked():
    broadcast = tpci.TDataBroadcast()
    with pytest.raises(WriteBlocked):
        ReadOnlyGuard().check(broadcast, apci.IndividualAddressRead())
    g = ReadOnlyGuard({"progmode"})
    g.check(broadcast, apci.IndividualAddressRead())
    for cls in ALL_APCI:
        if "IndividualAddress" in cls.__name__ and cls is not apci.IndividualAddressRead:
            with pytest.raises(WriteBlocked):
                g.check(broadcast, _instance(cls))


def test_permit_is_temporary():
    g = ReadOnlyGuard()
    with g.permit("progmode"):
        g.check(tpci.TDataBroadcast(), apci.IndividualAddressRead())
    with pytest.raises(WriteBlocked):
        g.check(tpci.TDataBroadcast(), apci.IndividualAddressRead())
    with pytest.raises(ValueError), g.permit("write"):
        pass


def test_optional_needs_explicit_allow():
    with pytest.raises(WriteBlocked):
        ReadOnlyGuard().check(DATA, apci.AuthorizeRequest(key=0xFFFFFFFF))
    ReadOnlyGuard({"authorize"}).check(DATA, apci.AuthorizeRequest(key=0xFFFFFFFF))
    with pytest.raises(WriteBlocked):
        ReadOnlyGuard({"authorize"}).check(tpci.TDataGroup(), _instance(apci.GroupValueWrite))


def test_group_write_never_allowed():
    for allow in (set(), {"group_read"}, {"group_read", "authorize"}):
        with pytest.raises(WriteBlocked):
            ReadOnlyGuard(allow).check(tpci.TDataGroup(), _instance(apci.GroupValueWrite))
        with pytest.raises(WriteBlocked):
            ReadOnlyGuard(allow).check(tpci.TDataGroup(), _instance(apci.GroupValueResponse))


def test_control_tpci():
    g = ReadOnlyGuard()
    for t in (tpci.TConnect(), tpci.TDisconnect(), tpci.TAck(0), tpci.TNak(0)):
        g.check(t, None)
    with pytest.raises(WriteBlocked):
        g.check(tpci.TDataGroup(), None)


def test_unknown_allow_key():
    with pytest.raises(ValueError):
        ReadOnlyGuard({"write"})


def test_install_blocks_on_real_xknx():
    import asyncio

    from xknx import XKNX
    from xknx.dpt import DPTBinary
    from xknx.telegram import GroupAddress, Telegram

    from knxlens.bus import ReadOnlyGuard

    async def run():
        xknx = XKNX()
        ReadOnlyGuard().install(xknx)
        t = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=apci.GroupValueWrite(DPTBinary(1)),
        )
        with pytest.raises(WriteBlocked):
            await xknx.cemi_handler.send_telegram(t)

    asyncio.run(run())


def test_scan_progmode_permits_only_during_call(monkeypatch):
    import asyncio

    import xknx.management.procedures as procedures
    from xknx import XKNX
    from xknx.telegram import IndividualAddress

    from knxlens import bus, scan

    x = XKNX()
    g = ReadOnlyGuard()
    g.install(x)
    seen = []

    async def fake(xknx, timeout=3, raise_if_multiple=False):
        g.check(tpci.TDataBroadcast(), apci.IndividualAddressRead())  # allowed right now
        seen.append(timeout)
        return [IndividualAddress("1.1.12"), IndividualAddress("1.1.3"), IndividualAddress("1.1.12")]

    monkeypatch.setattr(procedures, "nm_individual_address_read", fake)
    monkeypatch.setitem(bus.GUARDS, id(x), g)
    assert asyncio.run(scan.progmode(x, timeout=1)) == ["1.1.3", "1.1.12"]
    assert seen == [1]
    with pytest.raises(WriteBlocked):
        g.check(tpci.TDataBroadcast(), apci.IndividualAddressRead())

    bus.GUARDS.pop(id(x))
    with pytest.raises(RuntimeError):
        asyncio.run(scan.progmode(x))
