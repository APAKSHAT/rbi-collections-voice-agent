"""Builds the dynamic variables for a call. Only what the agent must say goes in; identity answers stay out."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from backend import config, policy
from backend.config import ROOT

OPENING_LINES: dict[str, str] = {
    k: v
    for k, v in json.loads((ROOT / "agent_config" / "opening_lines.json").read_text(encoding="utf-8")).items()
    if not k.startswith("_")
}


def opening_line(language: str, borrower_name: str) -> str:
    template = OPENING_LINES.get(language, OPENING_LINES["en"])
    return template.format(lender_name=config.LENDER_NAME, borrower_name=borrower_name)


def _rupees(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def dynamic_variables(borrower: dict[str, Any], now: datetime, human_desk_number: str = "") -> dict[str, str]:
    due = date.fromisoformat(borrower["due_date"])
    return {
        "borrower_id": borrower["borrower_id"],
        "opening_line": opening_line(borrower["preferred_language"], borrower["borrower_name"]),
        "lender_name": config.LENDER_NAME,
        "borrower_name": borrower["borrower_name"],
        "preferred_language": borrower["preferred_language"],
        "loan_type": borrower["loan_type"],
        "loan_last4": borrower["loan_last4"],
        "emi_amount": _rupees(borrower["emi_amount_inr"]),
        "due_date": due.isoformat(),
        "days_past_due": str(policy.days_past_due(due, now.date())),
        "outstanding_amount": _rupees(borrower["outstanding_amount_inr"]),
        "grievance_contact": config.GRIEVANCE_CONTACT,
        "human_desk_number": human_desk_number,
    }


# Every variable the backend sends to the agent. Retell adds its own time variables on top.
VARIABLE_NAMES = (
    "borrower_id", "opening_line", "lender_name", "borrower_name", "preferred_language", "loan_type", "loan_last4",
    "emi_amount", "due_date", "days_past_due", "outstanding_amount", "grievance_contact", "human_desk_number",
)
