# Bus logger: ts-knx-relay in logger-only mode (no relay, never sends to the bus).
# Build context is deploy/ (see compose.yaml), so the script is shared with ts-router.
FROM python:3.12-slim

COPY ts-router/device/bin/ts-knx-relay /usr/local/bin/ts-knx-relay

RUN useradd --system --uid 10001 --home-dir /data knxlog \
    && mkdir -p /data && chown knxlog /data

ENV KNX_RELAY_LISTEN=off \
    KNX_REQUIRE_TIME_SYNC=no \
    KNX_LOG=yes \
    KNX_LOG_DIR=/data \
    PYTHONUNBUFFERED=1

USER knxlog
VOLUME ["/data"]
ENTRYPOINT ["python3", "/usr/local/bin/ts-knx-relay"]
