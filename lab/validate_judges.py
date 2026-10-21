"""Measure each judge against human labels.

    python -m lab.validate_judges --counts               # labels per judge and split, no API calls
    python -m lab.validate_judges --split dev            # iterate on judge prompts with this
    python -m lab.validate_judges --split test --final   # held-out numbers, report once

Pass is the positive class:
    TPR = share of human-Pass calls the judge also marked Pass
    TNR = share of human-Fail calls the judge also marked Fail
A judge that passes everything gets TPR 1.0 and TNR 0.0, so both numbers matter.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from backend.config import IST, ROOT, Settings
from lab import judges as J
from lab.judge_data import few_shot_for, labelled, split_of
from lab.llm import chat_models
from lab.store import LabStore

REPORTS_DIR = ROOT / "lab" / "reports"
TARGET = 0.9


def metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """pairs are (human label, judge verdict)."""
    tp = sum(1 for h, j in pairs if h == "Pass" and j == "Pass")
    fn = sum(1 for h, j in pairs if h == "Pass" and j == "Fail")
    tn = sum(1 for h, j in pairs if h == "Fail" and j == "Fail")
    fp = sum(1 for h, j in pairs if h == "Fail" and j == "Pass")
    return {
        "n": len(pairs), "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "tpr": round(tp / (tp + fn), 3) if tp + fn else None,
        "tnr": round(tn / (tn + fp), 3) if tn + fp else None,
    }


def counts(store: LabStore) -> None:
    print(f"{'judge':<36} {'few_shot':>9} {'dev':>5} {'test':>5}   pass/fail")
    for judge in J.JUDGES:
        rows = labelled(store, judge)
        by_split = {s: sum(1 for r in rows if split_of(r["run_id"], judge) == s) for s in ("few_shot", "dev", "test")}
        p = sum(1 for r in rows if r["label"] == "Pass")
        print(f"{judge:<36} {by_split['few_shot']:>9} {by_split['dev']:>5} {by_split['test']:>5}   {p}/{len(rows) - p}")


def validate(store: LabStore, judge_model: Any, split: str, judges: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = {"split": split, "created_at": datetime.now(IST).isoformat(timespec="seconds"), "judges": {}}
    for judge in judges:
        rows = labelled(store, judge, split)
        if not rows:
            continue
        few = few_shot_for(store, judge)
        psha = J.prompt_sha(judge, few)
        pairs: list[tuple[str, str]] = []
        disagreements: list[dict[str, Any]] = []
        for r in rows:
            cached = store.cached_judge(r["run_id"], judge, psha)
            if cached:
                verdict, reasoning = cached["verdict"], cached["reasoning"]
            else:
                events = json.loads(r["events_json"])
                v = J.run_judge(judge_model, judge, events, J.verified_from_events(events), few)
                store.save_judge(r["run_id"], judge, psha, v.verdict, v.reasoning, judge_model.name, datetime.now(IST))
                verdict, reasoning = v.verdict, v.reasoning
            pairs.append((r["label"], verdict))
            if verdict != r["label"]:
                disagreements.append({"run_id": r["run_id"], "human": r["label"], "judge": verdict,
                                      "judge_reasoning": reasoning, "human_note": r["note"]})
        m = metrics(pairs)
        store.insert("judge_validation", {"judge": judge, "split": split, "prompt_sha": psha, "n": m["n"],
                                          "tp": m["tp"], "tn": m["tn"], "fp": m["fp"], "fn": m["fn"],
                                          "created_at": report["created_at"]})
        report["judges"][judge] = {**m, "prompt_sha": psha, "disagreements": disagreements}
    return report


def print_report(report: dict[str, Any]) -> None:
    print(f"\nJudge validation on {report['split']} split (target {TARGET} for TPR and TNR)")
    print(f"{'judge':<36} {'n':>4} {'TPR':>6} {'TNR':>6}")
    for judge, m in report["judges"].items():
        fmt = lambda x: "  n/a" if x is None else f"{x:.2f}"  # noqa: E731
        flag = "" if (m["tpr"] or 0) >= TARGET and (m["tnr"] or 0) >= TARGET else "  below target"
        print(f"{judge:<36} {m['n']:>4} {fmt(m['tpr']):>6} {fmt(m['tnr']):>6}{flag}")
    for judge, m in report["judges"].items():
        for d in m["disagreements"]:
            print(f"\n[{judge}] {d['run_id']}: human {d['human']}, judge {d['judge']}")
            print(f"  judge said: {d['judge_reasoning']}")
            if d["human_note"]:
                print(f"  your note: {d['human_note']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lab.validate_judges")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--judges", help="Comma-separated judges (default all)")
    parser.add_argument("--counts", action="store_true", help="Show label counts and stop")
    parser.add_argument("--final", action="store_true", help="Required for the test split")
    args = parser.parse_args(argv)

    store = LabStore(Settings.from_env().db_path)
    if args.counts:
        counts(store)
        return 0
    if args.split == "test" and not args.final:
        print("The test split is held out. Run it once, when judge prompts are final, with --final.")
        return 2
    if args.split == "test" and store.one("SELECT 1 AS x FROM judge_validation WHERE split = 'test'"):
        print("Note: the test split has been run before. Numbers from a second run are no longer held out.")

    judges = args.judges.split(",") if args.judges else J.JUDGES
    try:
        judge_model = chat_models(0.0).judge
    except RuntimeError as e:
        print(e)
        return 2
    report = validate(store, judge_model, args.split, judges)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"judge_validation_{args.split}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
