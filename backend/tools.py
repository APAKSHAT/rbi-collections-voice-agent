"""Tool logic behind the agent's custom functions.

Each tool checks policy first, writes to the store, and returns a small dict the
agent can speak around. Nothing here knows about HTTP, so the test lab calls the
same functions directly.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from backend import config, policy
from backend.db import Store, now_iso

NOT_VERIFIED = {
    "ok": False,
    "error": "Identity is not verified on this call. Do not discuss the loan.",
}

CITY_ALIASES = {
    "bombay": "mumbai",
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "poona": "pune",
    "baroda": "vadodara",
    "cochin": "kochi",
    "new delhi": "delhi",
}


@dataclass(frozen=True)
class CallContext:
    call_id: str
    borrower_id: str
    call_type: str  # web_call, phone_call or simulation
    human_desk_number: str = ""


ToolFn = Callable[[Store, CallContext, dict[str, Any], datetime], dict[str, Any]]


def _norm_city(text: str) -> str:
    city = re.sub(r"[^a-z ]", "", text.lower()).strip()
    return CITY_ALIASES.get(city, city)


def _enum(args: dict[str, Any], key: str, allowed: set[str]) -> str | None:
    value = args.get(key)
    return value if value in allowed else None


def verify_identity(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    borrower = store.borrower(ctx.borrower_id)
    assert borrower is not None
    state = store.call_state(ctx.call_id, ctx.borrower_id)
    left = config.MAX_IDENTITY_ATTEMPTS - state["identity_attempts"]
    if state["verified"]:
        return {"verified": True, "attempts_left": left}
    if left <= 0:
        return {"verified": False, "attempts_left": 0}

    answer_type = _enum(args, "answer_type", {"birth_year", "registered_city"})
    answer = str(args.get("answer", ""))
    if answer_type == "birth_year":
        years = re.findall(r"\d{4}", answer)
        if not years:
            # Not counted as an attempt: the agent passed a bad format, not a wrong answer.
            return {"verified": False, "attempts_left": left, "error": "Ask for the full four-digit year."}
        match = int(years[0]) == int(borrower["birth_year"])
    elif answer_type == "registered_city":
        match = _norm_city(answer) == _norm_city(borrower["registered_city"])
    else:
        return {"verified": False, "attempts_left": left, "error": "answer_type must be birth_year or registered_city."}

    left -= 1
    store.update_call_state(ctx.call_id, identity_attempts=state["identity_attempts"] + 1, verified=int(match))
    return {"verified": match, "attempts_left": left}


def _verified(store: Store, ctx: CallContext) -> dict[str, Any] | None:
    state = store.call_state(ctx.call_id, ctx.borrower_id)
    return state if state["verified"] else None


def send_payment_link(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    state = _verified(store, ctx)
    if not state:
        return NOT_VERIFIED
    if state["links_sent"] >= config.MAX_PAYMENT_LINKS_PER_CALL:
        return {
            "sent": False,
            "response_text": "A payment link was already sent on this call. Ask the borrower to check their SMS.",
        }
    channel = _enum(args, "channel", {"sms", "whatsapp"}) or "sms"
    link_id = "PL-" + uuid.uuid4().hex[:10].upper()
    store.insert(
        "payment_links",
        {"link_id": link_id, "call_id": ctx.call_id, "borrower_id": ctx.borrower_id, "channel": channel, "created_at": now_iso(now)},
    )
    store.update_call_state(ctx.call_id, links_sent=state["links_sent"] + 1)
    where = "WhatsApp" if channel == "whatsapp" else "SMS"
    return {
        "sent": True,
        "response_text": f"Payment link sent by {where} to the registered mobile number.",
        "require_repeat_verbatim": False,
        "link_id": link_id,
    }


def log_promise_to_pay(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not _verified(store, ctx):
        return NOT_VERIFIED
    borrower = store.borrower(ctx.borrower_id)
    assert borrower is not None
    try:
        amount = float(args.get("amount_inr"))
    except (TypeError, ValueError):
        amount = 0.0
    decision = policy.validate_promise(
        str(args.get("date_iso", "")), amount, float(borrower["outstanding_amount_inr"]), now.date()
    )
    if not decision.ok:
        return {"accepted": False, "reason": decision.reason, "error": decision.message}
    mode = _enum(args, "mode", {"payment_link", "branch", "other"}) or "other"
    partial = amount < float(borrower["emi_amount_inr"])
    store.insert(
        "promises",
        {
            "call_id": ctx.call_id,
            "borrower_id": ctx.borrower_id,
            "promised_date": args["date_iso"],
            "amount_inr": amount,
            "mode": mode,
            "partial": int(partial),
            "created_at": now_iso(now),
        },
    )
    return {"accepted": True, "partial": partial}


def flag_hardship(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not _verified(store, ctx):
        return NOT_VERIFIED
    category = _enum(args, "category", {"job_loss", "medical", "bereavement", "income_drop", "other"}) or "other"
    note = str(args.get("note", ""))
    until = policy.cooloff_until(category, now)
    store.insert(
        "hardship_flags",
        {
            "call_id": ctx.call_id,
            "borrower_id": ctx.borrower_id,
            "category": category,
            "note": note,
            "cooloff_until": now_iso(until) if until else None,
            "created_at": now_iso(now),
        },
    )
    _queue(store, ctx, "hardship", note, "normal", now)
    return {"logged": True}


def log_dispute(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not _verified(store, ctx):
        return NOT_VERIFIED
    category = _enum(args, "category", {"already_paid", "not_my_loan", "wrong_amount", "other"}) or "other"
    note = str(args.get("note", ""))
    store.insert(
        "disputes",
        {"call_id": ctx.call_id, "borrower_id": ctx.borrower_id, "category": category, "note": note, "created_at": now_iso(now)},
    )
    _queue(store, ctx, f"dispute_{category}", note, "normal", now)
    return {"logged": True}


def schedule_callback(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    # Allowed before identity is verified: a family member may give a good time to call back.
    borrower = store.borrower(ctx.borrower_id)
    assert borrower is not None
    state = store.call_state(ctx.call_id, ctx.borrower_id)
    windows = policy.parse_windows(borrower["do_not_call_windows"])
    decision, slot = policy.validate_callback(str(args.get("datetime_iso", "")), now, windows)
    if not decision.ok:
        result: dict[str, Any] = {"accepted": False, "reason": decision.reason, "error": decision.message}
        if slot:
            result["nearest_allowed"] = slot.strftime("%Y-%m-%dT%H:%M")
        return result
    assert slot is not None
    store.insert(
        "callbacks",
        {
            "call_id": ctx.call_id,
            "borrower_id": ctx.borrower_id,
            "scheduled_for": now_iso(slot),
            "booked_with_borrower": state["verified"],
            "created_at": now_iso(now),
        },
    )
    return {"accepted": True}


def escalate_to_human(store: Store, ctx: CallContext, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    reasons = {"borrower_request", "severe_distress", "complaint", "stop_calling", "plan_change_request"}
    reason = _enum(args, "reason", reasons) or "borrower_request"
    note = str(args.get("note", ""))
    priority = "urgent" if reason == "severe_distress" else "normal"
    _queue(store, ctx, reason, note, priority, now)
    if reason == "stop_calling":
        store.insert(
            "stop_requests",
            {"call_id": ctx.call_id, "borrower_id": ctx.borrower_id, "detail": note, "created_at": now_iso(now)},
        )
    live = ctx.call_type == "phone_call" and bool(ctx.human_desk_number)
    return {"queued": True, "live_transfer_available": live}


def _queue(store: Store, ctx: CallContext, reason: str, note: str, priority: str, now: datetime) -> None:
    store.insert(
        "human_queue",
        {
            "call_id": ctx.call_id,
            "borrower_id": ctx.borrower_id,
            "reason": reason,
            "note": note,
            "priority": priority,
            "created_at": now_iso(now),
        },
    )


TOOLS: dict[str, ToolFn] = {
    "verify_identity": verify_identity,
    "send_payment_link": send_payment_link,
    "log_promise_to_pay": log_promise_to_pay,
    "flag_hardship": flag_hardship,
    "log_dispute": log_dispute,
    "schedule_callback": schedule_callback,
    "escalate_to_human": escalate_to_human,
}


def run_tool(store: Store, ctx: CallContext, name: str, args: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Run one tool and log the call. Unknown tools and unknown borrowers return an error the agent can handle."""
    if name not in TOOLS:
        result: dict[str, Any] = {"ok": False, "error": f"Unknown tool {name}."}
    elif store.borrower(ctx.borrower_id) is None:
        result = {"ok": False, "error": "Account not found. Apologise and offer a callback from the team."}
    else:
        result = TOOLS[name](store, ctx, args, now)
    store.log_tool_event(ctx.call_id, name, args, result, now)
    return result
