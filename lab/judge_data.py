"""Labels and splits for judge validation, shared by the suite runner and validate_judges.py.

Every labelled call goes to exactly one split, decided by a hash of its run ID:
few-shot (examples shown inside the judge prompt), dev (for iterating on the
judge prompt) and test (held out, reported once).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from lab.judges import format_few_shot
from lab.store import LabStore

FEW_SHOT_SHARE = 15
DEV_SHARE = 45  # the rest, 40 percent, is test
MAX_FEW_SHOT_PER_LABEL = 2


def split_of(run_id: str, judge: str) -> str:
    bucket = int(hashlib.sha256(f"{run_id}|{judge}".encode()).hexdigest(), 16) % 100
    if bucket < FEW_SHOT_SHARE:
        return "few_shot"
    if bucket < FEW_SHOT_SHARE + DEV_SHARE:
        return "dev"
    return "test"


def labelled(store: LabStore, judge: str, split: str | None = None) -> list[dict[str, Any]]:
    rows = store.all(
        "SELECT l.run_id, l.label, l.note, r.events_json FROM labels l JOIN runs r ON r.run_id = l.run_id "
        "WHERE l.judge = ? ORDER BY l.run_id",
        (judge,),
    )
    return [r for r in rows if split is None or split_of(r["run_id"], judge) == split]


def few_shot_for(store: LabStore, judge: str) -> str:
    """Up to two Pass and two Fail examples from the few-shot split, so the judge sees both sides."""
    rows = labelled(store, judge, "few_shot")
    chosen: list[tuple[list[dict[str, Any]], str, str]] = []
    for label in ("Pass", "Fail"):
        for r in [r for r in rows if r["label"] == label][:MAX_FEW_SHOT_PER_LABEL]:
            chosen.append((json.loads(r["events_json"]), r["label"], r["note"]))
    return format_few_shot(chosen)
