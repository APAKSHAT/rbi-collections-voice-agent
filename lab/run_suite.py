"""Run every persona against one prompt version, N times each, then check and judge every call.

    python -m lab.run_suite --prompt v1 --estimate                       # counts only, no API calls
    python -m lab.run_suite --prompt v1                                  # all personas x 3 seeds
    python -m lab.run_suite --prompt v1 --seeds 1 --critical-seeds 3     # free-tier plan
    python -m lab.run_suite --prompt v1 --personas job_loss,wife_answers --seeds 1

Runs are saved as they finish, so the same command resumes after a free-tier
limit stops it. Results go to SQLite and to lab/runs/<suite_id>/*.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

from backend.config import IST, ROOT, Settings
from lab import judges as J
from lab.checks import ALWAYS_MUST_PASS, run_checks
from lab.judge_data import few_shot_for
from lab.llm import Models, QuotaExhausted, chat_models, env_models
from lab.simulate import DEFAULT_SIM_TIME, load_personas, prompt_texts, sha, simulate
from lab.store import LabStore

RUNS_DIR = ROOT / "lab" / "runs"
REPORTS_DIR = ROOT / "lab" / "reports"

# Rough sizes per simulated call, used only for the estimate.
EST_AGENT_REQUESTS = 14
EST_SIM_REQUESTS = 10
EST_AGENT_TOKENS = 3000  # prompt, tools and history per agent request
EST_SIM_TOKENS = 1000
EST_JUDGE_TOKENS = 2500


def persona_passed(persona: dict[str, Any], checks: dict[str, str], verdicts: dict[str, str]) -> bool:
    for item in ALWAYS_MUST_PASS + persona.get("must_pass", []):
        if item in checks and checks[item] == "fail":
            return False
        if item in verdicts and verdicts[item] == "Fail":
            return False
    return True


def seeds_for(persona: dict[str, Any], seeds: int, critical_seeds: int | None) -> int:
    return critical_seeds if persona.get("critical") and critical_seeds is not None else seeds


def estimate(personas: list[dict[str, Any]], seeds: int, critical_seeds: int | None, judges: bool) -> dict[str, int]:
    runs = sum(seeds_for(p, seeds, critical_seeds) for p in personas)
    judge_calls = sum(len(J.judges_for(p)) * seeds_for(p, seeds, critical_seeds) for p in personas) if judges else 0
    return {
        "runs": runs,
        "agent": runs * EST_AGENT_REQUESTS,
        "sim": runs * EST_SIM_REQUESTS,
        "judge": judge_calls,
        "agent_tokens": runs * EST_AGENT_REQUESTS * EST_AGENT_TOKENS,
        "sim_tokens": runs * EST_SIM_REQUESTS * EST_SIM_TOKENS,
        "judge_tokens": judge_calls * EST_JUDGE_TOKENS,
    }


def print_estimate(suite_id: str, est: dict[str, int]) -> None:
    names = env_models()
    print(f"Suite {suite_id}: {est['runs']} simulated calls.")
    days = 0.0
    for role, env, default in (("agent", "AGENT_TPD", "200000"), ("sim", "SIM_TPD", "200000"), ("judge", "JUDGE_TPD", "200000")):
        tpd = int(os.environ.get(env, default))
        need = est[f"{role}_tokens"]
        days = max(days, need / tpd)
        print(f"  {role:<6} {names[role]:<28} {est[role]:>6} requests  {need / 1e6:>5.1f}M tokens  ({need / tpd:.1f} days at {tpd:,} tokens/day)")
    print(f"At these daily limits the suite takes about {days:.0f} day(s). It resumes where it stopped.")


def write_jsonl(suite_id: str, run_id: str, meta: dict[str, Any], events: list[dict[str, Any]]) -> None:
    folder = RUNS_DIR / suite_id
    folder.mkdir(parents=True, exist_ok=True)
    name = run_id.split(":", 1)[1].replace(":", "__")
    with open(folder / f"{name}.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def run_one(
    store: LabStore, models: Models, persona: dict[str, Any], seed: int, suite_id: str,
    prompt_text: str | dict[str, str], sim_time: datetime, max_turns: int, use_judges: bool,
) -> None:
    run_id = f"{suite_id}:{persona['id']}:s{seed}"
    now = datetime.now(IST)
    print(f"  {persona['id']} s{seed}: started {now.strftime('%H:%M')}", flush=True)
    try:
        result = simulate(persona, prompt_text, models.agent_factory(), models.borrower, run_id, sim_time, max_turns)
    except QuotaExhausted:
        raise
    except Exception as e:  # keep going; the failed run is recorded and retried next time
        store.save_run(
            {"run_id": run_id, "suite_id": suite_id, "persona_id": persona["id"], "seed": seed, "status": "error",
             "expected_outcome": json.dumps(persona["expected_outcome"]), "events_json": "[]", "error": repr(e)},
            [], now,
        )
        print(f"  {persona['id']} s{seed}: error {e!r}")
        return

    checks = run_checks(result.events, persona, result.outcome, result.ended_by)
    verdicts: dict[str, str] = {}
    if use_judges:
        for judge in J.judges_for(persona):
            few = few_shot_for(store, judge)
            psha = J.prompt_sha(judge, few)
            cached = store.cached_judge(run_id, judge, psha)
            if cached:
                verdicts[judge] = cached["verdict"]
                continue
            v = J.run_judge(models.judge, judge, result.events, result.verified, few)
            store.save_judge(run_id, judge, v.prompt_sha, v.verdict, v.reasoning, models.judge.name, datetime.now(IST))
            verdicts[judge] = v.verdict

    store.save_run(
        {"run_id": run_id, "suite_id": suite_id, "persona_id": persona["id"], "seed": seed, "status": "ok",
         "ended_by": result.ended_by, "turns": result.borrower_turns, "outcome": result.outcome,
         "expected_outcome": json.dumps(persona["expected_outcome"]),
         "events_json": json.dumps(result.events, ensure_ascii=False), "error": None},
        checks, now,
    )
    write_jsonl(suite_id, run_id, {"persona": persona["id"], "seed": seed, "outcome": result.outcome,
                                   "ended_by": result.ended_by}, result.events)
    passed = persona_passed(persona, {c.name: c.result for c in checks}, verdicts)
    print(f"  {persona['id']} s{seed}: {result.outcome} ({result.ended_by}) {'PASS' if passed else 'FAIL'}")


def summarize(store: LabStore, suite_id: str, personas: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {p["id"]: p for p in personas}
    runs = store.all("SELECT * FROM runs WHERE suite_id = ? AND status = 'ok'", (suite_id,))
    checks = defaultdict(lambda: [0, 0])
    judge_counts = defaultdict(lambda: [0, 0])
    persona_counts = defaultdict(lambda: [0, 0])
    for r in runs:
        c = {x["check_name"]: x["result"] for x in store.all("SELECT * FROM check_results WHERE run_id = ?", (r["run_id"],))}
        v = {x["judge"]: x["verdict"] for x in store.all(
            "SELECT judge, verdict FROM judge_results WHERE run_id = ? ORDER BY created_at", (r["run_id"],))}
        for name, res in c.items():
            if res != "na":
                checks[name][0] += res == "pass"
                checks[name][1] += 1
        for name, res in v.items():
            judge_counts[name][0] += res == "Pass"
            judge_counts[name][1] += 1
        if r["persona_id"] in by_id:
            persona_counts[r["persona_id"]][0] += persona_passed(by_id[r["persona_id"]], c, v)
            persona_counts[r["persona_id"]][1] += 1
    total_pass = sum(p for p, _ in persona_counts.values())
    total = sum(n for _, n in persona_counts.values())
    return {
        "suite_id": suite_id,
        "runs": len(runs),
        "persona_pass_rate": round(total_pass / total, 3) if total else None,
        "checks": {k: {"pass": p, "n": n, "rate": round(p / n, 3)} for k, (p, n) in sorted(checks.items())},
        "judges": {k: {"pass": p, "n": n, "rate": round(p / n, 3)} for k, (p, n) in sorted(judge_counts.items())},
        "personas": {k: {"pass": p, "n": n} for k, (p, n) in sorted(persona_counts.items())},
    }


def print_summary(s: dict[str, Any]) -> None:
    print(f"\nSuite {s['suite_id']}: {s['runs']} runs, persona pass rate {s['persona_pass_rate']}")
    for section in ("checks", "judges"):
        print(f"\n{section.capitalize()}")
        for name, v in s[section].items():
            print(f"  {name:<36} {v['pass']:>3}/{v['n']:<3} {v['rate']:.0%}")
    failing = {k: v for k, v in s["personas"].items() if v["pass"] < v["n"]}
    if failing:
        print("\nPersonas with failures")
        for k, v in failing.items():
            print(f"  {k:<36} {v['pass']}/{v['n']} passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lab.run_suite")
    parser.add_argument("--prompt", required=True, help="Prompt version, for example v1")
    parser.add_argument("--seeds", type=int, default=3, help="Runs per persona (default 3)")
    parser.add_argument("--critical-seeds", type=int, help="Runs per critical persona (default: same as --seeds)")
    parser.add_argument("--personas", help="Comma-separated persona IDs (default all)")
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--no-judges", action="store_true", help="Checks only, no judge calls")
    parser.add_argument("--estimate", action="store_true", help="Print request counts and stop")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args(argv)

    settings = Settings.from_env()  # also loads .env
    personas = load_personas()
    if args.personas:
        wanted = set(args.personas.split(","))
        personas = [p for p in personas if p["id"] in wanted]
        if missing := wanted - {p["id"] for p in personas}:
            print(f"Unknown personas: {sorted(missing)}")
            return 2

    try:
        prompt_text = prompt_texts(args.prompt)
    except FileNotFoundError as e:
        print(e)
        return 2
    prompt_sha = sha(prompt_text["hi"] + prompt_text["en"])
    suite_id = f"{args.prompt}_{prompt_sha}"

    est = estimate(personas, args.seeds, args.critical_seeds, not args.no_judges)
    print_estimate(suite_id, est)
    if args.estimate:
        return 0
    if est["runs"] > 5 and not args.yes:
        if not sys.stdin.isatty() or input("Run it? Free-tier limits may stop it partway; it resumes. [y/N] ").lower() != "y":
            print("Stopped.")
            return 1

    # The lab agent is a different model from the live Gemini agent, so it has its own temperature.
    agent_temperature = float(os.environ.get("LAB_AGENT_TEMPERATURE", "0.3"))
    try:
        models = chat_models(agent_temperature)
    except RuntimeError as e:
        print(e)
        return 2
    store = LabStore(settings.db_path)
    store.save_suite({
        "suite_id": suite_id, "prompt_version": args.prompt, "prompt_sha": prompt_sha,
        "agent_model": models.agent_factory().name, "sim_model": models.borrower.name,
        "judge_model": models.judge.name, "sim_time": DEFAULT_SIM_TIME.isoformat(), "seeds": max(args.seeds, args.critical_seeds or 0),
        "created_at": datetime.now(IST).isoformat(timespec="seconds"),
    })

    try:
        for persona in personas:
            for seed in range(1, seeds_for(persona, args.seeds, args.critical_seeds) + 1):
                if store.run_done(f"{suite_id}:{persona['id']}:s{seed}"):
                    continue
                run_one(store, models, persona, seed, suite_id, prompt_text, DEFAULT_SIM_TIME, args.max_turns, not args.no_judges)
    except QuotaExhausted as e:
        print(f"\nStopped: the API rate limit was hit and did not clear ({e}).")
        print("Run the same command later to continue. Finished runs are kept.")

    summary = summarize(store, suite_id, load_personas())
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{suite_id}.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
