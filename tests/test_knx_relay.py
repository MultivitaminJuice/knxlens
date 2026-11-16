"""ts-knx-relay (deploy/ts-router): HPAI rewriting and a full round trip on localhost."""

import asyncio
import importlib.machinery
import importlib.util
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
