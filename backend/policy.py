"""Rules the model cannot override. Every function is pure and takes `now` so tests can fix the clock.

All datetimes are timezone-aware and in IST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from backend import config
from backend.config import IST


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""
    message: str = ""


Window = tuple[time, time]


def parse_windows(spec: str) -> list[Window]:
    """Parse "09:00-11:00;17:00-18:30" into time pairs. Empty string means none."""
    windows: list[Window] = []
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        start, end = part.split("-")
        windows.append((time.fromisoformat(start), time.fromisoformat(end)))
    return windows


def _in_windows(t: time, windows: list[Window]) -> Window | None:
    for start, end in windows:
        if start <= t < end:
            return (start, end)
    return None


def can_start_call(
    now: datetime,
    attempts_today: list[datetime],
    do_not_call: list[Window],
    cooloff_until: datetime | None,
    stop_request_open: bool,
) -> Decision:
    """Pre-dial gate. Checked before every call, including booked callbacks."""
    t = now.astimezone(IST).time()
    if not (config.CALL_WINDOW_START <= t <= config.LAST_CALL_START):
        return Decision(False, "outside_hours", "Calls may start only between 08:00 and 18:45 IST.")
    if len(attempts_today) >= config.MAX_ATTEMPTS_PER_DAY:
        return Decision(False, "attempt_limit", "Daily attempt limit reached for this borrower.")
    if attempts_today:
        gap = now - max(attempts_today)
        if gap < timedelta(hours=config.MIN_HOURS_BETWEEN_ATTEMPTS):
            return Decision(False, "too_soon", "Last attempt was less than two hours ago.")
    if _in_windows(t, do_not_call):
        return Decision(False, "borrower_time_preference", "Borrower asked not to be called at this time.")
    if cooloff_until and now < cooloff_until:
        return Decision(False, "hardship_cooloff", "Borrower is in a hardship cool-off period.")
    if stop_request_open:
        return Decision(False, "stop_request_open", "A stop-calling request is waiting for human review.")
    return Decision(True)


def validate_promise(date_iso: str, amount_inr: float, outstanding_inr: float, today: date) -> Decision:
    try:
        promised = date.fromisoformat(date_iso)
    except (TypeError, ValueError):
        return Decision(False, "bad_date", "Date must be YYYY-MM-DD. Confirm the date with the borrower.")
    if promised < today:
        return Decision(False, "date_in_past", "That date has passed. Ask for a date from today onwards.")
    latest = today + timedelta(days=config.PTP_MAX_DAYS)
    if promised > latest:
        return Decision(
            False,
            "date_too_far",
            f"Only dates up to {latest.isoformat()} can be logged. Ask if that date works; "
            "if not, a person must review the request.",
        )
    if not isinstance(amount_inr, (int, float)) or amount_inr <= 0:
        return Decision(False, "bad_amount", "Amount must be more than zero. Confirm the amount.")
    if amount_inr > outstanding_inr:
        return Decision(
            False,
            "amount_above_outstanding",
            f"Amount is more than the {outstanding_inr:g} rupees due. Confirm the amount.",
        )
    return Decision(True)


def _clamp_to_window(dt: datetime) -> datetime:
    t = dt.time()
    if t < config.CALL_WINDOW_START:
        return dt.replace(hour=config.CALL_WINDOW_START.hour, minute=config.CALL_WINDOW_START.minute)
    if t > config.LAST_CALL_START:
        nxt = dt + timedelta(days=1)
        return nxt.replace(hour=config.CALL_WINDOW_START.hour, minute=config.CALL_WINDOW_START.minute)
    return dt


def nearest_allowed_callback(wanted: datetime, now: datetime, do_not_call: list[Window]) -> datetime | None:
    """The first allowed slot at or after the wanted time, or None if none fits in the booking horizon."""
    earliest = (now + timedelta(minutes=15)).replace(second=0, microsecond=0)
    dt = max(wanted, earliest)
    horizon = now + timedelta(days=config.CALLBACK_MAX_DAYS)
    for _ in range(50):
        dt = _clamp_to_window(dt)
        blocked = _in_windows(dt.time(), do_not_call)
        if blocked:
            dt = dt.replace(hour=blocked[1].hour, minute=blocked[1].minute)
            continue
        return dt if dt <= horizon else None
    return None


def validate_callback(datetime_iso: str, now: datetime, do_not_call: list[Window]) -> tuple[Decision, datetime | None]:
    """Returns the decision and, when rejected, the nearest allowed time to offer instead."""
    try:
        wanted = datetime.fromisoformat(datetime_iso)
    except (TypeError, ValueError):
        return Decision(False, "bad_datetime", "Time must be YYYY-MM-DDTHH:MM in India time."), None
    wanted = wanted.replace(tzinfo=IST) if wanted.tzinfo is None else wanted.astimezone(IST)

    if wanted <= now:
        reason, msg = "in_past", "That time has passed."
    elif wanted > now + timedelta(days=config.CALLBACK_MAX_DAYS):
        reason, msg = "too_far", "Callbacks can be booked only within the next seven days."
    elif not (config.CALL_WINDOW_START <= wanted.time() <= config.LAST_CALL_START):
        reason, msg = "outside_hours", "Callbacks can be booked only between 08:00 and 18:45 India time."
    elif _in_windows(wanted.time(), do_not_call):
        reason, msg = "borrower_time_preference", "The borrower asked not to be called at that time."
    else:
        return Decision(True), wanted

    if reason == "too_far":
        # Offer the same time of day on the last day that can still be booked.
        horizon = now + timedelta(days=config.CALLBACK_MAX_DAYS)
        wanted = wanted.replace(year=horizon.year, month=horizon.month, day=horizon.day)
        if wanted > horizon:
            wanted -= timedelta(days=1)
    return Decision(False, reason, msg), nearest_allowed_callback(wanted, now, do_not_call)


def cooloff_until(category: str, now: datetime) -> datetime | None:
    if category in config.COOLOFF_CATEGORIES:
        return now + timedelta(days=config.HARDSHIP_COOLOFF_DAYS)
    return None


def days_past_due(due_date: date, today: date) -> int:
    return max(0, (today - due_date).days)
