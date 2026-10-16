"""Rohdatenspeicher. Jede Busantwort wird unverändert abgelegt; gedeutet wird offline."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY, started REAL, gateway TEXT, own_address TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS probe (           -- Linienscan
    session INTEGER, pa TEXT, result TEXT, mask INTEGER, ms INTEGER, ts REAL
);
CREATE TABLE IF NOT EXISTS read (            -- jede einzelne Leseanfrage
    session INTEGER, pa TEXT, service TEXT, args TEXT, data BLOB, error TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS telegram (        -- passiver Mitschnitt
    session INTEGER, ts REAL, src TEXT, dst TEXT, service TEXT, payload BLOB
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.session: int | None = None

    def start_session(self, gateway: str, own_address: str, note: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO session (started, gateway, own_address, note) VALUES (?,?,?,?)",
            (time.time(), gateway, own_address, note),
        )
        self.db.commit()
        self.session = cur.lastrowid
        return self.session

    def probe(self, pa: str, result: str, mask: int | None, ms: int) -> None:
        self.db.execute(
            "INSERT INTO probe VALUES (?,?,?,?,?,?)",
            (self.session, pa, result, mask, ms, time.time()),
        )
        self.db.commit()

    def read(self, pa: str, service: str, args: dict, data: bytes | None, error: str | None) -> None:
        self.db.execute(
            "INSERT INTO read VALUES (?,?,?,?,?,?,?)",
            (self.session, pa, service, json.dumps(args, sort_keys=True), data, error, time.time()),
        )
        self.db.commit()

    def latest_read(self, pa: str, service: str, args: dict) -> sqlite3.Row | None:
        cur = self.db.execute(
            "SELECT data, error FROM read WHERE pa=? AND service=? AND args=? ORDER BY ts DESC LIMIT 1",
            (pa, service, json.dumps(args, sort_keys=True)),
        )
        return cur.fetchone()

    def found_devices(self) -> list[tuple[str, int]]:
        """PAs, die im jüngsten Scan geantwortet haben, mit Maskenversion."""
        cur = self.db.execute(
            """SELECT pa, mask FROM probe p WHERE result='device' AND ts = (
                   SELECT MAX(ts) FROM probe q WHERE q.pa = p.pa)"""
        )
        rows = cur.fetchall()
        return sorted(rows, key=lambda r: [int(x) for x in r[0].split(".")])
