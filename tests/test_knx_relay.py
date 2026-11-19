"""ts-knx-relay (deploy/ts-router): HPAI rewriting and a full round trip on localhost."""

import asyncio
import gzip
import importlib.machinery
import importlib.util
import json
import socket
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "ts-router" / "device" / "bin" / "ts-knx-relay"
_loader = importlib.machinery.SourceFileLoader("ts_knx_relay", str(SCRIPT))
_spec = importlib.util.spec_from_loader("ts_knx_relay", _loader)
relay = importlib.util.module_from_spec(_spec)
_loader.exec_module(relay)

RB = relay.ROUTE_BACK_HPAI


def frame(service: int, body: bytes) -> bytes:
    return bytes([0x06, 0x10]) + service.to_bytes(2, "big") + (6 + len(body)).to_bytes(2, "big") + body


def hpai(ip: str, port: int) -> bytes:
    return relay.udp_hpai(ip, port)


CRI_TUNNEL = bytes([4, 4, 2, 0])  # tunnel connection, link layer


# --- rewriting -------------------------------------------------------------------------------

@pytest.mark.parametrize(("service", "body", "offsets"), [
    (relay.DESCRIPTION_REQUEST, RB, (6,)),
    (relay.SEARCH_REQUEST, RB, (6,)),
    (relay.CONNECT_REQUEST, RB + RB + CRI_TUNNEL, (6, 14)),
    (relay.CONNECTIONSTATE_REQUEST, bytes([7, 0]) + RB, (8,)),
    (relay.DISCONNECT_REQUEST, bytes([7, 0]) + RB, (8,)),
])
def test_requests_point_to_the_relay(service: int, body: bytes, offsets: tuple[int, ...]) -> None:
    out = relay.rewrite_to_target(frame(service, body), "192.168.6.128", 40000)
    for offset in offsets:
        assert out[offset:offset + 8] == hpai("192.168.6.128", 40000)
    assert len(out) == 6 + len(body)


def test_tunnelling_frames_pass_unchanged() -> None:
    f = frame(0x0420, bytes([4, 7, 0, 0]) + bytes.fromhex("1100bce0110a0b010080"))
    assert relay.rewrite_to_target(f, "192.168.6.128", 40000) == f
    assert relay.rewrite_to_client(f) == f


def test_tcp_hpai_is_left_alone() -> None:
    tcp = bytes([8, 2, 0, 0, 0, 0, 0, 0])
    f = frame(relay.DESCRIPTION_REQUEST, tcp)
    assert relay.rewrite_to_target(f, "192.168.6.128", 40000) == f


def test_connect_response_hides_data_endpoint() -> None:
    f = frame(relay.CONNECT_RESPONSE, bytes([7, 0]) + hpai("192.168.6.20", 3672) + bytes([4, 4, 0x11, 0xFD]))
    assert relay.data_endpoint(f) == ("192.168.6.20", 3672)
    out = relay.rewrite_to_client(f)
    assert out[8:16] == RB and out[16:] == f[16:]


def test_failed_connect_response_unchanged() -> None:
    f = frame(relay.CONNECT_RESPONSE, bytes([0, 0x24]))  # E_NO_MORE_CONNECTIONS
    assert relay.rewrite_to_client(f) == f
    assert relay.data_endpoint(f) is None


def test_search_response_hides_control_endpoint() -> None:
    f = frame(relay.SEARCH_RESPONSE, hpai("192.168.6.20", 3671) + bytes(54))
    assert relay.rewrite_to_client(f)[6:14] == RB


def test_control_and_data_services() -> None:
    assert relay.is_control(relay.CONNECTIONSTATE_REQUEST)
    assert not relay.is_control(0x0420) and not relay.is_control(0x0310)
    assert relay.service_type(b"\x00\x01") is None


def test_target_fixed_and_auto() -> None:
    fixed = relay.Target("192.168.6.20")
    assert asyncio.run(fixed.resolve()) == "192.168.6.20"
    calls = []
    auto = relay.Target("auto", discover_fn=lambda: calls.append(1) or "192.168.6.20")
    assert asyncio.run(auto.resolve()) == "192.168.6.20"
    assert asyncio.run(auto.resolve()) == "192.168.6.20" and len(calls) == 1  # cached
    none = relay.Target("auto", discover_fn=lambda: None)
    assert asyncio.run(none.resolve()) is None


