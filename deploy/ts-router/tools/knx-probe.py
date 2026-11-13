#!/usr/bin/env python3
"""KNXnet/IP-Interface per Unicast prüfen: DESCRIPTION_REQUEST an UDP 3671.

    python3 knx-probe.py <ip> [port]

Der Request trägt das HPAI 0.0.0.0:0 (Route-Back): Das Interface antwortet an
die Absenderadresse des Pakets. Hinter dem Subnetz-Router (Source-NAT) ist das
die LAN-Adresse des Odroid, der die Antwort zurück durch Tailscale schickt.
Kein Multicast, kein Tunnelaufbau, nichts wird auf den Bus geschrieben.
"""

import socket
import sys

ip = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 3671

# Header (06 10, Service 0x0203, Länge 14) + Control-HPAI (UDP, 0.0.0.0:0)
request = bytes.fromhex("06100203000e" "0801" "00000000" "0000")

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.settimeout(3)
    s.sendto(request, (ip, port))
    try:
        data, peer = s.recvfrom(1024)
    except TimeoutError:
        sys.exit(f"Keine Antwort von {ip}:{port} innerhalb von 3 s")

if len(data) < 8 or data[2:4] != b"\x02\x04":
    sys.exit(f"Unerwartete Antwort von {peer}: {data.hex()}")

dib = data[6:]  # Device-Information-DIB
addr = dib[4] << 8 | dib[5]
name = dib[24:54].split(b"\x00", 1)[0].decode("latin-1")
mac = dib[18:24].hex(":")
print(f"Antwort von {peer[0]}:{peer[1]}")
print(f"  Name:     {name}")
print(f"  Adresse:  {addr >> 12}.{(addr >> 8) & 0xF}.{addr & 0xFF}")
print(f"  MAC:      {mac}")
