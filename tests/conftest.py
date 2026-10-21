from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import IST  # noqa: E402
from backend.db import Store  # noqa: E402
from backend.tools import CallContext  # noqa: E402

# Wednesday 21 Oct 2026, 11:00 IST. Sunil Verma's EMI of 4500 was due on 5 Oct.
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)
SUNIL = "KF-TW-000184"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.load_borrowers()
    return s


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(call_id="call_test_1", borrower_id=SUNIL, call_type="web_call")


def at(hour: int, minute: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)
