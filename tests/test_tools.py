from __future__ import annotations

from backend.db import Store
from backend.tools import CallContext, run_tool
from tests.conftest import NOW, SUNIL


def run(store: Store, ctx: CallContext, name: str, **args):
    return run_tool(store, ctx, name, args, NOW)


def verify(store: Store, ctx: CallContext) -> None:
    assert run(store, ctx, "verify_identity", answer_type="birth_year", answer="1991")["verified"]


class TestVerifyIdentity:
    def test_correct_birth_year(self, store, ctx) -> None:
        assert run(store, ctx, "verify_identity", answer_type="birth_year", answer="1991") == {
            "verified": True,
            "attempts_left": 1,
        }

    def test_wrong_then_right_city(self, store, ctx) -> None:
        assert run(store, ctx, "verify_identity", answer_type="birth_year", answer="1990")["attempts_left"] == 1
        r = run(store, ctx, "verify_identity", answer_type="registered_city", answer="nagpur ")
        assert r == {"verified": True, "attempts_left": 0}

    def test_two_wrong_answers_lock_the_call(self, store, ctx) -> None:
        run(store, ctx, "verify_identity", answer_type="birth_year", answer="1990")
        run(store, ctx, "verify_identity", answer_type="registered_city", answer="Pune")
        r = run(store, ctx, "verify_identity", answer_type="birth_year", answer="1991")
        assert r == {"verified": False, "attempts_left": 0}

    def test_two_digit_year_does_not_use_an_attempt(self, store, ctx) -> None:
        r = run(store, ctx, "verify_identity", answer_type="birth_year", answer="91")
        assert r["attempts_left"] == 2 and "four-digit" in r["error"]

    def test_never_returns_the_right_answer(self, store, ctx) -> None:
        r = run(store, ctx, "verify_identity", answer_type="birth_year", answer="1990")
        assert "1991" not in str(r) and "Nagpur" not in str(r)

    def test_city_aliases(self, store) -> None:
        store.conn.execute("UPDATE borrowers SET registered_city = 'Mumbai' WHERE borrower_id = ?", (SUNIL,))
        ctx = CallContext("call_alias", SUNIL, "web_call")
        assert run(store, ctx, "verify_identity", answer_type="registered_city", answer="Bombay")["verified"]

    def test_state_is_per_call(self, store, ctx) -> None:
        verify(store, ctx)
        other = CallContext("call_test_2", SUNIL, "web_call")
        assert run(store, other, "send_payment_link", channel="sms")["ok"] is False


class TestIdentityGate:
    """Every tool that touches the loan refuses until identity is verified."""

    def test_money_tools_refuse_before_verification(self, store, ctx) -> None:
        calls = {
            "send_payment_link": {"channel": "sms"},
            "log_promise_to_pay": {"date_iso": "2026-10-24", "amount_inr": 4500, "mode": "payment_link"},
            "flag_hardship": {"category": "job_loss", "note": "Lost job"},
            "log_dispute": {"category": "already_paid", "note": "Paid on 18 Oct"},
        }
        for name, args in calls.items():
            r = run_tool(store, ctx, name, args, NOW)
            assert r["ok"] is False and "not verified" in r["error"], name
        assert store.all("SELECT * FROM promises") == []
        assert store.all("SELECT * FROM payment_links") == []

    def test_callback_and_escalation_work_before_verification(self, store, ctx) -> None:
        assert run(store, ctx, "schedule_callback", datetime_iso="2026-10-21T17:00")["accepted"]
        assert run(store, ctx, "escalate_to_human", reason="severe_distress", note="x")["queued"]
        row = store.one("SELECT booked_with_borrower FROM callbacks")
        assert row == {"booked_with_borrower": 0}


class TestPaymentLink:
    def test_sends_once_per_call(self, store, ctx) -> None:
        verify(store, ctx)
        first = run(store, ctx, "send_payment_link", channel="sms")
        assert first["sent"] and first["link_id"].startswith("PL-") and first["require_repeat_verbatim"] is False
        second = run(store, ctx, "send_payment_link", channel="sms")
        assert second["sent"] is False
        assert len(store.all("SELECT * FROM payment_links")) == 1


