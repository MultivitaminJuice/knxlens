"""Download over the bus (070x) against a fake device – no bus access."""

import asyncio
from types import SimpleNamespace

import pytest
from xknx.dpt import DPTBinary
from xknx.telegram import IndividualAddress, apci, tpci

from knxlens import bus, buswrite
from knxlens.bus import ReadOnlyGuard, WriteBlocked
from knxlens.download import DownloadError
from knxlens.loadplan import build
from knxlens.store import Store

PA = "1.1.83"
IDENT = buswrite.Identity(0x0083, 0x00B6, 0x32, 1)
DATA = tpci.TDataConnected(sequence_number=0)


# --- records: the worked examples of Test Suite Supplement G ---------------------------------------

def test_records_match_tssg_examples():
    seg = buswrite.segment_record(2, {"SegType": "0", "Address": str(0x4200), "Size": "16", "Access": "255",
                                      "MemType": "3", "SegFlags": "128"})
    assert seg.hex(" ") == "23 00 00 42 00 00 10 ff 03 80 00"
    task = buswrite.task_record(2, 0x4200, buswrite.Identity(0x0002, 0xA04A, 0x10, 0x80))
    assert task.hex(" ") == "23 02 00 42 00 80 00 02 a0 4a 10"
    assert buswrite.event_record(1, "unload").hex() == "14" + "00" * 10
    assert buswrite.event_record(2, "completed")[0] == 0x22


def test_table_task_segments_carry_no_identity():
    assert buswrite.task_record(1, 0x4000, None)[5:] == bytes(6)


# --- bus guard ------------------------------------------------------------------------------------

def test_download_permit_only_for_one_device_and_only_connected():
    g = ReadOnlyGuard()
    w = apci.MemoryWrite(address=0x4000, data=b"\x01")
    with pytest.raises(WriteBlocked):
        g.check(DATA, w, IndividualAddress(PA))
    with g.permit_download(PA):
        g.check(DATA, w, IndividualAddress(PA))
        g.check(DATA, apci.Restart(), IndividualAddress(PA))
        with pytest.raises(WriteBlocked):
            g.check(DATA, w, IndividualAddress("1.1.84"))  # another device
        with pytest.raises(WriteBlocked):
            g.check(tpci.TDataIndividual(), w, IndividualAddress(PA))  # connectionless
        with pytest.raises(WriteBlocked):
            g.check(DATA, apci.IndividualAddressWrite(address=IndividualAddress("1.1.99")), IndividualAddress(PA))
        with pytest.raises(WriteBlocked):
            g.check(tpci.TDataGroup(), apci.GroupValueWrite(DPTBinary(1)), None)
        with pytest.raises(RuntimeError):
            with g.permit_download("1.1.84"):
                pass
    with pytest.raises(WriteBlocked):
        g.check(DATA, w, IndividualAddress(PA))


# --- fake 070x device -----------------------------------------------------------------------------

class FakeDevice:
    def __init__(self, mem: dict[int, int], mask=0x0705):
        self.mem = dict(mem)
        self.mask = mask
        self.states = [1, 1, 1, 0]
        self.records: list[bytes] = []
        self.restarts = 0
        self.fail_after: int | None = None  # drop the connection after n memory writes
        self.writes = 0
        self.authorized = False
        self.mem_control = True  # False: load control only via PID 5
        self.prop_records: list[tuple[int, bytes]] = []
        self.props = {(0, 12): b"\x00\x83", (0, 11): bytes.fromhex("008383A02696"), (3, 13): IDENT.program_version}

    def event(self, lsm: int, ev: int) -> None:
        st = self.states[lsm - 1]
        if ev == 4:
            st = 0
        elif ev == 1:
            st = 2
        elif ev == 3:
            st = 2 if st == 2 else 3
        elif ev == 2:
            st = 1 if st == 2 else 3
        self.states[lsm - 1] = st

    def write(self, address: int, data: bytes) -> None:
        if address == buswrite.LOAD_CONTROL:
            assert len(data) == 11
            if not self.authorized or not self.mem_control:  # like 1.1.83: acknowledged, but ignored
                return
            self.records.append(data)
            self.event(data[0] >> 4, data[0] & 0xF)
            return
        assert 0x4000 <= address < 0x5000, f"write outside EEPROM {address:#06x}"
        for i, b in enumerate(data):
            self.mem[address + i] = b

    def read(self, address: int, n: int) -> bytes:
        if buswrite.LOAD_STATE <= address < buswrite.LOAD_STATE + 4:
            return bytes(self.states[address - buswrite.LOAD_STATE:address - buswrite.LOAD_STATE + n])
        return bytes(self.mem.get(address + i, 0) for i in range(n))


