"""Settings and fixed business rules. Policy limits live here so they change in one place."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

# Calling window. Kaveri Finance is stricter than RBI: no calls or callbacks
# outside this window, even if the borrower asks. The last start is 18:45 so a
# call capped at 12 minutes ends before 19:00.
CALL_WINDOW_START = time(8, 0)
CALL_WINDOW_END = time(19, 0)
LAST_CALL_START = time(18, 45)

MAX_ATTEMPTS_PER_DAY = 3
MIN_HOURS_BETWEEN_ATTEMPTS = 2
MAX_IDENTITY_ATTEMPTS = 2
MAX_PAYMENT_LINKS_PER_CALL = 1
PTP_MAX_DAYS = 7
CALLBACK_MAX_DAYS = 7
HARDSHIP_COOLOFF_DAYS = 14
COOLOFF_CATEGORIES = {"medical", "bereavement"}

LENDER_NAME = "Kaveri Finance"
GRIEVANCE_CONTACT = (
    "Grievance officer Anita Rao, phone 1800 000 0000, email grievance@kaverifinance.example"
)


@dataclass(frozen=True)
class Settings:
    retell_api_key: str
    db_path: Path
    human_desk_number: str
    agent_ids_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        return cls(
            retell_api_key=os.environ.get("RETELL_API_KEY", ""),
            db_path=Path(os.environ.get("DB_PATH", ROOT / "data" / "collections.db")),
            human_desk_number=os.environ.get("HUMAN_DESK_NUMBER", ""),
            agent_ids_file=ROOT / "agent_config" / "retell_ids.json",
        )
