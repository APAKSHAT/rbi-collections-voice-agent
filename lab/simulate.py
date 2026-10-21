"""Text-mode simulation of one call: the agent prompt against one simulated borrower.

The agent side uses the exact prompt version from prompts/agent/, the same tool
schemas as Retell, and the real backend tool logic against a throwaway
in-memory database, so tool side effects never touch real data.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backend import call_setup
from backend.config import IST, ROOT
from backend.db import Store
from backend.tools import CallContext, run_tool
from lab.llm import AgentModel, TextModel, ToolCall

PERSONAS_FILE = ROOT / "lab" / "personas.yaml"
BORROWER_PROMPT = ROOT / "prompts" / "simulator" / "borrower.md"
DEFAULT_SIM_TIME = datetime(2026, 10, 21, 11, 0, tzinfo=IST)
MAX_TOOL_ROUNDS = 4

# Fields Retell reads but a plain function declaration does not need.
_RETELL_ONLY = {"type", "url", "method", "speak_during_execution", "speak_after_execution",
                "execution_message_type", "execution_message_description", "timeout_ms",
                "transfer_destination", "transfer_option"}

OUTCOME_ORDER = [
    "escalated_to_human", "hardship_escalated", "dispute_logged", "paid_already", "promise_to_pay",
    "payment_link_sent", "callback_scheduled", "third_party_or_wrong_number", "identity_not_verified",
    "refused_or_abusive", "call_incomplete",
]


@dataclass
class RunResult:
    events: list[dict[str, Any]]
    ended_by: str
    borrower_turns: int
    outcome: str
    verified: bool = False
    tool_log: list[dict[str, Any]] = field(default_factory=list)


def load_personas(path: Path = PERSONAS_FILE) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["personas"]


def prompt_texts(version: str) -> dict[str, str]:
    """The prompt for each language: v3-hi.md and v3-en.md if they exist, otherwise the shared v3.md.
    Mirrors agent_config/create_agent.py, so the lab runs what each Retell agent runs."""
    folder = ROOT / "prompts" / "agent"
    shared = folder / f"{version}.md"
    out = {}
    for lang in ("hi", "en"):
        path = folder / f"{version}-{lang}.md"
        path = path if path.exists() else shared
        if not path.exists():
            raise FileNotFoundError(f"No prompt for {version} ({lang}): expected {path.name}")
        out[lang] = path.read_text(encoding="utf-8")
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def current_time_text(now: datetime) -> str:
    """Matches the shape of Retell's {{current_time_...}} variable."""
    return now.strftime("%A, %B %-d, %Y at %-I:%M %p IST")


def fill(template: str, variables: dict[str, str]) -> str:
    return re.sub(r"\{\{([^}]+)\}\}", lambda m: variables.get(m.group(1).strip(), m.group(0)), template)


def agent_tools() -> list[dict[str, Any]]:
    """Tool schemas as the model sees them: no Retell-only fields, and no fixed (const) arguments,
    which Retell fills itself and the model never sees."""
    tools = json.loads((ROOT / "agent_config" / "tools.json").read_text(encoding="utf-8"))
    out = []
    for t in tools:
        t = {k: v for k, v in t.items() if k not in _RETELL_ONLY}
        if "parameters" in t:
            props = {k: v for k, v in t["parameters"]["properties"].items() if "const" not in v}
            t["parameters"] = {**t["parameters"], "properties": props}
        out.append(t)
    return out


def borrower_record(store: Store, persona: dict[str, Any]) -> dict[str, Any]:
    record = store.borrower(persona["borrower_id"])
    if record is None:
        raise ValueError(f"Persona {persona['id']} uses unknown borrower {persona['borrower_id']}")
    if persona.get("preferred_language"):
        record = {**record, "preferred_language": persona["preferred_language"]}
    return record


def persona_card(persona: dict[str, Any], record: dict[str, Any]) -> str:
    who = persona.get("answers_as", "borrower")
    lines = [f"Your name: {persona['name']}.", f"You speak: {persona['language']}."]
    if who == "borrower":
        lines.append(f"You are {record['borrower_name']}, who has a two-wheeler loan with Kaveri Finance.")
        lines.append(f"Your monthly EMI is {record['emi_amount_inr']:.0f} rupees.")
        if persona.get("knows_identity", True):
            lines.append(
                f"If asked to confirm who you are: you were born in {record['birth_year']} "
                f"and your registered city is {record['registered_city']}."
            )
    elif who == "stranger":
        lines.append("You are not the person the caller is looking for.")
    else:
        lines.append(f"You are {record['borrower_name']}'s {who}. You are not the borrower.")
    lines.append("What is true for you:")
    lines += [f"- {fact}" for fact in persona.get("hidden_facts", [])]
    lines.append(f"How you talk and behave: {persona['behavior']}")
    return "\n".join(lines)