class FakeConn:
    def __init__(self, dev: FakeDevice):
        self.dev = dev
        self.address = IndividualAddress(PA)
        self.guard = bus.ReadOnlyGuard()

    async def send_data(self, payload, wait_for_ack=True):
        self.guard.check(DATA, payload, self.address)
        if isinstance(payload, apci.MemoryWrite):
            self.dev.writes += 1
            if self.dev.fail_after is not None and self.dev.writes > self.dev.fail_after:
                from xknx.exceptions import ManagementConnectionTimeout
                raise ManagementConnectionTimeout("no ACK")
            self.dev.write(payload.address, payload.data)
        elif isinstance(payload, apci.Restart):
            self.dev.restarts += 1

    async def request(self, payload):
        self.guard.check(DATA, payload, self.address)
        if isinstance(payload, apci.MemoryRead):
            return SimpleNamespace(payload=apci.MemoryResponse(address=payload.address,
                                                               data=self.dev.read(payload.address, payload.count)))
        if isinstance(payload, apci.AuthorizeRequest):
            self.dev.authorized = True
            return SimpleNamespace(payload=apci.AuthorizeResponse(level=0))
        if isinstance(payload, apci.DeviceDescriptorRead):
            return SimpleNamespace(payload=apci.DeviceDescriptorResponse(value=self.dev.mask))
        if isinstance(payload, apci.PropertyValueWrite):
            assert payload.property_id == 5 and len(payload.data) == 10
            self.dev.prop_records.append((payload.object_index, payload.data))
            self.dev.event(payload.object_index, payload.data[0])
            return SimpleNamespace(payload=apci.PropertyValueResponse(
                object_index=payload.object_index, property_id=5, count=1, start_index=1,
                data=bytes([self.dev.states[payload.object_index - 1]])))
        if isinstance(payload, apci.PropertyValueRead) and payload.property_id == 5:
            return SimpleNamespace(payload=apci.PropertyValueResponse(
                object_index=payload.object_index, property_id=5, count=1, start_index=1,
                data=bytes([self.dev.states[payload.object_index - 1]])))
        if isinstance(payload, apci.PropertyValueRead):
            data = self.dev.props.get((payload.object_index, payload.property_id))
            return SimpleNamespace(payload=apci.PropertyValueResponse(
                object_index=payload.object_index, property_id=payload.property_id,
                count=1 if data else 0, start_index=1, data=data or b""))
        raise AssertionError(payload)


class FakeXKNX:
    def __init__(self, dev: FakeDevice):
        self.dev = dev
        guard = bus.ReadOnlyGuard()
        bus.GUARDS[id(self)] = guard
        self.guard = guard
        xknx = self

        class Mgmt:
            async def connect(self, address):
                c = FakeConn(xknx.dev)
                c.guard = guard
                return c

            async def disconnect(self, address):
                pass

            def connection(self, address):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def cm():
                    c = FakeConn(xknx.dev)
                    c.guard = guard
                    yield c
                return cm()

        self.management = Mgmt()


def plan(addr_table: bytes, params: bytes):
    raw = [("Connect", {}),
           ("Unload", {"LsmIdx": "1"}), ("Unload", {"LsmIdx": "2"}), ("Unload", {"LsmIdx": "3"}),
           ("Load", {"LsmIdx": "1"}),
           ("AbsSegment", {"LsmIdx": "1", "SegType": "0", "Address": str(0x4000), "Size": "513", "Access": "255",
                           "MemType": "3", "SegFlags": "128"}),
           ("TaskSegment", {"LsmIdx": "1", "Address": str(0x4000)}),
           ("LoadCompleted", {"LsmIdx": "1"}),
           ("Load", {"LsmIdx": "2"}),
           ("AbsSegment", {"LsmIdx": "2", "SegType": "0", "Address": str(0x4201), "Size": "511", "Access": "255",
                           "MemType": "3", "SegFlags": "128"}),
           ("TaskSegment", {"LsmIdx": "2", "Address": str(0x4201)}),
           ("LoadCompleted", {"LsmIdx": "2"}),
           ("Load", {"LsmIdx": "3"}),
           ("AbsSegment", {"LsmIdx": "3", "SegType": "1", "Address": str(0x0752), "Size": "1", "Access": "0",
                           "MemType": "2", "SegFlags": "0"}),
           ("AbsSegment", {"LsmIdx": "3", "SegType": "0", "Address": str(0x4400), "Size": "8", "Access": "255",
                           "MemType": "3", "SegFlags": "128"}),
           ("TaskSegment", {"LsmIdx": "3", "Address": str(0x4400)}),
           ("LoadCompleted", {"LsmIdx": "3"}),
           ("Restart", {}), ("Disconnect", {})]
    return build(raw, {0x4400: params}, {1: addr_table, 2: ASSOC}, {})


def device_mem(addr_table: bytes, params: bytes) -> dict[int, int]:
    mem = {0x4000 + i: b for i, b in enumerate(addr_table)}
    mem.update({0x4201 + i: b for i, b in enumerate(ASSOC)})
    mem.update({0x4400 + i: b for i, b in enumerate(params)})
    return mem


