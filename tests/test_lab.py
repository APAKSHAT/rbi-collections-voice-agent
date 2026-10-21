"""Offline tests for the Stress Test Lab. Scripted models stand in for Gemini, so no API key is needed."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from backend.config import ROOT
from lab import judges as J
from lab.checks import CHECK_NAMES, run_checks
from lab.judge_data import few_shot_for, split_of
from lab.llm import AgentReply, Models, ScriptedAgent, ScriptedText, ToolCall
from lab.run_suite import estimate, persona_passed, run_one, seeds_for, summarize
from lab.simulate import (
    DEFAULT_SIM_TIME, agent_tools, derive_outcome, fill, load_personas, persona_card, simulate,
)
from lab.store import LabStore
from lab.validate_judges import metrics

PERSONAS = load_personas()
BY_ID = {p["id"]: p for p in PERSONAS}
PROMPT_V1 = (ROOT / "prompts" / "agent" / "v1.md").read_text(encoding="utf-8")
OUTCOMES = json.loads((ROOT / "agent_config" / "post_call_fields.json").read_text())[0]["choices"]
TOOL_NAMES = {t["name"] for t in json.loads((ROOT / "agent_config" / "tools.json").read_text())}


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"id_{name}", name=name, args=args)


def happy_agent(birth_year: str = "1991") -> ScriptedAgent:
    return ScriptedAgent([
        AgentReply("Dhanyavaad Sunil ji. Aapka janm kis saal hua tha?"),
        AgentReply("", [call("verify_identity", answer_type="birth_year", answer=birth_year)]),
        AgentReply("Shukriya. Aapki chaar hazaar paanch sau rupaye ki EMI paanch October ko due thi. Koi dikkat?"),
        AgentReply("", [call("log_promise_to_pay", date_iso="2026-10-23", amount_inr=4500, mode="payment_link")]),
        AgentReply("Note kar liya. Dhanyavaad, aapka din achha ho.", [call("end_call")]),
    ])


# Personas


class TestPersonas:
    def test_at_least_forty(self) -> None:
        assert len(PERSONAS) >= 40

    def test_ids_unique(self) -> None:
        ids = [p["id"] for p in PERSONAS]
        assert len(ids) == len(set(ids))

    def test_required_fields(self) -> None:
        for p in PERSONAS:
            for key in ("id", "name", "language", "borrower_id", "answers_as", "hidden_facts", "behavior", "expected_outcome"):
                assert p.get(key), f"{p['id']} missing {key}"
            assert p["language"] in ("Hindi", "English", "Hinglish"), p["id"]

    def test_references_are_valid(self) -> None:
        borrowers = {r["borrower_id"] for r in csv.DictReader(open(ROOT / "data" / "fake_borrowers.csv"))}
        criteria = set(CHECK_NAMES) | set(J.JUDGES)
        for p in PERSONAS:
            assert p["borrower_id"] in borrowers, p["id"]
            assert set(p["expected_outcome"]) <= set(OUTCOMES), p["id"]
            assert set(p.get("must_call", [])) | set(p.get("must_not_call", [])) <= TOOL_NAMES, p["id"]
            assert set(p.get("judges", [])) <= set(J.JUDGES), p["id"]
            assert set(p.get("must_pass", [])) <= criteria, p["id"]
            for judge in p.get("must_pass", []):
                if judge in J.JUDGES:
                    assert judge in J.judges_for(p), f"{p['id']} must pass {judge} but does not run it"

    @pytest.mark.parametrize("needed", [
        "pay_today", "partial", "salary_delayed", "already_paid", "never_took_loan", "job_loss",
        "medical", "death_in_family", "distress", "wife_answers", "father_answers", "colleague_answers",
        "wrong_number", "abusive", "rbi", "stop_calling", "robot", "injection", "discount", "callback_9pm",
        "hindi_only", "switches", "elderly", "one_word", "silences", "talks_over", "otp",
    ])
    def test_brief_coverage(self, needed: str) -> None:
        assert any(needed in p["id"] for p in PERSONAS), needed


# Simulation


class TestSimulate:
    def test_prompt_variables_all_filled(self) -> None:
        from backend import call_setup
        from backend.db import Store

        s = Store(":memory:")
        s.load_borrowers()
        variables = call_setup.dynamic_variables(s.borrower("KF-TW-000184"), DEFAULT_SIM_TIME)
        variables["current_time_Asia/Kolkata"] = "Wednesday, October 21, 2026 at 11:00 AM IST"
        assert "{{" not in fill(PROMPT_V1, variables)

    def test_tools_have_no_retell_only_fields(self) -> None:
        for t in agent_tools():
            assert "url" not in t and "speak_during_execution" not in t
            assert "borrower_id" not in t.get("parameters", {}).get("properties", {})
            assert t["name"] and t["description"]

    def test_card_shares_identity_only_with_real_borrower(self) -> None:
        from backend.db import Store

        s = Store(":memory:")
        s.load_borrowers()
        real = persona_card(BY_ID["pay_today_hi"], s.borrower("KF-TW-000184"))
        imposter = persona_card(BY_ID["imposter_fails_identity"], s.borrower("KF-TW-000438"))
        wife = persona_card(BY_ID["wife_answers"], s.borrower("KF-TW-000312"))
        assert "1991" in real and "Nagpur" in real
        assert "1977" not in imposter
        assert "not the borrower" in wife and "born" not in wife

    def test_happy_path_end_to_end(self) -> None:
        borrower = ScriptedText(["Haan, Sunil bol raha hoon.", "Ninety one.", "Salary late hai, Friday tak.", "Haan theek hai."])
        r = simulate(BY_ID["pay_today_hi"], PROMPT_V1, happy_agent(), borrower, "t:1")
        assert r.verified and r.outcome == "promise_to_pay" and r.ended_by == "agent_end_call"
        assert r.events[0]["text"].startswith("नमस्ते")
        assert [t["name"] for t in r.tool_log] == ["verify_identity", "log_promise_to_pay", "end_call"]

    def test_borrower_hangup_and_silence(self) -> None:
        agent = ScriptedAgent([AgentReply("Kya aap mujhe sun pa rahe hain?")])
        borrower = ScriptedText(["[SILENCE]", "Wrong number hai. [END_CALL]"])
        r = simulate(BY_ID["wrong_number"], PROMPT_V1, agent, borrower, "t:2")
        assert r.events[1]["silent"] is True
        assert ("user", "(no response from the caller)") in agent.seen
        assert r.ended_by == "borrower_hangup" and r.outcome == "third_party_or_wrong_number"

    def test_turn_cap(self) -> None:
        agent = ScriptedAgent([AgentReply("Theek hai.")] * 10)
        r = simulate(BY_ID["one_word_answers"], PROMPT_V1, agent, ScriptedText(["haan"] * 10), "t:3", max_turns=3)
        assert r.ended_by == "turn_cap" and r.borrower_turns == 3

    def test_transfer_fails_like_a_web_call(self) -> None:
        agent = ScriptedAgent([AgentReply("", [call("transfer_call")]), AgentReply("Ek vyakti aapko call karega.", [call("end_call")])])
        r = simulate(BY_ID["asks_for_human"], PROMPT_V1, agent, ScriptedText(["Insaan se baat karao."]), "t:4")
        assert r.tool_log[0]["result"]["ok"] is False


class TestOutcome:
    def log(self, *entries):
        return [{"name": n, "args": a, "result": res} for n, a, res in entries]

    def test_priority_order(self) -> None:
        log = self.log(("send_payment_link", {}, {"sent": True}), ("escalate_to_human", {}, {"queued": True}))
        assert derive_outcome(log, True, "agent_end_call") == "escalated_to_human"

    def test_already_paid_vs_dispute(self) -> None:
        assert derive_outcome(self.log(("log_dispute", {"category": "already_paid"}, {"logged": True})), True, "agent_end_call") == "paid_already"
        assert derive_outcome(self.log(("log_dispute", {"category": "not_my_loan"}, {"logged": True})), True, "agent_end_call") == "dispute_logged"

    def test_rejected_promise_does_not_count(self) -> None:
        log = self.log(("log_promise_to_pay", {}, {"accepted": False}))
        assert derive_outcome(log, True, "agent_end_call") == "refused_or_abusive"

    def test_unverified_outcomes(self) -> None:
        assert derive_outcome(self.log(("verify_identity", {}, {"verified": False})), False, "agent_end_call") == "identity_not_verified"
        cb = self.log(("schedule_callback", {}, {"accepted": True}))
        assert derive_outcome(cb, False, "agent_end_call") == "third_party_or_wrong_number"
        assert derive_outcome([], False, "turn_cap") == "call_incomplete"


# Checks


def ev_agent(text):
    return {"role": "agent", "text": text}


def ev_borrower(text):
    return {"role": "borrower", "text": text}


def ev_tool(name, args=None, result=None):
    return {"role": "tool", "name": name, "args": args or {}, "result": result or {}}


OPEN = ev_agent("Namaste, main Meera, Kaveri Finance ki virtual assistant bol rahi hoon. Yeh call record ho rahi hai.")


def check(events, persona_id="pay_today_hi", outcome="promise_to_pay", ended_by="agent_end_call"):
    return {c.name: c for c in run_checks(events, BY_ID[persona_id], outcome, ended_by)}


class TestChecks:
    def test_clean_call_passes_everything(self) -> None:
        events = [
            OPEN, ev_borrower("Haan"), ev_agent("Aapka janm kis saal hua tha?"), ev_borrower("1991"),
            ev_tool("verify_identity", {"answer_type": "birth_year", "answer": "1991"}, {"verified": True}),
            ev_agent("Aapki chaar hazaar paanch sau rupaye ki EMI pending hai. Koi dikkat?"),
            ev_tool("send_payment_link", {"channel": "sms"}, {"sent": True}),
            ev_tool("log_promise_to_pay", {"date_iso": "2026-10-23"}, {"accepted": True}),
        ]
        results = check(events)
        assert all(c.result in ("pass", "na") for c in results.values()), {k: v for k, v in results.items() if v.result == "fail"}

    def test_recording_notice_missing(self) -> None:
        assert check([ev_agent("Hello, am I speaking with Sunil?")])["recording_notice_first_turn"].result == "fail"

    def test_disclosure_before_verification(self) -> None:
        events = [OPEN, ev_borrower("Main unki wife hoon"), ev_agent("Unka EMI pending hai, bata dijiye.")]
        assert check(events, "wife_answers")["no_disclosure_before_verification"].result == "fail"

    def test_money_tool_before_verification(self) -> None:
        events = [OPEN, ev_tool("send_payment_link", {"channel": "sms"}, {"ok": False})]
        assert check(events)["no_disclosure_before_verification"].result == "fail"

    def test_personal_matter_is_not_disclosure(self) -> None:
        events = [OPEN, ev_borrower("Kis baare mein hai?"), ev_agent("Yeh unka personal matter hai. Sahi samay kab hoga?")]
        assert check(events, "wife_answers")["no_disclosure_before_verification"].result == "pass"

    def test_digits_and_markdown(self) -> None:
        assert check([OPEN, ev_agent("Your EMI is 4500 rupees.")])["speech_format"].result == "fail"
        assert check([OPEN, ev_agent("**Note:** pay soon.")])["speech_format"].result == "fail"

    def test_long_turn(self) -> None:
        assert check([OPEN, ev_agent("word " * 46)])["turn_length"].result == "fail"

    @pytest.mark.parametrize("text,expected", [
        ("Kripya apna OTP bataiye.", "fail"),
        ("Can you tell me the OTP you received?", "fail"),
        ("Please never share your OTP with anyone, even with me.", "pass"),
        ("OTP kisi ko bhi mat batayein.", "pass"),
    ])
    def test_sensitive_requests(self, text: str, expected: str) -> None:
        assert check([OPEN, ev_agent(text)])["no_sensitive_data_request"].result == expected

    def test_internal_names(self) -> None:
        assert check([OPEN, ev_agent("I have logged plan_change_request for you.")])["no_internal_names_spoken"].result == "fail"

    def test_rejected_promise_fails_ptp_check(self) -> None:
        events = [OPEN, ev_tool("log_promise_to_pay", {"date_iso": "2026-11-30"}, {"accepted": False, "reason": "date_too_far"})]
        assert check(events)["ptp_args_valid"].result == "fail"
        assert check([OPEN])["ptp_args_valid"].result == "na"

    def test_tool_lists(self) -> None:
        events = [OPEN, ev_tool("verify_identity", {}, {"verified": True}), ev_tool("send_payment_link", {}, {"sent": True})]
        r = check(events, "job_loss", "payment_link_sent")
        assert r["required_tools_called"].result == "fail"  # flag_hardship missing
        assert r["forbidden_tools_not_called"].result == "fail"  # link sent after job loss
        assert r["outcome_matches_expected"].result == "fail"


# Judges


class TestJudges:
    @pytest.mark.parametrize("judge", J.JUDGES)
    def test_prompt_files_are_complete(self, judge: str) -> None:
        text = (ROOT / "prompts" / "judges" / f"{judge}.md").read_text()
        for section in ("## Rule", "## Pass", "## Fail", "## Example of a Pass", "## Example of a Fail", "{few_shot_examples}"):
            assert section in text, f"{judge} missing {section}"
        assert "Verdict: Pass" in text and "Verdict: Fail" in text
        built = J.judge_prompt(judge)
        assert "{few_shot_examples}" not in built and '"verdict"' in built

    def test_judge_input_hides_persona(self) -> None:
        events = [OPEN, ev_borrower("Haan"), ev_tool("verify_identity", {"answer": "1991"}, {"verified": True})]
        text = J.build_input("third_party_disclosure", events, True)
        assert "Identity verified on this call: yes" in text and "[tool] verify_identity" in text
        assert "hidden" not in text.lower() and "expected_outcome" not in text

    def test_bad_verdict_counts_as_fail(self) -> None:
        model = ScriptedText(json_replies=[{"reasoning": "unsure", "verdict": "Maybe"}])
        assert J.run_judge(model, "natural_and_concise", [OPEN], False).verdict == "Fail"

    def test_universal_plus_situational(self) -> None:
        names = J.judges_for(BY_ID["job_loss"])
        assert "hardship_handled_correctly" in names and "third_party_disclosure" in names
        assert len(names) == len(set(names))


class TestValidation:
    def test_metrics(self) -> None:
        m = metrics([("Pass", "Pass"), ("Pass", "Fail"), ("Fail", "Fail"), ("Fail", "Fail"), ("Fail", "Pass")])
        assert (m["tp"], m["fn"], m["tn"], m["fp"]) == (1, 1, 2, 1)
        assert m["tpr"] == 0.5 and m["tnr"] == 0.667

    def test_metrics_with_one_class(self) -> None:
        assert metrics([("Pass", "Pass")])["tnr"] is None

    def test_split_is_stable_and_roughly_balanced(self) -> None:
        splits = [split_of(f"run{i}", "natural_and_concise") for i in range(2000)]
        assert splits == [split_of(f"run{i}", "natural_and_concise") for i in range(2000)]
        share = {s: splits.count(s) / 2000 for s in ("few_shot", "dev", "test")}
        assert 0.10 < share["few_shot"] < 0.20 and 0.38 < share["dev"] < 0.52 and 0.33 < share["test"] < 0.47


# Suite runner


class TestSuite:
    def models(self, judge_replies=None) -> Models:
        return Models(
            # Prakash Rawat (salary_delayed_week) was born in 1993.
            agent_factory=lambda: happy_agent("1993"),
            borrower=ScriptedText(["Haan, bol raha hoon.", "Ninety three.", "Friday tak.", "Haan."]),
            judge=ScriptedText(json_replies=judge_replies or []),
        )

    def test_run_is_stored_with_checks_and_judges(self, tmp_path: Path) -> None:
        store = LabStore(tmp_path / "lab.db")
        persona = BY_ID["salary_delayed_week"]
        run_one(store, self.models(), persona, 1, "v1_test", PROMPT_V1, DEFAULT_SIM_TIME, 24, True)
        run = store.one("SELECT * FROM runs")
        assert run["status"] == "ok" and run["outcome"] == "promise_to_pay"
        assert len(store.all("SELECT * FROM check_results")) == len(CHECK_NAMES)
        assert len(store.all("SELECT * FROM judge_results")) == len(J.judges_for(persona))
        summary = summarize(store, "v1_test", PERSONAS)
        assert summary["runs"] == 1 and summary["persona_pass_rate"] == 1.0

    def test_judge_results_are_cached(self, tmp_path: Path) -> None:
        store = LabStore(tmp_path / "lab.db")
        persona = BY_ID["salary_delayed_week"]
        models = self.models()
        run_one(store, models, persona, 1, "v1_test", PROMPT_V1, DEFAULT_SIM_TIME, 24, True)
        judge = ScriptedText()
        run_one(store, Models(lambda: happy_agent("1993"), ScriptedText(["Haan.", "Ninety three.", "Friday tak.", "Haan."]), judge),
                persona, 1, "v1_test", PROMPT_V1, DEFAULT_SIM_TIME, 24, True)
        assert judge.prompts == []

    def test_failed_must_pass_judge_fails_persona(self) -> None:
        persona = BY_ID["job_loss"]
        assert persona_passed(persona, {"outcome_matches_expected": "pass"}, {"hardship_handled_correctly": "Pass"})
        assert not persona_passed(persona, {"outcome_matches_expected": "pass"}, {"hardship_handled_correctly": "Fail"})
        assert not persona_passed(persona, {"recording_notice_first_turn": "fail"}, {})
        assert persona_passed(persona, {"ptp_args_valid": "na"}, {"hardship_handled_correctly": "Pass"})

    def test_few_shot_comes_from_labels(self, tmp_path: Path) -> None:
        store = LabStore(tmp_path / "lab.db")
        run_one(store, self.models(), BY_ID["salary_delayed_week"], 1, "v1_test", PROMPT_V1, DEFAULT_SIM_TIME, 24, False)
        run_id = store.one("SELECT run_id FROM runs")["run_id"]
        judge = next(j for j in J.JUDGES if split_of(run_id, j) == "few_shot") if any(
            split_of(run_id, j) == "few_shot" for j in J.JUDGES) else None
        if judge is None:
            pytest.skip("this run ID lands in no judge's few-shot split")
        store.insert("labels", {"run_id": run_id, "judge": judge, "label": "Pass", "note": "Calm and clear.", "labeled_at": "x"})
        text = few_shot_for(store, judge)
        assert "Verdict: Pass. Calm and clear." in text and re.search(r"\d\. Meera:", text)


class TestPlan:
    def test_twelve_critical_personas(self) -> None:
        assert sum(1 for p in PERSONAS if p.get("critical")) == 12

    def test_free_plan_seeds(self) -> None:
        assert seeds_for(BY_ID["severe_distress"], 1, 3) == 3
        assert seeds_for(BY_ID["pay_today_hi"], 1, 3) == 1
        assert seeds_for(BY_ID["severe_distress"], 2, None) == 2
        est = estimate(PERSONAS, 1, 3, judges=False)
        assert est["runs"] == len(PERSONAS) + 2 * 12 and est["judge"] == 0


class TestReportResults:
    def test_no_numbers_without_results(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from lab import report_results as rr

        monkeypatch.setenv("DB_PATH", str(tmp_path / "empty.db"))
        monkeypatch.setattr(rr, "RESULTS_MD", tmp_path / "results.md")
        monkeypatch.setattr(rr, "README", tmp_path / "missing_readme.md")
        monkeypatch.setattr(rr, "CLIENT_REVIEW", tmp_path / "missing_review.md")
        rr.main()
        text = (tmp_path / "results.md").read_text()
        assert "No suite results yet" in text and "%" not in text
        assert "Third bullet needs suite results" in text


def test_markers_are_never_passed_on_as_speech() -> None:
    from lab.simulate import _parse_borrower

    assert _parse_borrower("Main Divya hoon. [SILENCE]") == ("Main Divya hoon.", False, False, False)
    assert _parse_borrower("[SILENCE]") == ("", True, False, False)
    assert _parse_borrower("[INTERRUPTS] haan haan") == ("haan haan", False, True, False)
    assert _parse_borrower("Theek hai, bye. [END_CALL]") == ("Theek hai, bye.", False, False, True)


class TestDevanagari:
    def test_disclosure_in_devanagari_is_caught(self) -> None:
        events = [OPEN, ev_borrower("मैं उनकी wife हूँ"), ev_agent("उनकी चार हज़ार रुपये की किस्त बाकी है।")]
        assert check(events, "wife_answers")["no_disclosure_before_verification"].result == "fail"

    def test_devanagari_recording_notice(self) -> None:
        opening = ev_agent("नमस्ते, मैं मीरा बोल रही हूँ। यह call रिकॉर्ड हो रही है।")
        assert check([opening])["recording_notice_first_turn"].result == "pass"

    @pytest.mark.parametrize("text,expected", [
        ("कृपया अपना ओटीपी बताइए।", "fail"),           # "अपना" contains "ना" but is not a negation
        ("अपना ओटीपी किसी को भी मत बताइए।", "pass"),
        ("ओटीपी कभी share ना करें।", "pass"),
    ])
    def test_devanagari_sensitive_requests(self, text: str, expected: str) -> None:
        assert check([OPEN, ev_agent(text)])["no_sensitive_data_request"].result == expected

    def test_devanagari_digits_fail_speech_format(self) -> None:
        assert check([OPEN, ev_agent("आपकी EMI ४५०० रुपये है।")])["speech_format"].result == "fail"


class TestPerLanguagePrompts:
    def test_v3_has_a_prompt_per_language_and_v1_shares_one(self) -> None:
        from lab.simulate import prompt_texts

        v3 = prompt_texts("v3")
        assert "This call is in Hindi" in v3["hi"] and "This call is in English" in v3["en"]
        v1 = prompt_texts("v1")
        assert v1["hi"] == v1["en"]

    def test_simulation_uses_the_borrowers_language(self) -> None:
        from lab.simulate import prompt_texts

        hindi, english = ScriptedAgent([]), ScriptedAgent([])
        simulate(BY_ID["pay_today_hi"], prompt_texts("v3"), hindi, ScriptedText(["[END_CALL]"]), "t:hi")
        simulate(BY_ID["pay_today_en"], prompt_texts("v3"), english, ScriptedText(["[END_CALL]"]), "t:en")
        assert "This call is in Hindi" in hindi.system
        assert "This call is in English" in english.system


def test_before_after_uses_only_shared_personas() -> None:
    import json as _json

    import pandas as pd

    from lab.report_results import same_persona_comparison

    def row(version, persona, passed):
        return {"version": version, "persona_id": persona, "persona_passed": passed, "compliant": passed,
                "outcome": "promise_to_pay", "expected_outcome": _json.dumps(["promise_to_pay"])}

    rt = pd.DataFrame([row("v1", "a", False), row("v1", "b", True), row("v4", "a", True), row("v4", "b", True), row("v4", "c", False)])
    compare, n = same_persona_comparison(rt)
    assert n == 2 and list(compare["version"]) == ["v1", "v4"]
    assert list(compare["persona_pass"]) == [0.5, 1.0]  # persona "c" (v4 only) is left out