def derive_outcome(tool_log: list[dict[str, Any]], verified: bool, ended_by: str) -> str:
    """The call's outcome from what the tools actually recorded, using the design doc's priority order."""
    found: set[str] = set()
    verify_attempted = False
    for e in tool_log:
        name, args, result = e["name"], e["args"], e["result"]
        if name == "verify_identity":
            verify_attempted = True
        elif name == "escalate_to_human" and result.get("queued"):
            found.add("escalated_to_human")
        elif name == "flag_hardship" and result.get("logged"):
            found.add("hardship_escalated")
        elif name == "log_dispute" and result.get("logged"):
            found.add("paid_already" if args.get("category") == "already_paid" else "dispute_logged")
        elif name == "log_promise_to_pay" and result.get("accepted"):
            found.add("promise_to_pay")
        elif name == "send_payment_link" and result.get("sent"):
            found.add("payment_link_sent")
        elif name == "schedule_callback" and result.get("accepted"):
            found.add("callback_scheduled")
    if not verified:
        # Without a verified borrower the only possible results are these.
        if "escalated_to_human" in found:
            return "escalated_to_human"
        if ended_by == "turn_cap":
            return "call_incomplete"
        return "identity_not_verified" if verify_attempted else "third_party_or_wrong_number"
    for outcome in OUTCOME_ORDER:
        if outcome in found:
            return outcome
    return "refused_or_abusive" if ended_by in ("agent_end_call", "borrower_hangup") else "call_incomplete"


def _parse_borrower(raw: str) -> tuple[str, bool, bool, bool]:
    ends = "[END_CALL]" in raw
    interrupts = raw.lstrip().startswith("[INTERRUPTS]")
    # The simulator sometimes adds a marker mid-reply; markers are never spoken.
    text = raw
    for marker in ("[END_CALL]", "[INTERRUPTS]", "[SILENCE]"):
        text = text.replace(marker, "")
    text = text.strip()
    silent = text == ""
    return text, silent, interrupts, ends


def simulate(
    persona: dict[str, Any],
    prompt_text: str | dict[str, str],
    agent: AgentModel,
    borrower: TextModel,
    run_id: str,
    now: datetime = DEFAULT_SIM_TIME,
    max_turns: int = 24,
) -> RunResult:
    store = Store(":memory:")
    store.load_borrowers()
    record = borrower_record(store, persona)
    variables = call_setup.dynamic_variables(record, now)
    variables["current_time_Asia/Kolkata"] = current_time_text(now)
    opening = variables["opening_line"]
    ctx = CallContext(call_id=run_id, borrower_id=record["borrower_id"], call_type="simulation")

    if isinstance(prompt_text, dict):
        prompt_text = prompt_text[record["preferred_language"]]
    agent.start(fill(prompt_text, variables), agent_tools(), opening)
    sim_system = BORROWER_PROMPT.read_text(encoding="utf-8").replace("{persona_card}", persona_card(persona, record))

    events: list[dict[str, Any]] = [{"role": "agent", "text": opening}]
    sim_turns: list[tuple[str, str]] = [("user", opening)]
    tool_log: list[dict[str, Any]] = []
    ended_by = "turn_cap"
    turns = 0

    for turns in range(1, max_turns + 1):
        raw = borrower.complete(sim_system, sim_turns)
        text, silent, interrupts, ends = _parse_borrower(raw)
        events.append({"role": "borrower", "text": text, "silent": silent, "interrupts": interrupts})
        sim_turns.append(("model", text or "[SILENCE]"))
        if ends:
            ended_by = "borrower_hangup"
            break

        agent.add_user(text if not silent else "(no response from the caller)")
        said: list[str] = []
        ended = False
        for _ in range(MAX_TOOL_ROUNDS):
            reply = agent.generate()
            if reply.text:
                said.append(reply.text)
                events.append({"role": "agent", "text": reply.text})
            if not reply.tool_calls:
                break
            results: list[tuple[ToolCall, dict[str, Any]]] = []
            for call in reply.tool_calls:
                result = _run_call(store, ctx, call, now)
                events.append({"role": "tool", "name": call.name, "args": call.args, "result": result})
                tool_log.append({"name": call.name, "args": call.args, "result": result})
                results.append((call, result))
                ended = ended or call.name == "end_call"
            agent.add_tool_results(results)
            if ended:
                break
        sim_turns.append(("user", " ".join(said) or "[SILENCE]"))
        if ended:
            ended_by = "agent_end_call"
            break

    state = store.call_state(run_id, record["borrower_id"])
    verified = bool(state["verified"])
    store.close()
    return RunResult(events, ended_by, turns, derive_outcome(tool_log, verified, ended_by), verified, tool_log)


def _run_call(store: Store, ctx: CallContext, call: ToolCall, now: datetime) -> dict[str, Any]:
    if call.name == "end_call":
        return {"ok": True}
    if call.name == "transfer_call":
        # Same as a Retell web call: there is no phone line to transfer.
        return {"ok": False, "error": "Transfer failed. This is not a phone call."}
    return run_tool(store, ctx, call.name, call.args, now)
