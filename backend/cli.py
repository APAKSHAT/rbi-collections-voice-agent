"""Command line for setup and test calls.

    python -m backend.cli init-db
    python -m backend.cli check KF-TW-000184
    python -m backend.cli call KF-TW-000184                 # web call in the browser
    python -m backend.cli call KF-TW-000184 --phone --from +1XXXXXXXXXX --to +91XXXXXXXXXX

Every call is checked against policy.py first and refused outside the allowed hours.
Phone calls need an explicit --to number: the fake borrowers' numbers are not real.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime

from backend import calls
from backend.config import IST, Settings
from backend.db import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Create tables and load data/fake_borrowers.csv")
    check = sub.add_parser("check", help="Show whether a borrower may be called now")
    check.add_argument("borrower_id")
    call = sub.add_parser("call", help="Place a test call")
    call.add_argument("borrower_id")
    call.add_argument("--phone", action="store_true", help="Phone call instead of a web call")
    call.add_argument("--from", dest="from_number", help="Your Retell number, E.164")
    call.add_argument("--to", dest="to_number", help="Number to ring, E.164. Use your own phone.")
    call.add_argument("--port", type=int, default=8000, help="Local backend port for the web call page")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = Store(settings.db_path)
    now = datetime.now(IST)

    if args.command == "init-db":
        print(f"Loaded {store.load_borrowers()} borrowers into {settings.db_path}")
        return 0

    decision, borrower = calls.precheck(store, args.borrower_id, now)
    if args.command == "check":
        print("Allowed" if decision.ok else f"Refused: {decision.message}")
        return 0 if decision.ok else 1

    if not decision.ok or borrower is None:
        print(f"Refused: {decision.message}")
        return 1

    if not args.phone:
        # The page asks the backend to create the call, so the policy check runs again at click time.
        url = f"http://localhost:{args.port}/webcall?borrower_id={args.borrower_id}"
        print(f"Open {url} and press Start. The backend must be running on port {args.port}.")
        webbrowser.open(url)
        return 0

    if not (args.from_number and args.to_number):
        print("Phone calls need --from (your Retell number) and --to (your own phone).")
        return 2
    call_id = calls.create_phone_call(settings, borrower, now, args.from_number, args.to_number)
    calls.record_attempt(store, args.borrower_id, "phone", now, decision, call_id)
    print(f"Placed phone call {call_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
