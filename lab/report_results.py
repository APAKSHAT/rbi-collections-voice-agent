"""Write the results that the README, the client review and the resume bullets quote.

    python -m lab.report_results

Every number comes from the SQLite results or from counting project files. Nothing
is typed in by hand. With no suite results yet, the output says so instead of
showing a number.

Writes docs/results.md and refreshes the results block in README.md and docs/05-client-review.md.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from backend.config import IST, ROOT, Settings
from dashboard import data
from dashboard.fmt import label, pct
from lab.checks import CHECK_NAMES
from lab.judges import JUDGES
from lab.simulate import load_personas

RESULTS_MD = ROOT / "docs" / "results.md"
README = ROOT / "README.md"
CLIENT_REVIEW = ROOT / "docs" / "05-client-review.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"


def build_facts() -> dict[str, int]:
    """Counts of what was built, read from the project files."""
    personas = load_personas()
    tests = sum(len(re.findall(r"^\s*def test_", p.read_text(), re.M)) for p in (ROOT / "tests").glob("test_*.py"))
    return {
        "personas": len(personas),
        "critical_personas": sum(1 for p in personas if p.get("critical")),
        "checks": len(CHECK_NAMES),
        "judges": len(JUDGES),
        "test_functions": tests,
    }


def version_table(kpis) -> str:
    lines = ["| Version | Calls | Persona pass | Compliance | Correct outcome | Promise to pay | Hardship flagged | Escalated |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in kpis.itertuples():
        lines.append(f"| {r.version} | {r.calls} | {pct(r.persona_pass)} | {pct(r.compliance)} | {pct(r.outcome_correct)} | "
                     f"{pct(r.promise_to_pay)} | {pct(r.hardship)} | {pct(r.escalation)} |")
    return "\n".join(lines)


def criteria_table(rates, version: str) -> str:
    sub = rates[rates["version"] == version].sort_values(["kind", "rate"])
    lines = ["| Criterion | Type | Passed | Runs | Rate |", "| --- | --- | ---: | ---: | ---: |"]
    for r in sub.itertuples():
        lines.append(f"| {label(r.criterion)} | {r.kind.capitalize()} | {int(r.passed)} | {int(r.n)} | {pct(r.rate)} |")
    return "\n".join(lines)


def judge_table(v) -> str:
    lines = ["| Judge | Split | Labelled | TPR | TNR |", "| --- | --- | ---: | ---: | ---: |"]
    for r in v.sort_values(["split", "judge"]).itertuples():
        tpr = "n/a" if r.tpr != r.tpr else f"{r.tpr:.2f}"  # NaN check
        tnr = "n/a" if r.tnr != r.tnr else f"{r.tnr:.2f}"
        lines.append(f"| {label(r.judge)} | {r.split} | {r.n} | {tpr} | {tnr} |")
    return "\n".join(lines)


def same_persona_comparison(rt):
    """First and latest version, measured only on personas both versions ran, so the change is like for like."""
    versions = list(dict.fromkeys(rt["version"]))
    if len(versions) < 2:
        return None, 0
    first, last = versions[0], versions[-1]
    common = set(rt[rt["version"] == first]["persona_id"]) & set(rt[rt["version"] == last]["persona_id"])
    if not common:
        return None, 0
    sub = rt[rt["version"].isin([first, last]) & rt["persona_id"].isin(common)]
    return data.version_kpis(sub), len(common)


def bullets(facts: dict[str, int], kpis, judge_v, compare=None, n_common: int = 0) -> list[str]:
    out = []
    build = (f"Built a Hindi and English voice collections agent on Retell for a fictional NBFC, with RBI recovery rules "
             f"(calling hours, borrower-only disclosure, call recording) enforced in backend code as well as the prompt.")
    out.append(build)
    lab = (f"Designed a stress test lab with {facts['personas']} simulated borrower personas, {facts['checks']} deterministic "
           f"checks and {facts['judges']} single-purpose LLM judges")
    if not judge_v.empty and "test" in set(judge_v["split"]):
        t = judge_v[judge_v["split"] == "test"]
        good = int(((t["tpr"] >= 0.9) & (t["tnr"] >= 0.9)).sum())
        lab += f"; {good} of {len(t)} judges reached 0.9 TPR and TNR against hand labels on a held-out set"
    out.append(lab + ".")
    if compare is not None and len(compare) == 2:
        first, last = compare.iloc[0], compare.iloc[-1]
        out.append(f"Iterated the agent prompt from {first['version']} to {last['version']} by fixing the root cause of each failure, "
                   f"found in live voice tests and simulation; on the same {n_common} high-risk test personas, compliance pass rate went from "
                   f"{pct(first['compliance'])} to {pct(last['compliance'])} and persona pass rate from "
                   f"{pct(first['persona_pass'])} to {pct(last['persona_pass'])} ({int(first['calls'])} and {int(last['calls'])} simulated calls).")
    elif kpis is not None and len(kpis) == 1:
        k = kpis.iloc[0]
        out.append(f"Measured {k['version']} on {int(k['calls'])} simulated calls: compliance pass rate {pct(k['compliance'])}, "
                   f"persona pass rate {pct(k['persona_pass'])}.")
    else:
        out.append("(Third bullet needs suite results. Run the suite, then run this script again.)")
    return out


def main() -> int:
    store = data.store(Settings.from_env().db_path)
    facts = build_facts()
    rt = data.run_table(store)
    kpis = data.version_kpis(rt) if not rt.empty else None
    compare, n_common = same_persona_comparison(rt) if not rt.empty else (None, 0)
    judge_v = data.judge_validation(store)
    stamp = datetime.now(IST).strftime("%d %b %Y")

    parts = [f"# Results\n\nGenerated by `python -m lab.report_results` on {stamp}. Do not edit by hand.\n"]
    if kpis is None:
        parts.append("No suite results yet. Run `python -m lab.run_suite --prompt v1 --seeds 1 --critical-seeds 3`, then this script.\n")
    else:
        latest = kpis.iloc[-1]["version"]
        suites = data.suites(store)
        models = suites.iloc[-1]
        parts.append(f"Lab models: agent `{models['agent_model']}`, simulated borrower `{models['sim_model']}`, judges `{models['judge_model']}`.\n")
        parts.append("## By prompt version\n\n" + version_table(kpis) + "\n")
        if compare is not None:
            parts.append(f"## Before and after, same {n_common} personas\n\nOnly personas run on both versions, so the change is like for like.\n\n"
                         + version_table(compare) + "\n")
        parts.append(f"## Checks and judges, {latest}\n\n" + criteria_table(data.criterion_rates(rt, data.results_long(store)), latest) + "\n")
    if not judge_v.empty:
        parts.append("## Judge validation\n\nPass is the positive class. TPR: share of human Pass the judge passed. "
                     "TNR: share of human Fail the judge failed.\n\n" + judge_table(judge_v) + "\n")
    parts.append("## What was built\n\n" + "\n".join(f"- {label(k)}: {v}" for k, v in facts.items()) + "\n")
    parts.append("## Resume bullets\n\nOnly numbers from this file. Edit wording, not numbers.\n\n" + "\n".join(f"- {b}" for b in bullets(facts, kpis, judge_v, compare, n_common)) + "\n")
    RESULTS_MD.write_text("\n".join(parts), encoding="utf-8")

    table = version_table(compare if compare is not None else kpis) if kpis is not None else "No suite results yet. They appear here after the first run."
    for path, link in ((README, "docs/results.md"), (CLIENT_REVIEW, "results.md")):
        if path.exists():
            text = path.read_text(encoding="utf-8")
            block = f"{START}\n{table}\n\nFull breakdown: [results.md]({link}).\n{END}"
            text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
            path.write_text(text, encoding="utf-8")
    print(f"Wrote {RESULTS_MD} and refreshed the results blocks")
    print(json.dumps(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
