"""Lab tables. They live in the same SQLite file as the backend so the dashboard reads one database."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.db import Store, now_iso

LAB_SCHEMA = """
CREATE TABLE IF NOT EXISTS suites (
    suite_id TEXT PRIMARY KEY,
    prompt_version TEXT NOT NULL,
    prompt_sha TEXT NOT NULL,
    agent_model TEXT NOT NULL,
    sim_model TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    sim_time TEXT NOT NULL,
    seeds INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    seed INTEGER NOT NULL,
    status TEXT NOT NULL,
    ended_by TEXT,
    turns INTEGER,
    outcome TEXT,
    expected_outcome TEXT NOT NULL,
    events_json TEXT NOT NULL,
    error TEXT,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS check_results (
    run_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (run_id, check_name)
);
CREATE TABLE IF NOT EXISTS judge_results (
    run_id TEXT NOT NULL,
    judge TEXT NOT NULL,
    prompt_sha TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, judge, prompt_sha)
);
CREATE TABLE IF NOT EXISTS labels (
    run_id TEXT NOT NULL,
    judge TEXT NOT NULL,
    label TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (run_id, judge)
);
CREATE TABLE IF NOT EXISTS judge_validation (
    id INTEGER PRIMARY KEY,
    judge TEXT NOT NULL,
    split TEXT NOT NULL,
    prompt_sha TEXT NOT NULL,
    n INTEGER NOT NULL,
    tp INTEGER NOT NULL,
    tn INTEGER NOT NULL,
    fp INTEGER NOT NULL,
    fn INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class LabStore(Store):
    def __init__(self, path: Path | str) -> None:
        super().__init__(path)
        self.conn.executescript(LAB_SCHEMA)

    def save_suite(self, row: dict[str, Any]) -> None:
        if not self.one("SELECT 1 AS x FROM suites WHERE suite_id = ?", (row["suite_id"],)):
            self.insert("suites", row)

    def run_done(self, run_id: str) -> bool:
        return self.one("SELECT 1 AS x FROM runs WHERE run_id = ? AND status = 'ok'", (run_id,)) is not None

    def save_run(self, row: dict[str, Any], checks: list[Any], now: datetime) -> None:
        self.conn.execute("DELETE FROM runs WHERE run_id = ?", (row["run_id"],))
        self.conn.execute("DELETE FROM check_results WHERE run_id = ?", (row["run_id"],))
        self.insert("runs", {**row, "finished_at": now_iso(now)})
        for c in checks:
            self.conn.execute(
                "INSERT INTO check_results VALUES (?, ?, ?, ?)", (row["run_id"], c.name, c.result, c.detail)
            )
        self.conn.commit()

    def save_judge(self, run_id: str, judge: str, prompt_sha: str, verdict: str, reasoning: str, model: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO judge_results VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, judge, prompt_sha, verdict, reasoning, model, now_iso(now)),
        )
        self.conn.commit()

    def cached_judge(self, run_id: str, judge: str, prompt_sha: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT verdict, reasoning FROM judge_results WHERE run_id = ? AND judge = ? AND prompt_sha = ?",
            (run_id, judge, prompt_sha),
        )

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        row = self.one("SELECT events_json FROM runs WHERE run_id = ?", (run_id,))
        return json.loads(row["events_json"]) if row else []
