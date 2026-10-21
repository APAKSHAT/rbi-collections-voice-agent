"""Consistency checks between the prompt, the tool schemas and the agent settings.

These catch the cheap mistakes before anything is sent to Retell: a prompt that
names a tool or reason that does not exist, a variable nobody fills in, or a
post-call outcome list that drifts from the design doc.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = sorted((ROOT / "prompts" / "agent").glob("v*.md"))
TOOLS = json.loads((ROOT / "agent_config" / "tools.json").read_text())
SETTINGS = json.loads((ROOT / "agent_config" / "agent_settings.json").read_text())
POST_CALL = json.loads((ROOT / "agent_config" / "post_call_fields.json").read_text())

# Variables Retell fills in itself.
BUILT_IN_VARIABLES = {"current_time_Asia/Kolkata"}

OUTCOMES = [
    "escalated_to_human",
    "hardship_escalated",
    "dispute_logged",
    "paid_already",
    "promise_to_pay",
    "payment_link_sent",
    "callback_scheduled",
    "third_party_or_wrong_number",
    "identity_not_verified",
    "refused_or_abusive",
    "call_incomplete",
]


def tool_names() -> set[str]:
    return {t["name"] for t in TOOLS}


def enum_values() -> set[str]:
    values: set[str] = set()
    for tool in TOOLS:
        for prop in tool.get("parameters", {}).get("properties", {}).values():
            values.update(prop.get("enum", []))
    return values


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_only_names_real_tools_and_values(prompt: Path) -> None:
    known = tool_names() | enum_values()
    text = re.sub(r"\{\{[^}]+\}\}", "", prompt.read_text())
    snake = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))
    assert snake - known == set()


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_variables_have_defaults(prompt: Path) -> None:
    used = set(re.findall(r"\{\{([^}]+)\}\}", prompt.read_text()))
    from backend.call_setup import VARIABLE_NAMES

    defaults = set(VARIABLE_NAMES)
    assert used - defaults - BUILT_IN_VARIABLES == set()


def test_begin_message_variable_has_default() -> None:
    used = set(re.findall(r"\{\{([^}]+)\}\}", SETTINGS["llm"]["begin_message"]))
    from backend.call_setup import VARIABLE_NAMES

    assert used <= set(VARIABLE_NAMES)


def test_custom_tools_point_at_backend() -> None:
    for tool in TOOLS:
        if tool["type"] == "custom":
            assert tool["url"] == f"${{BACKEND_URL}}/tools/{tool['name']}"


def test_every_tool_has_a_description() -> None:
    for tool in TOOLS:
        assert len(tool.get("description", "")) > 20, tool["name"]


def test_outcome_choices_match_design() -> None:
    outcome = next(f for f in POST_CALL if f["name"] == "outcome")
    assert outcome["choices"] == OUTCOMES


def test_escalation_reasons_match_tool() -> None:
    field = next(f for f in POST_CALL if f["name"] == "escalation_reason")
    tool = next(t for t in TOOLS if t["name"] == "escalate_to_human")
    assert field["choices"] == tool["parameters"]["properties"]["reason"]["enum"]


def test_hardship_categories_match_tool() -> None:
    field = next(f for f in POST_CALL if f["name"] == "hardship_category")
    tool = next(t for t in TOOLS if t["name"] == "flag_hardship")
    assert field["choices"] == tool["parameters"]["properties"]["category"]["enum"]


def test_timezone_is_india() -> None:
    assert SETTINGS["agent"]["timezone"] == "Asia/Kolkata"


def test_handbook_presets_are_off() -> None:
    # Presets add hidden text to the prompt, which the test lab would not see.
    assert not any(SETTINGS["agent"]["handbook_config"].values())


def test_retention_covers_six_months() -> None:
    assert SETTINGS["agent"]["data_storage_retention_days"] >= 183


def test_opening_lines_carry_recording_notice() -> None:
    lines = json.loads((ROOT / "agent_config" / "opening_lines.json").read_text())
    assert "रिकॉर्ड" in lines["hi"]
    assert "recorded" in lines["en"].lower()


def test_variable_names_match_what_the_backend_sends() -> None:
    from datetime import datetime

    from backend.call_setup import VARIABLE_NAMES, dynamic_variables
    from backend.config import IST
    from backend.db import Store

    s = Store(":memory:")
    s.load_borrowers()
    assert set(dynamic_variables(s.borrower("KF-TW-000184"), datetime.now(IST))) == set(VARIABLE_NAMES)


def test_each_language_has_its_own_v3_prompt() -> None:
    hi = (ROOT / "prompts" / "agent" / "v3-hi.md").read_text()
    en = (ROOT / "prompts" / "agent" / "v3-en.md").read_text()
    assert "This call is in Hindi" in hi and "This call is in English" in en
    assert "चार हज़ार" in hi and "चार हज़ार" not in en