ADDR = bytes.fromhex("02 1153 3201".replace(" ", ""))  # 1.1.83, one group address
PARAMS = bytes(range(8))
ASSOC = bytes.fromhex("010100")


def go(dev, steps, tmp_path, write):
    store = Store(tmp_path / "t.sqlite")
    return asyncio.run(buswrite.run(FakeXKNX(dev), store, PA, 0x0705, IDENT, steps, tmp_path / "backups",
                                    write=write, settle=0))


def test_check_only_reads_and_backs_up(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    rep = go(dev, plan(ADDR, PARAMS), tmp_path, write=False)
    assert dev.records == [] and dev.writes == 0 and dev.restarts == 0
    assert rep.changed == 0 and rep.planned == len(ADDR) + len(ASSOC) + len(PARAMS)
    assert rep.backup.exists() and '"0x4000"' in rep.backup.read_text()


def test_identical_download_writes_and_reads_back(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    rep = go(dev, plan(ADDR, PARAMS), tmp_path, write=True)
    assert rep.error == ""
    assert dev.states[:3] == [1, 1, 1] and dev.restarts == 1
    assert rep.after["abweichend"] == 0 and rep.states_after[:3] == [1, 1, 1]
    firsts = [r[0] for r in dev.records]
    assert firsts[:3] == [0x14, 0x24, 0x34]  # unload 1, 2, 3
    task3 = next(r for r in dev.records if r[0] == 0x33 and r[1] == 2)
    assert task3[5:] == bytes([1, 0x00, 0x83, 0x00, 0xB6, 0x32])
    stack = next(r for r in dev.records if r[0] == 0x33 and r[1] == 1)
    assert stack.hex(" ") == "33 01 00 07 52 00 01 00 02 00 00"


def test_changed_parameter_is_written(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    new = bytes([7]) + PARAMS[1:]
    rep = go(dev, plan(ADDR, new), tmp_path, write=True)
    assert rep.changed == 1 and rep.after["abweichend"] == 0 and dev.mem[0x4400] == 7


def test_wrong_application_is_refused(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    dev.props[(3, 13)] = bytes.fromhex("008300B631")
    with pytest.raises(DownloadError, match="Applikation"):
        go(dev, plan(ADDR, PARAMS), tmp_path, write=True)
    assert dev.writes == 0


def test_wrong_mask_is_refused(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS), mask=0x07B0)
    with pytest.raises(DownloadError, match="Maske"):
        go(dev, plan(ADDR, PARAMS), tmp_path, write=True)
    assert dev.writes == 0


def test_own_address_in_table_must_match(tmp_path):
    other = bytes.fromhex("0211543201")  # 1.1.84
    dev = FakeDevice(device_mem(other, PARAMS))
    with pytest.raises(DownloadError, match="eigenen Adresse"):
        go(dev, plan(other, PARAMS), tmp_path, write=True)
    assert dev.writes == 0


def test_abort_is_reported_with_device_state(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    dev.fail_after = 5  # connection lost in the middle of the address table
    rep = go(dev, plan(ADDR, PARAMS), tmp_path, write=True)
    assert "no ACK" in rep.error and dev.restarts == 0
    assert rep.states_after[0] == 2  # address table left "loading" – visible to the user


def test_writes_outside_the_plan_are_blocked(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    conn = FakeConn(dev)
    target = buswrite.BusTarget(conn, None, IDENT)
    guard = buswrite.PlanGuard(target, plan(ADDR, PARAMS))
    with pytest.raises(DownloadError):
        asyncio.run(guard.write_mem(0x4100, b"\x00"))
    assert dev.writes == 0


def test_property_load_control_when_memory_records_are_ignored(tmp_path):
    dev = FakeDevice(device_mem(ADDR, PARAMS))
    dev.mem_control = False
    rep = go(dev, plan(ADDR, PARAMS), tmp_path, write=True)
    assert rep.error == "" and rep.after["abweichend"] == 0 and dev.states[:3] == [1, 1, 1]
    assert dev.records == []
    first = dev.prop_records[0]
    assert first == (1, bytes([4]) + bytes(9))  # unload address table
    task3 = next(d for o, d in dev.prop_records if o == 3 and d[:2] == b"\x03\x02")
    assert task3.hex(" ") == "03 02 44 00 01 00 83 00 b6 32"
    seg = next(d for o, d in dev.prop_records if o == 1 and d[:2] == b"\x03\x00")
    assert seg.hex(" ") == "03 00 40 00 02 01 ff 03 80 00"


def test_records_property_form():
    assert buswrite.prop_event_record("completed") == bytes([2]) + bytes(9)
    assert buswrite.prop_task_record(0x4200, None) == bytes([3, 2, 0x42, 0]) + bytes(6)
