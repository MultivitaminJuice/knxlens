"""Der Guard muss jeden schreibenden oder steuernden Dienst abweisen."""

import inspect

import pytest
from xknx.telegram import apci, tpci

from knxrecon.bus import ALLOWED_APCI, OPTIONAL_APCI, ReadOnlyGuard, WriteBlocked

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
    for bad in ("Write", "Restart", "Response", "IndividualAddress"):
        assert not any(bad in n for n in names), names


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

    from knxrecon.bus import ReadOnlyGuard

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
