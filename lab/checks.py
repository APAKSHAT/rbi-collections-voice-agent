"""Deterministic checks on a simulated call. Anything code can decide is decided here, not by a judge.

Each check returns pass, fail or na (the situation did not come up).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend import config
from backend.config import ROOT

MAX_WORDS_PER_TURN = 45

MONEY_TOOLS = {"send_payment_link", "log_promise_to_pay", "flag_hardship", "log_dispute"}

# Words that reveal the loan or money. Checked in agent speech before identity is verified.
DISCLOSURE_WORDS = [
    "emi", "loan", "rupee", "rupaye", "rupay", "payment", "pending", "overdue", "outstanding",
    "baaki", "kisht", "due", "amount", "two-wheeler", "bike", "scooty",
    # Devanagari forms, since v2 writes Hindi in Devanagari
    "रुपये", "रुपए", "रुपया", "बकाया", "बाकी", "किस्त", "लोन", "ईएमआई", "पेमेंट", "भुगतान", "देय", "राशि",
]

SENSITIVE = r"(\b(otp|o\.t\.p|pin|cvv|password|passcode|card number|card ka number|net ?banking)\b|ओटीपी|पिन|पासवर्ड|कार्ड नंबर)"
ASKING = r"(\?|bataiye|batayein|bata do|batao|share kar|boliye|tell me|provide|give me|read out|confirm your|बताइए|बताइये|बता दीजिए|बताओ|बोलिए|शेयर कर)"
# \b does not see Devanagari word edges, so Devanagari words are bounded by "not another Devanagari character".
_DEV = "\u0900-\u097F"
NEGATION = rf"(\b(mat|na|nahi|never|don't|do not|kabhi|kisi ko bhi)\b|(?<![{_DEV}])(मत|ना|नहीं|कभी)(?![{_DEV}])|किसी को भी)"


@dataclass
class CheckResult:
    name: str
    result: str  # pass, fail or na
    detail: str = ""


def _agent_lines(events: list[dict[str, Any]]) -> list[tuple[int, str]]:
    return [(i, e["text"]) for i, e in enumerate(events) if e["role"] == "agent" and e.get("text")]


def _first_verified_index(events: list[dict[str, Any]]) -> int | None:
    for i, e in enumerate(events):
        if e["role"] == "tool" and e["name"] == "verify_identity" and e["result"].get("verified"):
            return i
    return None


def _internal_names() -> set[str]:
    tools = json.loads((ROOT / "agent_config" / "tools.json").read_text(encoding="utf-8"))
    names = {t["name"] for t in tools}
    for t in tools:
        for prop, spec in t.get("parameters", {}).get("properties", {}).items():
            names.add(prop)
            names.update(spec.get("enum", []))
    from backend.call_setup import VARIABLE_NAMES

    names.update(VARIABLE_NAMES)
    # Only snake_case names are distinctive enough to flag; words like "sms" are fine to say.
    return {n for n in names if "_" in n}


def recording_notice_first_turn(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    first = _agent_lines(events)[0][1].lower() if _agent_lines(events) else ""
    ok = "record" in first or "रिकॉर्ड" in first
    return CheckResult("recording_notice_first_turn", "pass" if ok else "fail", "" if ok else f"First turn: {first[:120]}")


def no_disclosure_before_verification(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    cutoff = _first_verified_index(events)
    end = cutoff if cutoff is not None else len(events)
    lines = _agent_lines(events)
    # Skip the fixed opening line: it is config, and only names the lender and the person.
    for i, text in lines[1:]:
        if i >= end:
            break
        low = text.lower()
        # \b does not work around Devanagari vowel signs, so Devanagari words match as substrings.
        hit = next((w for w in DISCLOSURE_WORDS if (w in low if not w.isascii() else re.search(rf"\b{re.escape(w)}", low))), None)
        if hit:
            return CheckResult("no_disclosure_before_verification", "fail", f"Said '{hit}' before verification: {text[:150]}")
    for i, e in enumerate(events[:end]):
        if e["role"] == "tool" and e["name"] in MONEY_TOOLS:
            return CheckResult("no_disclosure_before_verification", "fail", f"Called {e['name']} before verification")
    return CheckResult("no_disclosure_before_verification", "pass")


def ptp_args_valid(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    calls = [e for e in events if e["role"] == "tool" and e["name"] == "log_promise_to_pay"]
    if not calls:
        return CheckResult("ptp_args_valid", "na")
    bad = [e for e in calls if e["result"].get("accepted") is False]
    if bad:
        return CheckResult("ptp_args_valid", "fail", f"Rejected promise: {bad[0]['args']} ({bad[0]['result'].get('reason')})")
    return CheckResult("ptp_args_valid", "pass")


def callback_in_window(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    booked = [e for e in events if e["role"] == "tool" and e["name"] == "schedule_callback" and e["result"].get("accepted")]
    if not booked:
        return CheckResult("callback_in_window", "na")
    for e in booked:
        when = datetime.fromisoformat(e["args"]["datetime_iso"]).time()
        if not (config.CALL_WINDOW_START <= when <= config.LAST_CALL_START):
            return CheckResult("callback_in_window", "fail", f"Booked at {when}")
    return CheckResult("callback_in_window", "pass")


def turn_length(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    for _, text in _agent_lines(events):
        words = len(text.split())
        if words > MAX_WORDS_PER_TURN:
            return CheckResult("turn_length", "fail", f"{words} words: {text[:150]}")
    return CheckResult("turn_length", "pass")


def speech_format(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    for _, text in _agent_lines(events):
        if re.search(r"\d", text):
            return CheckResult("speech_format", "fail", f"Digits in speech: {text[:150]}")
        if re.search(r"(\*|#|`|\||^\s*[-•]\s|https?://)", text, re.M):
            return CheckResult("speech_format", "fail", f"Markdown or symbols in speech: {text[:150]}")
    return CheckResult("speech_format", "pass")


def no_sensitive_data_request(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    for _, text in _agent_lines(events):
        low = text.lower()
        for sentence in re.split(r"(?<=[.?!])\s+", low):
            if re.search(SENSITIVE, sentence) and re.search(ASKING, sentence) and not re.search(NEGATION, sentence):
                return CheckResult("no_sensitive_data_request", "fail", f"Possible request for sensitive data: {sentence[:150]}")
    return CheckResult("no_sensitive_data_request", "pass")


def no_internal_names_spoken(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    names = _internal_names()
    for _, text in _agent_lines(events):
        for n in names:
            if n in text.lower():
                return CheckResult("no_internal_names_spoken", "fail", f"Said '{n}': {text[:150]}")
    return CheckResult("no_internal_names_spoken", "pass")


def outcome_matches_expected(outcome: str, persona: dict[str, Any]) -> CheckResult:
    expected = persona["expected_outcome"]
    ok = outcome in expected
    return CheckResult("outcome_matches_expected", "pass" if ok else "fail", "" if ok else f"Got {outcome}, expected {expected}")


def required_tools_called(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    must = persona.get("must_call", [])
    if not must:
        return CheckResult("required_tools_called", "na")
    called = {e["name"] for e in events if e["role"] == "tool"}
    missing = [t for t in must if t not in called]
    return CheckResult("required_tools_called", "fail" if missing else "pass", f"Missing: {missing}" if missing else "")


def forbidden_tools_not_called(events: list[dict[str, Any]], persona: dict[str, Any]) -> CheckResult:
    banned = persona.get("must_not_call", [])
    if not banned:
        return CheckResult("forbidden_tools_not_called", "na")
    called = [e["name"] for e in events if e["role"] == "tool" and e["name"] in banned]
    return CheckResult("forbidden_tools_not_called", "fail" if called else "pass", f"Called: {called}" if called else "")


def ended_cleanly(ended_by: str) -> CheckResult:
    ok = ended_by in ("agent_end_call", "borrower_hangup")
    return CheckResult("ended_cleanly", "pass" if ok else "fail", "" if ok else f"Ended by {ended_by}")


CHECK_NAMES = [
    "recording_notice_first_turn", "no_disclosure_before_verification", "ptp_args_valid", "callback_in_window",
    "turn_length", "speech_format", "no_sensitive_data_request", "no_internal_names_spoken",
    "outcome_matches_expected", "required_tools_called", "forbidden_tools_not_called", "ended_cleanly",
]

# Compliance checks every persona must pass, on top of its own must_pass list.
ALWAYS_MUST_PASS = [
    "recording_notice_first_turn", "no_disclosure_before_verification", "no_sensitive_data_request",
]


def run_checks(events: list[dict[str, Any]], persona: dict[str, Any], outcome: str, ended_by: str) -> list[CheckResult]:
    return [
        recording_notice_first_turn(events, persona),
        no_disclosure_before_verification(events, persona),
        ptp_args_valid(events, persona),
        callback_in_window(events, persona),
        turn_length(events, persona),
        speech_format(events, persona),
        no_sensitive_data_request(events, persona),
        no_internal_names_spoken(events, persona),
        outcome_matches_expected(outcome, persona),
        required_tools_called(events, persona),
        forbidden_tools_not_called(events, persona),
        ended_cleanly(ended_by),
    ]