class TestPromise:
    def test_valid_promise_logged(self, store, ctx) -> None:
        verify(store, ctx)
        r = run(store, ctx, "log_promise_to_pay", date_iso="2026-10-24", amount_inr=4500, mode="payment_link")
        assert r == {"accepted": True, "partial": False}

    def test_partial_promise_marked(self, store, ctx) -> None:
        verify(store, ctx)
        r = run(store, ctx, "log_promise_to_pay", date_iso="2026-10-24", amount_inr="2000", mode="branch")
        assert r == {"accepted": True, "partial": True}
        assert store.one("SELECT amount_inr, partial FROM promises") == {"amount_inr": 2000.0, "partial": 1}

    def test_rejects_beyond_seven_days_with_speakable_reason(self, store, ctx) -> None:
        verify(store, ctx)
        r = run(store, ctx, "log_promise_to_pay", date_iso="2026-11-10", amount_inr=4500, mode="other")
        assert r["accepted"] is False and r["reason"] == "date_too_far"
        assert store.all("SELECT * FROM promises") == []

    def test_rejects_amount_above_outstanding(self, store, ctx) -> None:
        verify(store, ctx)
        r = run(store, ctx, "log_promise_to_pay", date_iso="2026-10-24", amount_inr=9000, mode="other")
        assert r["reason"] == "amount_above_outstanding"

    def test_missing_amount(self, store, ctx) -> None:
        verify(store, ctx)
        assert run(store, ctx, "log_promise_to_pay", date_iso="2026-10-24", mode="other")["reason"] == "bad_amount"


class TestHardship:
    def test_medical_starts_cooloff_and_queues_human(self, store, ctx) -> None:
        verify(store, ctx)
        assert run(store, ctx, "flag_hardship", category="medical", note="Father in hospital") == {"logged": True}
        assert store.latest_cooloff(SUNIL) is not None
        assert store.one("SELECT reason, priority FROM human_queue") == {"reason": "hardship", "priority": "normal"}

    def test_job_loss_has_no_cooloff(self, store, ctx) -> None:
        verify(store, ctx)
        run(store, ctx, "flag_hardship", category="job_loss", note="Lost job last month")
        assert store.latest_cooloff(SUNIL) is None

    def test_unknown_category_stored_as_other(self, store, ctx) -> None:
        verify(store, ctx)
        run(store, ctx, "flag_hardship", category="flood", note="House flooded")
        assert store.one("SELECT category FROM hardship_flags") == {"category": "other"}


class TestDispute:
    def test_already_paid_logged_and_queued(self, store, ctx) -> None:
        verify(store, ctx)
        r = run(store, ctx, "log_dispute", category="already_paid", note="Paid by UPI on 18 Oct, ref 12345")
        assert r == {"logged": True}
        assert store.one("SELECT reason FROM human_queue") == {"reason": "dispute_already_paid"}


class TestCallback:
    def test_rejected_outside_hours_with_nearest_slot(self, store, ctx) -> None:
        r = run(store, ctx, "schedule_callback", datetime_iso="2026-10-21T21:00")
        assert r["accepted"] is False and r["nearest_allowed"] == "2026-10-22T08:00"
        assert store.all("SELECT * FROM callbacks") == []

    def test_respects_borrower_time_preference(self, store, ctx) -> None:
        store.conn.execute("UPDATE borrowers SET do_not_call_windows = '17:00-18:30' WHERE borrower_id = ?", (SUNIL,))
        r = run(store, ctx, "schedule_callback", datetime_iso="2026-10-21T17:30")
        assert r["reason"] == "borrower_time_preference" and r["nearest_allowed"] == "2026-10-21T18:30"


class TestEscalation:
    def test_distress_is_urgent(self, store, ctx) -> None:
        r = run(store, ctx, "escalate_to_human", reason="severe_distress", note="Said they want to end it")
        assert r == {"queued": True, "live_transfer_available": False}
        assert store.one("SELECT priority FROM human_queue") == {"priority": "urgent"}

    def test_stop_calling_blocks_future_dials(self, store, ctx) -> None:
        run(store, ctx, "escalate_to_human", reason="stop_calling", note="Do not call at office")
        assert store.stop_request_open(SUNIL)

    def test_live_transfer_only_on_phone_with_desk_number(self, store) -> None:
        web = CallContext("c1", SUNIL, "web_call", human_desk_number="+911234567890")
        phone_no_desk = CallContext("c2", SUNIL, "phone_call")
        phone = CallContext("c3", SUNIL, "phone_call", human_desk_number="+911234567890")
        assert run(store, web, "escalate_to_human", reason="borrower_request", note="")["live_transfer_available"] is False
        assert run(store, phone_no_desk, "escalate_to_human", reason="borrower_request", note="")["live_transfer_available"] is False
        assert run(store, phone, "escalate_to_human", reason="borrower_request", note="")["live_transfer_available"] is True


class TestRunTool:
    def test_every_call_is_logged(self, store, ctx) -> None:
        run(store, ctx, "verify_identity", answer_type="birth_year", answer="1991")
        run(store, ctx, "no_such_tool")
        events = store.all("SELECT tool_name FROM tool_events ORDER BY id")
        assert [e["tool_name"] for e in events] == ["verify_identity", "no_such_tool"]

    def test_unknown_borrower(self, store) -> None:
        ctx = CallContext("c9", "KF-NOPE", "web_call")
        r = run(store, ctx, "verify_identity", answer_type="birth_year", answer="1991")
        assert r["ok"] is False and "not found" in r["error"]
