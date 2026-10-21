"""Display formatting in the house style: Indian number grouping, Rs amounts, dates like 15 Nov 2026."""

from __future__ import annotations

from datetime import date, datetime


def group_indian(n: int) -> str:
    """1234567 -> 12,34,567."""
    sign, s = ("-" if n < 0 else ""), str(abs(int(n)))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts) + "," + tail


def inr(amount: float | None) -> str:
    """Rs 4,500 below one lakh; Rs 1.2 lakh and Rs 3.4 crore above."""
    if amount is None:
        return ""
    a = float(amount)
    if abs(a) >= 1e7:
        return f"Rs {a / 1e7:.1f} crore".replace(".0 ", " ")
    if abs(a) >= 1e5:
        return f"Rs {a / 1e5:.1f} lakh".replace(".0 ", " ")
    return f"Rs {group_indian(round(a))}"


def count(n: int | None) -> str:
    return "" if n is None else group_indian(n)


def pct(x: float | None, digits: int = 0) -> str:
    return "" if x is None else f"{x * 100:.{digits}f}%"


def day(value: str | date | datetime | None) -> str:
    """15 Nov 2026. Accepts ISO strings."""
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return f"{value.day} {value.strftime('%b %Y')}"


def day_time(value: str | datetime | None) -> str:
    """15 Nov 2026, 17:30."""
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return f"{day(value)}, {value.strftime('%H:%M')}"


ACRONYMS = {"ptp": "PTP", "ai": "AI", "otp": "OTP", "emi": "EMI", "rbi": "RBI", "upi": "UPI"}


def label(name: str) -> str:
    """snake_case to a sentence-case label: no_disclosure_before_verification -> No disclosure before verification."""
    words = [ACRONYMS.get(w, w) for w in name.split("_")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def field_value(name: str, value: object) -> str:
    """A post-call field shown the way a person reads it."""
    if hasattr(value, "item"):  # numpy scalars from pandas
        value = value.item()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if name.endswith("_amount") and isinstance(value, (int, float)):
        return inr(value)
    if name.endswith("_date") and isinstance(value, str):
        try:
            return day(value)
        except ValueError:
            return value
    if isinstance(value, str) and value.replace("_", "").isalpha() and "_" in value:
        return label(value)
    return str(value)