def test_idle_sessions_expire() -> None:
    r = relay.Relay(relay.Target("127.0.0.1"))
    s = relay.Session(r, ("100.101.88.38", 50000), "127.0.0.1")
    r.sessions[s.client] = s
    r.expire(now=s.last + relay.IDLE_TIMEOUT - 1)
    assert s.client in r.sessions
    r.expire(now=s.last + relay.IDLE_TIMEOUT + 1)
    assert s.client not in r.sessions


# --- full round trip ---------------------------------------------------------------------------

class Queue(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.q.put_nowait((data, addr))


class OldInterface(asyncio.DatagramProtocol):
    """Answers only to explicit HPAIs (like the Siemens interface at the customer), data on its own port."""

    def __init__(self) -> None:
        self.data_port = 0
        self.data_hpai: tuple[str, int] | None = None
        self.ignored = 0

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    @staticmethod
    def addr(h: bytes) -> tuple[str, int] | None:
        ip, port = socket.inet_ntoa(h[2:6]), int.from_bytes(h[6:8], "big")
        return None if ip == "0.0.0.0" else (ip, port)

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        st = relay.service_type(data)
        if st == relay.DESCRIPTION_REQUEST:
            to = self.addr(data[6:14])
            reply = frame(0x0204, bytes([54, 1, 2, 0, 0x11, 0xFD]) + bytes(48))
        elif st == relay.CONNECT_REQUEST:
            to, self.data_hpai = self.addr(data[6:14]), self.addr(data[14:22])
            reply = frame(relay.CONNECT_RESPONSE, bytes([7, 0]) + hpai("127.0.0.1", self.data_port) + bytes([4, 4, 0x11, 0xFD]))
        elif st == relay.CONNECTIONSTATE_REQUEST:
            to = self.addr(data[8:16])
            reply = frame(0x0208, bytes([7, 0]))
        else:
            return
        if to is None:
            self.ignored += 1
            return
        self.transport.sendto(reply, to)


class DataEndpoint(asyncio.DatagramProtocol):
    def __init__(self, iface: OldInterface) -> None:
        self.iface = iface
        self.received: list[bytes] = []

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        if relay.service_type(data) == 0x0420 and self.iface.data_hpai:
            self.received.append(data)
            self.transport.sendto(frame(0x0421, bytes([4, data[7], data[8], 0])), self.iface.data_hpai)


def test_round_trip_through_relay() -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        iface = OldInterface()
        ctrl_t, _ = await loop.create_datagram_endpoint(lambda: iface, local_addr=("127.0.0.1", 0))
        data_proto = DataEndpoint(iface)
        data_t, _ = await loop.create_datagram_endpoint(lambda: data_proto, local_addr=("127.0.0.1", 0))
        iface.data_port = data_t.get_extra_info("sockname")[1]
        target = relay.Target("127.0.0.1", port=ctrl_t.get_extra_info("sockname")[1])
        r = relay.Relay(target, lan_ip_fn=lambda ip: "127.0.0.1")
        relay_t, _ = await loop.create_datagram_endpoint(lambda: r, local_addr=("127.0.0.1", 0))
        relay_addr = relay_t.get_extra_info("sockname")[:2]
        client = Queue()
        client_t, _ = await loop.create_datagram_endpoint(lambda: client, local_addr=("127.0.0.1", 0))

        async def ask(f: bytes) -> tuple[bytes, tuple]:
            client_t.sendto(f, relay_addr)
            return await asyncio.wait_for(client.q.get(), 2)

        try:
            # Without the relay the interface ignores route-back requests
            client_t.sendto(frame(relay.DESCRIPTION_REQUEST, RB), ctrl_t.get_extra_info("sockname")[:2])
            await asyncio.sleep(0.1)
            assert iface.ignored == 1

            d, src = await ask(frame(relay.DESCRIPTION_REQUEST, RB))
            assert relay.service_type(d) == 0x0204 and src[:2] == relay_addr

            d, src = await ask(frame(relay.CONNECT_REQUEST, RB + RB + CRI_TUNNEL))
            assert relay.service_type(d) == relay.CONNECT_RESPONSE and d[7] == 0
            assert d[8:16] == RB  # client keeps sending data to the relay

            d, _ = await ask(frame(0x0420, bytes([4, 7, 0, 0]) + bytes.fromhex("1100bce0110a0b010080")))
            assert relay.service_type(d) == 0x0421 and d[7:9] == bytes([7, 0])
            assert len(data_proto.received) == 1  # went to the interface's data endpoint

            d, _ = await ask(frame(relay.CONNECTIONSTATE_REQUEST, bytes([7, 0]) + RB))
            assert relay.service_type(d) == 0x0208
            assert iface.ignored == 1
            assert len(r.sessions) == 1
        finally:
            for t in (client_t, relay_t, data_t, ctrl_t):
                t.close()
            for c in list(r.sessions):
                r.close(c)

    asyncio.run(run())


# --- bus log -----------------------------------------------------------------------------------

def cemi(code: int, src: int, dst: int, apdu: bytes, group: bool = True) -> bytes:
    ctrl2 = 0xE0 if group else 0x60
    return bytes([code, 0, 0xBC, ctrl2]) + src.to_bytes(2, "big") + dst.to_bytes(2, "big") + bytes([len(apdu) - 1]) + apdu


def test_decode_group_telegrams() -> None:
    w = relay.decode_cemi(cemi(0x29, 0x1111, 0x193D, bytes([0x00, 0x81])))  # 1.1.17 → 3/1/61 write 1
    assert w["src"] == "1.1.17" and w["dst"] == "3/1/61" and w["apci"] == "GroupValueWrite" and w["value"] == 1
    t = relay.decode_cemi(cemi(0x29, 0x11F9, 0x0305, bytes([0x00, 0x80, 0x0C, 0x1A])))  # 2-byte float
    assert t["dst"] == "0/3/5" and t["data"] == "0c1a" and "value" not in t
    r = relay.decode_cemi(cemi(0x29, 0x1101, 0x0305, bytes([0x00, 0x00])))
    assert r["apci"] == "GroupValueRead" and "value" not in r and "data" not in r
    a = relay.decode_cemi(cemi(0x29, 0x1101, 0x0305, bytes([0x00, 0x41])))
    assert a["apci"] == "GroupValueResponse" and a["value"] == 1


def test_decode_individual_and_short() -> None:
    d = relay.decode_cemi(cemi(0x29, 0x1101, 0x1105, bytes([0x43, 0x00]), group=False))
    assert d["dst"] == "1.1.5" and d["apci"] == "0x300"
    c = relay.decode_cemi(cemi(0x29, 0x1101, 0x1105, bytes([0x80]), group=False))
    assert c["apci"] == "tpci"
    assert relay.decode_cemi(b"\x29") is None


DAY = 86400.0
T0 = 1_794_873_600.0  # 2026-11-17 00:00 UTC


def make_log(tmp_path, **kw):  # noqa: ANN001, ANN201
    now = [T0 + 3600]
    tl = relay.TelegramLog(tmp_path, clock=lambda: now[0], **kw)
    return tl, now


def test_log_writes_one_file_per_day(tmp_path) -> None:  # noqa: ANN001
    tl, now = make_log(tmp_path)
    tl.add({"dir": "rx", "src": "1.1.1"})
    now[0] += DAY
    tl.add({"dir": "rx", "src": "1.1.2"})
    tl.flush()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["telegrams-2026-11-17.jsonl", "telegrams-2026-11-18.jsonl"]
    line = json.loads((tmp_path / names[0]).read_text())
    assert line["src"] == "1.1.1" and line["ts"].startswith("2026-11-17T01:00:00")


def test_log_buffer_is_bounded(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relay.TelegramLog, "MAX_BUFFER", 3)
    tl, _ = make_log(tmp_path)
    for i in range(5):
        tl.add({"n": i})
    assert len(tl.buffer) == 3 and tl.dropped == 2


def test_housekeeping_compresses_and_expires(tmp_path) -> None:  # noqa: ANN001
    tl, now = make_log(tmp_path, keep_days=2)
    for d in range(4):
        (tmp_path / f"telegrams-2026-11-1{4 + d}.jsonl").write_text('{"n":1}\n')
    (tmp_path / "telegrams-2026-11-16.jsonl.gz.tmp").write_text("junk")
    assert tl.housekeeping() is None
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["telegrams-2026-11-15.jsonl.gz", "telegrams-2026-11-16.jsonl.gz", "telegrams-2026-11-17.jsonl"]
    assert gzip.open(tmp_path / names[0]).read() == b'{"n":1}\n'


def test_late_lines_are_appended_to_compressed_day(tmp_path) -> None:  # noqa: ANN001
    tl, _ = make_log(tmp_path)
    (tmp_path / "telegrams-2026-11-16.jsonl").write_text("a\n")
    tl.housekeeping()
    (tmp_path / "telegrams-2026-11-16.jsonl").write_text("b\n")
    tl.housekeeping()
    assert gzip.open(tmp_path / "telegrams-2026-11-16.jsonl.gz").read() == b"a\nb\n"


def test_size_cap_deletes_oldest_but_never_today(tmp_path) -> None:  # noqa: ANN001
    tl, _ = make_log(tmp_path, max_bytes=2500, min_free_bytes=0)
    for d in (14, 15, 16):
        (tmp_path / f"telegrams-2026-11-{d}.jsonl.gz").write_bytes(bytes(1000))
    (tmp_path / "telegrams-2026-11-17.jsonl").write_bytes(bytes(1000))
    assert tl.housekeeping() is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["telegrams-2026-11-16.jsonl.gz", "telegrams-2026-11-17.jsonl"]
    tl.max_bytes = 500  # today alone is too big: pause instead of deleting it
    assert "storage limit" in tl.housekeeping()
    assert (tmp_path / "telegrams-2026-11-17.jsonl").exists()


def test_low_disk_space_pauses_and_resumes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    tl, _ = make_log(tmp_path, min_free_bytes=1000)
    (tmp_path / "telegrams-2026-11-16.jsonl.gz").write_bytes(b"x")
    free = [10]
    monkeypatch.setattr(tl, "free_bytes", lambda: free[0])
    reason = tl.housekeeping()
    assert reason and not (tmp_path / "telegrams-2026-11-16.jsonl.gz").exists()  # our old files go first
    tl.set_paused(reason)
    tl.add({"n": 1})
    assert not tl.writable and tl.buffer == [] and tl.dropped == 1
    free[0] = 10_000
    tl.set_paused(tl.housekeeping())
    assert tl.writable and json.loads(tl.buffer[0][1])["dropped"] == 1


def test_write_error_pauses(tmp_path) -> None:  # noqa: ANN001
    blocker = tmp_path / "file"
    blocker.write_text("")
    tl, _ = make_log(blocker / "sub")  # cannot create a directory below a file
    tl.add({"n": 1})
    tl.flush()
    assert not tl.writable and tl.dropped == 1


# --- recorder and handover ---------------------------------------------------------------------

class SingleTunnelInterface(asyncio.DatagramProtocol):
    """One tunnel only, answers to explicit HPAIs, data on its own port (like the Siemens interface)."""

    def __init__(self) -> None:
        self.owner: tuple[str, int] | None = None  # data HPAI of the tunnel holder
        self.channel = 0
        self.seq = 0
        self.acks: list[int] = []
        self.refused = 0
        self.data_port = 0
        self.data_t = None

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    @staticmethod
    def addr(h: bytes) -> tuple[str, int]:
        return socket.inet_ntoa(h[2:6]), int.from_bytes(h[6:8], "big")

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        st = relay.service_type(data)
        if st == relay.CONNECT_REQUEST:
            ctrl = self.addr(data[6:14])
            if self.owner is not None:
                self.refused += 1
                self.transport.sendto(frame(relay.CONNECT_RESPONSE, bytes([0, 0x24])), ctrl)
                return
            self.owner, self.channel, self.seq = self.addr(data[14:22]), self.channel + 1, 0
            body = bytes([self.channel, 0]) + hpai("127.0.0.1", self.data_port) + bytes([4, 4, 0x11, 0xFD])
            self.transport.sendto(frame(relay.CONNECT_RESPONSE, body), ctrl)
        elif st == relay.CONNECTIONSTATE_REQUEST:
            self.transport.sendto(frame(relay.CONNECTIONSTATE_RESPONSE, bytes([data[6], 0])), self.addr(data[8:16]))
        elif st == relay.DISCONNECT_REQUEST:
            if data[6] == self.channel:
                self.owner = None
            self.transport.sendto(frame(relay.DISCONNECT_RESPONSE, bytes([data[6], 0])), self.addr(data[8:16]))

    def indicate(self, cemi_frame: bytes, repeat: bool = False) -> None:
        if not repeat:
            self.seq = (self.seq + 1) % 256
        self.data_t.sendto(frame(relay.TUNNELLING_REQUEST, bytes([4, self.channel, self.seq, 0]) + cemi_frame), self.owner)


class InterfaceData(asyncio.DatagramProtocol):
    def __init__(self, iface: SingleTunnelInterface) -> None:
        self.iface = iface

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        if relay.service_type(data) == relay.TUNNELLING_ACK:
            self.iface.acks.append(data[8])


async def until(cond, timeout: float = 3.0) -> None:  # noqa: ANN001
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "condition not met in time"
        await asyncio.sleep(0.02)


def test_recorder_logs_and_hands_over(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relay.Recorder, "POLL", 0.05)
    monkeypatch.setattr(relay.Recorder, "QUIET", 0.2)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        iface = SingleTunnelInterface()
        ctrl_t, _ = await loop.create_datagram_endpoint(lambda: iface, local_addr=("127.0.0.1", 0))
        iface.data_t, _ = await loop.create_datagram_endpoint(lambda: InterfaceData(iface), local_addr=("127.0.0.1", 0))
        iface.data_port = iface.data_t.get_extra_info("sockname")[1]
        target = relay.Target("127.0.0.1", port=ctrl_t.get_extra_info("sockname")[1])
        tl = relay.TelegramLog(tmp_path)
        r = relay.Relay(target, lan_ip_fn=lambda ip: "127.0.0.1", telegram_log=tl)
        rec = relay.Recorder(target, tl, r.tunnel_in_use, lan_ip_fn=lambda ip: "127.0.0.1", synced_fn=lambda: True)
        r.recorder = rec
        relay_t, _ = await loop.create_datagram_endpoint(lambda: r, local_addr=("127.0.0.1", 0))
        relay_addr = relay_t.get_extra_info("sockname")[:2]
        client = Queue()
        client_t, _ = await loop.create_datagram_endpoint(lambda: client, local_addr=("127.0.0.1", 0))
        task = loop.create_task(rec.run())

        async def ask(f: bytes) -> bytes:
            client_t.sendto(f, relay_addr)
            return (await asyncio.wait_for(client.q.get(), 3))[0]

        def lines() -> list[dict]:
            return [json.loads(line) for _, line in tl.buffer]

        try:
            # 1. recorder holds the tunnel and logs, a repeated frame only once
            await until(lambda: rec.state == "recording")
            ind = cemi(0x29, 0x1111, 0x193D, bytes([0x00, 0x81]))
            iface.indicate(ind)
            iface.indicate(ind, repeat=True)
            await until(lambda: len(iface.acks) == 2)
            assert [x["dst"] for x in lines() if "dst" in x] == ["3/1/61"]
            assert lines()[-1]["via"] == "recorder"

            # 2. a client connects: the recorder steps back, the client gets the only tunnel
            d = await ask(frame(relay.CONNECT_REQUEST, RB + RB + CRI_TUNNEL))
            assert d[7] == 0 and iface.refused == 0
            assert rec.state == "handed_over" and r.tunnel_in_use()
            iface.indicate(cemi(0x29, 0x11F9, 0x0305, bytes([0x00, 0x80, 0x0C, 0x1A])))
            d = await asyncio.wait_for(client.q.get(), 3)
            assert relay.service_type(d[0]) == relay.TUNNELLING_REQUEST
            client_t.sendto(frame(relay.TUNNELLING_ACK, bytes([4, d[0][7], d[0][8], 0])), relay_addr)
            tx = cemi(0x11, 0, 0x0A01, bytes([0x00, 0x80]))
            client_t.sendto(frame(relay.TUNNELLING_REQUEST, bytes([4, d[0][7], 0, 0]) + tx), relay_addr)
            await until(lambda: any(x.get("dir") == "tx" for x in lines()))
            session_lines = [x for x in lines() if x.get("via") == "session"]
            assert [(x["dir"], x["dst"]) for x in session_lines] == [("rx", "0/3/5"), ("tx", "1/2/1")]

            # 3. the client disconnects: the recorder takes the tunnel back
            d = await ask(frame(relay.DISCONNECT_REQUEST, bytes([iface.channel, 0]) + RB))
            assert relay.service_type(d) == relay.DISCONNECT_RESPONSE
            await until(lambda: rec.state == "recording")
            assert iface.refused == 0
            events = [x["event"] for x in lines() if "event" in x]
            assert events == ["recorder_recording", "recorder_handed_over", "recorder_recording"]

            # 4. stopping frees the interface
            await rec.release()
            assert iface.owner is None
        finally:
            task.cancel()
            for t in (client_t, relay_t, iface.data_t, ctrl_t):
                t.close()
            for c in list(r.sessions):
                r.close(c)

    asyncio.run(run())


def test_recorder_waits_when_interface_busy_or_clock_unsynced(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relay.Recorder, "POLL", 0.05)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        iface = SingleTunnelInterface()
        iface.owner = ("127.0.0.1", 9)  # somebody else (e.g. ETS on site) holds the tunnel
        ctrl_t, _ = await loop.create_datagram_endpoint(lambda: iface, local_addr=("127.0.0.1", 0))
        target = relay.Target("127.0.0.1", port=ctrl_t.get_extra_info("sockname")[1])
        tl = relay.TelegramLog(tmp_path)
        synced = [False]
        rec = relay.Recorder(target, tl, lambda: False, lan_ip_fn=lambda ip: "127.0.0.1", synced_fn=lambda: synced[0])
        task = loop.create_task(rec.run())
        try:
            await until(lambda: rec.state == "waiting_for_time_sync")
            synced[0] = True
            await until(lambda: rec.state == "busy")
            assert iface.refused == 1 and not rec.holding
        finally:
            task.cancel()
            ctrl_t.close()

    asyncio.run(run())


def test_time_sync_check_can_be_switched_off(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(relay.Path, "exists", lambda self: False)
    monkeypatch.delenv("KNX_REQUIRE_TIME_SYNC", raising=False)
    assert not relay.time_synced()
    monkeypatch.setenv("KNX_REQUIRE_TIME_SYNC", "no")
    assert relay.time_synced()


def test_logger_only_without_relay(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """KNX_RELAY_LISTEN=off: serve() opens no listen socket, the recorder still logs the bus."""
    monkeypatch.setattr(relay.Recorder, "POLL", 0.05)
    monkeypatch.setenv("KNX_REQUIRE_TIME_SYNC", "no")

    async def run() -> None:
        loop = asyncio.get_running_loop()
        iface = SingleTunnelInterface()
        ctrl_t, _ = await loop.create_datagram_endpoint(lambda: iface, local_addr=("127.0.0.1", 0))
        iface.data_t, _ = await loop.create_datagram_endpoint(lambda: InterfaceData(iface), local_addr=("127.0.0.1", 0))
        iface.data_port = iface.data_t.get_extra_info("sockname")[1]
        target = relay.Target("127.0.0.1", port=ctrl_t.get_extra_info("sockname")[1])
        tl = relay.TelegramLog(tmp_path)
        task = loop.create_task(relay.serve(None, 0, target, tl))
        try:
            await until(lambda: iface.owner is not None)
            iface.indicate(cemi(0x29, 0x1111, 0x193D, bytes([0x00, 0x81])))
            await until(lambda: len(iface.acks) == 1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert iface.owner is None  # the tunnel was given back on stop
            iface.data_t.close()
            ctrl_t.close()

    asyncio.run(run())
    lines = [json.loads(line) for f in tmp_path.glob("*.jsonl") for line in f.read_text().splitlines()]
    assert [x["dst"] for x in lines if "dst" in x] == ["3/1/61"]
    assert [x["event"] for x in lines if "event" in x][-1] == "relay_stopped"
