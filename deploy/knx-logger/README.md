# knx-logger: permanent KNX bus log in Docker

Runs `ts-knx-relay` (from `deploy/ts-router`) in logger-only mode: it opens its own
KNXnet/IP tunnel to the interface, only listens, and writes every telegram it sees as
JSON lines, one file per UTC day. It never sends a telegram to the bus. The file format
is the same as on the ts-router ("Busmitschnitt im Relay" in `deploy/ts-router/README.md`),
so the same analysis scripts work.

```
KNX interface (e.g. Gira X1) ──tunnel──► knx-logger container (host network) ──► /data/telegrams-YYYY-MM-DD.jsonl
```

## Requirements

- A Docker host in the same LAN as the KNX interface (UDP 3671, multicast for `auto`).
- A **free tunnel** on the interface. The logger holds one tunnel permanently.
  Check the free slots before you start: a Gira X1 has four tunnels, typically one is
  used by Home Assistant. Leave at least one free for ETS or other tools.
- The host clock must be correct (NTP). The container trusts it (`KNX_REQUIRE_TIME_SYNC=no`).

## Setup with Portainer

Stacks → Add stack → **Repository**:

| Field | Value |
|---|---|
| Repository URL | `https://github.com/MultivitaminJuice/knxlens` |
| Repository reference | `refs/heads/main` |
| Compose path | `deploy/knx-logger/compose.yaml` |
| Environment variables | `KNX_TARGET=<ip of the interface>`, optional the settings below |

Deploy the stack. The container log shows `recorder: recording` once the tunnel is open.
After a change in the repository: stack → **Pull and redeploy** (rebuilds the image; the
log files stay in the volume).

## Setup with docker compose

Copy the `deploy/` directory (or clone the repository) to the Docker host, then:

```bash
cd deploy/knx-logger
echo "KNX_TARGET=192.168.158.20" > .env
docker compose up -d --build
docker compose logs -f
```

## Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `KNX_TARGET` | `auto` | IP address of the interface; `auto` = multicast search (first answer wins) |
| `KNX_DATA` | `knx-logger-data` | named volume, or a host path for a bind mount (must be writable for uid 10001) |
| `KNX_LOG_KEEP_DAYS` | `730` | delete days older than this |
| `KNX_LOG_MAX_MB` | `5000` | size cap of all log files |
| `KNX_LOG_MIN_FREE_MB` | `2000` | pause the log if less disk space is free |

Set `KNX_TARGET` explicitly if more than one KNXnet/IP device answers in the LAN.

## Files

The files live in the volume `knx-logger-data` (mounted at `/data` in the container). To get
them out, for example:

```bash
docker cp knx-logger:/data ./knx-log
```

Or read them on the host under `docker volume inspect <stack>_knx-logger-data` → `Mountpoint`
(Portainer prefixes the volume name with the stack name).

- `telegrams-YYYY-MM-DD.jsonl`: today (UTC), written every 5 s.
- Older days are compressed to `.jsonl.gz`.
- Lines with `event` mark gaps and states (`recorder_recording`, `recorder_lost`,
  `recorder_busy`, `log_resumed`, `relay_started`, `relay_stopped`).

A tunnel delivers group telegrams and telegrams addressed to the interface, not the
complete bus traffic (for example no programming between other devices).

## Operation

- Update after a change of `ts-knx-relay`: Portainer "Pull and redeploy", or `docker compose up -d --build`.
- Stop: stop the container in Portainer, or `docker compose down` (the tunnel is closed cleanly).
  Do this when another tool needs the interface's last free tunnel; the gap is marked in the log.
- If the interface has no free tunnel, the logger retries every minute (`recorder_busy`).
