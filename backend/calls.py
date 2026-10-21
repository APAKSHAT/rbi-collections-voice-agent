"""Placing calls. Every call goes through the pre-dial policy check and is logged as an attempt."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend import call_setup, policy
from backend.config import Settings
from backend.db import Store, now_iso


def precheck(store: Store, borrower_id: str, now: datetime) -> tuple[policy.Decision, dict[str, Any] | None]:
    borrower = store.borrower(borrower_id)
    if borrower is None:
        return policy.Decision(False, "unknown_borrower", f"No borrower {borrower_id}."), None
    decision = policy.can_start_call(
        now,
        store.attempts_on(borrower_id, now.date()),
        policy.parse_windows(borrower["do_not_call_windows"]),
        store.latest_cooloff(borrower_id),
        store.stop_request_open(borrower_id),
    )
    return decision, borrower


def record_attempt(
    store: Store, borrower_id: str, channel: str, now: datetime, decision: policy.Decision, call_id: str | None = None
) -> None:
    store.insert(
        "call_attempts",
        {
            "borrower_id": borrower_id,
            "call_id": call_id,
            "channel": channel,
            "attempted_at": now_iso(now),
            "status": "placed" if decision.ok else "refused",
            "refusal_reason": decision.reason,
        },
    )


def agent_id(settings: Settings, language: str) -> str:
    """The agent for the borrower's language (one Retell agent per language)."""
    if not settings.agent_ids_file.exists():
        raise RuntimeError("No agent yet. Run agent_config/create_agent.py first.")
    ids = json.loads(settings.agent_ids_file.read_text())
    if language not in ids:
        raise RuntimeError(f"No {language} agent yet. Run agent_config/create_agent.py --lang {language}.")
    return ids[language]["agent_id"]


def create_web_call(settings: Settings, borrower: dict[str, Any], now: datetime) -> dict[str, str]:
    from retell import Retell

    client = Retell(api_key=settings.retell_api_key)
    call = client.call.create_web_call(
        agent_id=agent_id(settings, borrower["preferred_language"]),
        retell_llm_dynamic_variables=call_setup.dynamic_variables(borrower, now),
        metadata={"borrower_id": borrower["borrower_id"]},
    )
    return {"call_id": call.call_id, "access_token": call.access_token}


def create_phone_call(
    settings: Settings, borrower: dict[str, Any], now: datetime, from_number: str, to_number: str
) -> str:
    from retell import Retell

    client = Retell(api_key=settings.retell_api_key)
    call = client.call.create_phone_call(
        from_number=from_number,
        to_number=to_number,
        override_agent_id=agent_id(settings, borrower["preferred_language"]),
        retell_llm_dynamic_variables=call_setup.dynamic_variables(borrower, now, settings.human_desk_number),
        metadata={"borrower_id": borrower["borrower_id"]},
    )
    return call.call_id
