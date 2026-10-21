from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from backend import policy
from tests.conftest import NOW, at

TODAY = NOW.date()


class TestCanStartCall:
    def ok(self, now=NOW, attempts=None, dnc=None, cooloff=None, stop=False) -> policy.Decision:
        return policy.can_start_call(now, attempts or [], dnc or [], cooloff, stop)

    @pytest.mark.parametrize("hour,minute", [(8, 0), (12, 30), (18, 45)])
    def test_allowed_inside_window(self, hour: int, minute: int) -> None:
        assert self.ok(now=at(hour, minute)).ok

    @pytest.mark.parametrize("hour,minute", [(7, 59), (18, 46), (19, 0), (21, 0), (0, 30)])
    def test_refused_outside_window(self, hour: int, minute: int) -> None:
        d = self.ok(now=at(hour, minute))
        assert not d.ok and d.reason == "outside_hours"

    def test_window_uses_ist_even_if_clock_is_utc(self) -> None:
        # 03:00 UTC is 08:30 IST.
        from datetime import datetime, timezone

        assert self.ok(now=datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)).ok

    def test_attempt_limit(self) -> None:
        d = self.ok(attempts=[at(8), at(10), at(12)], now=at(15))
        assert d.reason == "attempt_limit"

    def test_minimum_gap_between_attempts(self) -> None:
        assert self.ok(attempts=[at(10)], now=at(11, 59)).reason == "too_soon"
        assert self.ok(attempts=[at(10)], now=at(12)).ok

    def test_borrower_time_preference(self) -> None:
        dnc = policy.parse_windows("09:00-11:00")
        assert self.ok(now=at(10), dnc=dnc).reason == "borrower_time_preference"
        assert self.ok(now=at(11), dnc=dnc).ok

    def test_hardship_cooloff(self) -> None:
        assert self.ok(cooloff=NOW + timedelta(days=1)).reason == "hardship_cooloff"
        assert self.ok(cooloff=NOW - timedelta(minutes=1)).ok

    def test_open_stop_request(self) -> None:
        assert self.ok(stop=True).reason == "stop_request_open"


class TestPromise:
    def check(self, d: str, amount: float, outstanding: float = 4500) -> policy.Decision:
        return policy.validate_promise(d, amount, outstanding, TODAY)

    def test_today_and_seventh_day_allowed(self) -> None:
        assert self.check("2026-10-21", 4500).ok
        assert self.check("2026-10-28", 4500).ok

    def test_eighth_day_rejected(self) -> None:
        d = self.check("2026-10-29", 4500)
        assert d.reason == "date_too_far" and "2026-10-28" in d.message

    def test_past_date_rejected(self) -> None:
        assert self.check("2026-10-20", 4500).reason == "date_in_past"

    @pytest.mark.parametrize("bad", ["", "28/10/2026", "next friday", "2026-13-01"])
    def test_bad_date_format(self, bad: str) -> None:
        assert self.check(bad, 4500).reason == "bad_date"

    @pytest.mark.parametrize("amount", [0, -100])
    def test_amount_must_be_positive(self, amount: float) -> None:
        assert self.check("2026-10-24", amount).reason == "bad_amount"

    def test_amount_above_outstanding(self) -> None:
        assert self.check("2026-10-24", 4501).reason == "amount_above_outstanding"

    def test_partial_amount_allowed(self) -> None:
        assert self.check("2026-10-24", 2000).ok


class TestCallback:
    def check(self, when: str, now=NOW, dnc: str = ""):
        return policy.validate_callback(when, now, policy.parse_windows(dnc))

    def test_accepts_time_inside_window(self) -> None:
        d, slot = self.check("2026-10-21T17:00")
        assert d.ok and slot == at(17)

    def test_9pm_offers_8am_next_day(self) -> None:
        d, slot = self.check("2026-10-21T21:00")
        assert d.reason == "outside_hours" and slot == at(8, day=22)

    def test_early_morning_offers_8am_same_day(self) -> None:
        d, slot = self.check("2026-10-22T06:30")
        assert d.reason == "outside_hours" and slot == at(8, day=22)

    def test_1850_rejected_because_last_start_is_1845(self) -> None:
        d, slot = self.check("2026-10-21T18:50")
        assert d.reason == "outside_hours" and slot == at(8, day=22)

    def test_past_time_rejected(self) -> None:
        d, slot = self.check("2026-10-21T10:00")
        assert d.reason == "in_past" and slot is not None and slot > NOW

    def test_too_far_offers_last_bookable_day(self) -> None:
        d, slot = self.check("2026-11-05T10:00")
        assert d.reason == "too_far" and slot is not None
        assert slot <= NOW + timedelta(days=7) and slot.time() == time(10, 0)

    def test_borrower_time_preference_moves_to_end_of_window(self) -> None:
        d, slot = self.check("2026-10-22T09:30", dnc="09:00-11:00")
        assert d.reason == "borrower_time_preference" and slot == at(11, day=22)

    def test_bad_format(self) -> None:
        d, slot = self.check("tomorrow evening")
        assert d.reason == "bad_datetime" and slot is None

    def test_naive_time_is_read_as_ist(self) -> None:
        d, slot = self.check("2026-10-22T08:00")
        assert d.ok and slot is not None and slot.utcoffset() == timedelta(hours=5, minutes=30)


def test_parse_windows() -> None:
    assert policy.parse_windows("") == []
    assert policy.parse_windows("09:00-11:00;17:00-18:30") == [(time(9), time(11)), (time(17), time(18, 30))]


def test_cooloff_only_for_medical_and_bereavement() -> None:
    assert policy.cooloff_until("medical", NOW) == NOW + timedelta(days=14)
    assert policy.cooloff_until("bereavement", NOW) is not None
    assert policy.cooloff_until("job_loss", NOW) is None


def test_days_past_due() -> None:
    assert policy.days_past_due(date(2026, 10, 5), TODAY) == 16
    assert policy.days_past_due(date(2026, 10, 25), TODAY) == 0


def test_fake_borrowers_have_unique_ids() -> None:
    import csv

    from backend.config import ROOT

    rows = list(csv.DictReader(open(ROOT / "data" / "fake_borrowers.csv", encoding="utf-8")))
    ids = [r["borrower_id"] for r in rows]
    assert len(ids) == len(set(ids))
    assert len({r["loan_last4"] for r in rows}) == len(rows)
    # Numbers must not route to a real phone: Indian mobiles start with 6 to 9.
    assert all(r["phone_e164"].startswith("+9100000") for r in rows)
