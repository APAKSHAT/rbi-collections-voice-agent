"""Dashboard tests: formatting helpers, and every page rendering without errors on an empty and a filled database."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.fmt import day, day_time, field_value, group_indian, inr, label, pct
from lab.llm import AgentReply, Models, ScriptedAgent, ScriptedText, ToolCall
from lab.run_suite import run_one
from lab.simulate import DEFAULT_SIM_TIME, load_personas
from lab.store import LabStore

APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")
PAGE_TITLES = ["Overview", "Compliance", "Personas", "Transcripts", "Labeling", "Judge quality", "Live calls"]


class TestFormat:
    @pytest.mark.parametrize("n,expected", [(0, "0"), (999, "999"), (4500, "4,500"), (120000, "1,20,000"), (12345678, "1,23,45,678"), (-4500, "-4,500")])
    def test_indian_grouping(self, n: int, expected: str) -> None:
        assert group_indian(n) == expected

    @pytest.mark.parametrize("amount,expected", [(4500, "Rs 4,500"), (99999, "Rs 99,999"), (120000, "Rs 1.2 lakh"), (100000, "Rs 1 lakh"), (34000000, "Rs 3.4 crore")])
    def test_rupees(self, amount: float, expected: str) -> None:
        assert inr(amount) == expected

    def test_dates(self) -> None:
        assert day("2026-11-15") == "15 Nov 2026"
        assert day_time("2026-11-15T17:30:00+05:30") == "15 Nov 2026, 17:30"
        assert day(None) == ""

    def test_labels(self) -> None:
        assert label("no_disclosure_before_verification") == "No disclosure before verification"
        assert label("ptp_args_valid") == "PTP args valid"
        assert label("ai_disclosure_honest") == "AI disclosure honest"
        assert pct(0.875) == "88%"

    def test_field_values(self) -> None:
        assert field_value("identity_verified", True) == "Yes"
        assert field_value("ptp_amount", 4500) == "Rs 4,500"
        assert field_value("ptp_date", "2026-10-24") == "24 Oct 2026"
        assert field_value("outcome", "promise_to_pay") == "Promise to pay"
        assert field_value("language_used", "hindi") == "hindi"
        import numpy as np

        assert field_value("identity_verified", np.bool_(True)) == "Yes"
        assert field_value("ptp_amount", np.float64(4500.0)) == "Rs 4,500"


def _filled_db(path: Path) -> None:
    store = LabStore(path)
    store.load_borrowers()
    persona = next(p for p in load_personas() if p["id"] == "salary_delayed_week")
    agent = lambda: ScriptedAgent([  # noqa: E731
        AgentReply("Aapka janm kis saal hua tha?"),
        AgentReply("", [ToolCall("a", "verify_identity", {"answer_type": "birth_year", "answer": "1993"})]),
        AgentReply("Shukriya. Aapki EMI pending hai. Koi dikkat?"),
        AgentReply("", [ToolCall("b", "log_promise_to_pay", {"date_iso": "2026-10-23", "amount_inr": 4800, "mode": "payment_link"})]),
        AgentReply("Dhanyavaad.", [ToolCall("c", "end_call", {})]),
    ])
    for version in ("v1", "v2"):
        store.save_suite({"suite_id": f"{version}_t", "prompt_version": version, "prompt_sha": "t", "agent_model": "s",
                          "sim_model": "s", "judge_model": "s", "sim_time": DEFAULT_SIM_TIME.isoformat(), "seeds": 1,
                          "created_at": f"2026-10-2{1 if version == 'v1' else 2}T10:00:00+05:30"})
        models = Models(agent, ScriptedText(["Haan.", "Ninety three.", "Friday tak.", "Haan."]),
                        ScriptedText(json_replies=[{"reasoning": "ok", "verdict": "Fail" if version == "v1" else "Pass"}] * 10))
        run_one(store, models, persona, 1, f"{version}_t", "prompt", DEFAULT_SIM_TIME, 10, True)
    store.close()


def _render(page_name: str) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path.cwd()))
    from dashboard import pages

    getattr(pages, page_name)()


PAGE_FUNCS = {"Overview": "overview", "Compliance": "compliance", "Personas": "personas", "Transcripts": "transcripts",
              "Labeling": "labeling", "Judge quality": "judge_quality", "Live calls": "live_calls"}


@pytest.mark.parametrize("filled", [False, True], ids=["empty", "filled"])
@pytest.mark.parametrize("title", PAGE_TITLES)
def test_page_renders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filled: bool, title: str) -> None:
    db = tmp_path / "dash.db"
    if filled:
        _filled_db(db)
    monkeypatch.setenv("DB_PATH", str(db))
    at = AppTest.from_function(_render, args=(PAGE_FUNCS[title],), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == title
    if filled and title == "Overview":
        # Proves the page read this test's database: two versions, v2 better than v1.
        assert [m.value for m in at.metric][:3] == ["1", "100%", "100%"]


def test_app_entry_point_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "dash.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception and at.title[0].value == "Overview"
