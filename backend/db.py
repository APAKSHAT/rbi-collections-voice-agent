"""SQLite storage. Plain sqlite3, one connection per Store, rows returned as dicts."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config import IST, ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS borrowers (
    borrower_id TEXT PRIMARY KEY,
    borrower_name TEXT NOT NULL,
    phone_e164 TEXT NOT NULL,
    preferred_language TEXT NOT NULL,
    loan_type TEXT NOT NULL,
    loan_last4 TEXT NOT NULL,
    emi_amount_inr REAL NOT NULL,
    due_date TEXT NOT NULL,
    outstanding_amount_inr REAL NOT NULL,
    birth_year INTEGER NOT NULL,
    registered_city TEXT NOT NULL,
    do_not_call_windows TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS call_attempts (
    id INTEGER PRIMARY KEY,
    borrower_id TEXT NOT NULL,
    call_id TEXT,
    channel TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    refusal_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    borrower_id TEXT,
    call_type TEXT,
    agent_id TEXT,
    agent_version INTEGER,
    started_at TEXT,
    ended_at TEXT,
    disconnection_reason TEXT,
    transcript_json TEXT,
    recording_url TEXT,
    recording_notice_given INTEGER,
    analysis_json TEXT,
    outcome TEXT,
    user_sentiment TEXT,
    call_summary TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_state (
    call_id TEXT PRIMARY KEY,
    borrower_id TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    identity_attempts INTEGER NOT NULL DEFAULT 0,
    links_sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    promised_date TEXT NOT NULL,
    amount_inr REAL NOT NULL,
    mode TEXT NOT NULL,
    partial INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_links (
    link_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hardship_flags (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL,
    cooloff_until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS callbacks (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    booked_with_borrower INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS human_queue (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    note TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stop_requests (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL,
    borrower_id TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY,
    call_id TEXT,
    event TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);
"""


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(IST)).astimezone(IST).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # Generic helpers

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # Borrowers

    def load_borrowers(self, csv_path: Path | None = None) -> int:
        csv_path = csv_path or ROOT / "data" / "fake_borrowers.csv"
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        ids = [r["borrower_id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate borrower_id in {csv_path}")
        for r in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO borrowers VALUES "
                "(:borrower_id, :borrower_name, :phone_e164, :preferred_language, :loan_type, "
                ":loan_last4, :emi_amount_inr, :due_date, :outstanding_amount_inr, :birth_year, "
                ":registered_city, :do_not_call_windows)",
                r,
            )
        self.conn.commit()
        return len(rows)

    def borrower(self, borrower_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM borrowers WHERE borrower_id = ?", (borrower_id,))

    # Per-call state

    def call_state(self, call_id: str, borrower_id: str) -> dict[str, Any]:
        self.conn.execute(
            "INSERT OR IGNORE INTO call_state (call_id, borrower_id) VALUES (?, ?)", (call_id, borrower_id)
        )
        self.conn.commit()
        state = self.one("SELECT * FROM call_state WHERE call_id = ?", (call_id,))
        assert state is not None
        return state

    def update_call_state(self, call_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE call_state SET {sets} WHERE call_id = ?", [*fields.values(), call_id])
        self.conn.commit()

    def log_tool_event(self, call_id: str, tool: str, args: dict[str, Any], result: dict[str, Any], now: datetime) -> None:
        self.insert(
            "tool_events",
            {
                "call_id": call_id,
                "tool_name": tool,
                "args_json": json.dumps(args, ensure_ascii=False),
                "result_json": json.dumps(result, ensure_ascii=False),
                "created_at": now_iso(now),
            },
        )

    # Pre-dial facts

    def attempts_on(self, borrower_id: str, day: date) -> list[datetime]:
        rows = self.all(
            "SELECT attempted_at FROM call_attempts WHERE borrower_id = ? AND status = 'placed' "
            "AND substr(attempted_at, 1, 10) = ?",
            (borrower_id, day.isoformat()),
        )
        return [datetime.fromisoformat(r["attempted_at"]) for r in rows]

    def latest_cooloff(self, borrower_id: str) -> datetime | None:
        row = self.one(
            "SELECT MAX(cooloff_until) AS until FROM hardship_flags WHERE borrower_id = ? AND cooloff_until IS NOT NULL",
            (borrower_id,),
        )
        return datetime.fromisoformat(row["until"]) if row and row["until"] else None

    def stop_request_open(self, borrower_id: str) -> bool:
        row = self.one(
            "SELECT 1 AS x FROM stop_requests WHERE borrower_id = ? AND status = 'open' LIMIT 1", (borrower_id,)
        )
        return row is not None

    # Webhooks

    def upsert_call(self, call_id: str, fields: dict[str, Any], now: datetime) -> None:
        fields = {**fields, "updated_at": now_iso(now)}
        self.conn.execute(
            "INSERT INTO calls (call_id, updated_at) VALUES (?, ?) ON CONFLICT(call_id) DO NOTHING",
            (call_id, fields["updated_at"]),
        )
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE calls SET {sets} WHERE call_id = ?", [*fields.values(), call_id])
        self.conn.commit()
