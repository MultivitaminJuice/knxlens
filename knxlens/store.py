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
    id INTEGER PRIMARY KEY, session INTEGER, ts REAL, src TEXT, dst TEXT, service TEXT,
    payload BLOB,                            -- Nutzbytes (DPTArray)
    bits INTEGER                             -- Kleinwert ≤ 6 Bit im APCI (DPTBinary)
);
CREATE INDEX IF NOT EXISTS telegram_dst ON telegram (dst);
CREATE TABLE IF NOT EXISTS note (            -- Ergänzungen des Nutzers (nicht rekonstruierbar)
    kind TEXT, key TEXT, field TEXT, value TEXT, ts REAL,
    PRIMARY KEY (kind, key, field)
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        # WAL: Monitor-Prozess schreibt, Weboberfläche liest gleichzeitig
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self.db.executescript(SCHEMA)
        self.session: int | None = None

    def _migrate(self) -> None:
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(telegram)")]
        if cols and "bits" not in cols:
            # alte Form ohne id/bits; war bis hier nie befüllt
            if self.db.execute("SELECT COUNT(*) FROM telegram").fetchone()[0]:
                raise RuntimeError("telegram-Tabelle im alten Format enthält Daten – bitte prüfen")
            self.db.execute("DROP TABLE telegram")

    def add_telegrams(self, rows: list[tuple]) -> None:
        """rows: (ts, src, dst, service, payload, bits)"""
        self.db.executemany(
            "INSERT INTO telegram (session, ts, src, dst, service, payload, bits) VALUES (?,?,?,?,?,?,?)",
            [(self.session, *r) for r in rows],
        )
        self.db.commit()

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

    def set_note(self, kind: str, key: str, field: str, value: str) -> None:
        value = value.strip()
        if value:
            self.db.execute(
                "INSERT OR REPLACE INTO note VALUES (?,?,?,?,?)", (kind, key, field, value, time.time())
            )
        else:
            self.db.execute("DELETE FROM note WHERE kind=? AND key=? AND field=?", (kind, key, field))
        self.db.commit()

    def notes(self, kind: str) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for key, field, value in self.db.execute("SELECT key, field, value FROM note WHERE kind=?", (kind,)):
            out.setdefault(key, {})[field] = value
        return out

    def found_devices(self) -> list[tuple[str, int]]:
        """PAs, die im jüngsten Scan geantwortet haben, mit Maskenversion."""
        cur = self.db.execute(
            """SELECT pa, mask FROM probe p WHERE result='device' AND ts = (
                   SELECT MAX(ts) FROM probe q WHERE q.pa = p.pa)"""
        )
        rows = cur.fetchall()
        return sorted(rows, key=lambda r: [int(x) for x in r[0].split(".")])
