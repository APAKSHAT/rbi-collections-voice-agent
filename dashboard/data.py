"""Read-side queries for the dashboard. Everything returns pandas DataFrames built from the one SQLite file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import ROOT
from lab.run_suite import persona_passed
from lab.simulate import load_personas
from lab.store import LabStore

# Rules a regulator would ask about. A call is compliant only if every one that applies passed.
COMPLIANCE_CHECKS = [
    "recording_notice_first_turn",
    "no_disclosure_before_verification",
    "no_sensitive_data_request",
    "callback_in_window",
]
COMPLIANCE_JUDGES = [
    "third_party_disclosure",
    "coercive_or_threatening_language",
    "misleading_consequences",
    "hardship_handled_correctly",
    "distress_handled_correctly",
]

REPORTS = ROOT / "lab" / "reports"


def store(path: Path | str) -> LabStore:
    return LabStore(path)


def suites(s: LabStore) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM suites ORDER BY created_at", s.conn)


def runs(s: LabStore) -> pd.DataFrame:
    """Finished runs with their prompt version."""
    return pd.read_sql_query(
        "SELECT r.*, su.prompt_version AS version FROM runs r JOIN suites su ON su.suite_id = r.suite_id "
        "WHERE r.status = 'ok' ORDER BY su.created_at, r.persona_id, r.seed",
        s.conn,
    )


def checks(s: LabStore) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT c.run_id, c.check_name AS criterion, c.result, c.detail FROM check_results c "
        "JOIN runs r ON r.run_id = c.run_id WHERE r.status = 'ok'",
        s.conn,
    )


def verdicts(s: LabStore) -> pd.DataFrame:
    """Latest verdict per run and judge (a judge prompt change adds rows; the newest wins)."""
    df = pd.read_sql_query(
        "SELECT j.run_id, j.judge AS criterion, j.verdict, j.reasoning, j.created_at FROM judge_results j "
        "JOIN runs r ON r.run_id = j.run_id WHERE r.status = 'ok' ORDER BY j.created_at",
        s.conn,
    )
    return df.groupby(["run_id", "criterion"], as_index=False).last()


def results_long(s: LabStore) -> pd.DataFrame:
    """One row per run and criterion: kind (check or judge), passed (True/False, None for not applicable)."""
    c = checks(s)
    c["kind"] = "check"
    c["passed"] = c["result"].map({"pass": True, "fail": False, "na": None})
    v = verdicts(s)
    v["kind"] = "judge"
    v["passed"] = v["verdict"] == "Pass"
    v = v.rename(columns={"reasoning": "detail"})
    cols = ["run_id", "criterion", "kind", "passed", "detail"]
    return pd.concat([c[cols], v[cols]], ignore_index=True)


def run_table(s: LabStore) -> pd.DataFrame:
    """Runs with derived flags: persona passed, compliant."""
    r = runs(s)
    if r.empty:
        return r.assign(persona_passed=pd.Series(dtype=bool), compliant=pd.Series(dtype=bool))
    personas = {p["id"]: p for p in load_personas()}
    long = results_long(s)
    by_run: dict[str, dict[str, Any]] = {}
    for run_id, g in long.groupby("run_id"):
        chk = {row.criterion: ("na" if row.passed is None else ("pass" if row.passed else "fail")) for row in g.itertuples() if row.kind == "check"}
        jud = {row.criterion: ("Pass" if row.passed else "Fail") for row in g.itertuples() if row.kind == "judge"}
        compliant = all(chk.get(c) != "fail" for c in COMPLIANCE_CHECKS) and all(jud.get(j) != "Fail" for j in COMPLIANCE_JUDGES)
        by_run[run_id] = {"chk": chk, "jud": jud, "compliant": compliant}
    r["persona_passed"] = [
        persona_passed(personas[row.persona_id], by_run.get(row.run_id, {}).get("chk", {}), by_run.get(row.run_id, {}).get("jud", {}))
        if row.persona_id in personas else False
        for row in r.itertuples()
    ]
    r["compliant"] = [by_run.get(rid, {}).get("compliant", True) for rid in r["run_id"]]
    return r


def version_kpis(rt: pd.DataFrame) -> pd.DataFrame:
    """Headline rates per prompt version."""
    if rt.empty:
        return pd.DataFrame()
    rows = []
    for version, g in rt.groupby("version", sort=False):
        n = len(g)
        rows.append({
            "version": version,
            "calls": n,
            "persona_pass": g["persona_passed"].mean(),
            "compliance": g["compliant"].mean(),
            "promise_to_pay": (g["outcome"] == "promise_to_pay").mean(),
            "hardship": (g["outcome"] == "hardship_escalated").mean(),
            "escalation": (g["outcome"] == "escalated_to_human").mean(),
            "outcome_correct": g.apply(lambda x: x["outcome"] in json.loads(x["expected_outcome"]), axis=1).mean(),
        })
    return pd.DataFrame(rows)


def criterion_rates(rt: pd.DataFrame, long: pd.DataFrame) -> pd.DataFrame:
    """Pass rate per criterion and version, ignoring not-applicable results."""
    if rt.empty or long.empty:
        return pd.DataFrame(columns=["version", "criterion", "kind", "passed", "n", "rate"])
    df = long.dropna(subset=["passed"]).merge(rt[["run_id", "version"]], on="run_id")
    df["passed"] = df["passed"].astype(bool)
    out = df.groupby(["version", "criterion", "kind"], as_index=False, sort=False).agg(passed=("passed", "sum"), n=("passed", "size"))
    out["rate"] = out["passed"] / out["n"]
    return out


def persona_matrix(rt: pd.DataFrame, long: pd.DataFrame, version: str) -> pd.DataFrame:
    """Persona x criterion pass rate for one version. NaN where the criterion does not apply."""
    sub = rt[rt["version"] == version][["run_id", "persona_id"]]
    df = long.dropna(subset=["passed"]).merge(sub, on="run_id")
    if df.empty:
        return pd.DataFrame()
    df["passed"] = df["passed"].astype(float)
    return df.pivot_table(index="persona_id", columns="criterion", values="passed", aggfunc="mean")


def labels(s: LabStore) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM labels", s.conn)


def save_label(s: LabStore, run_id: str, judge: str, label: str, note: str, when: str) -> None:
    s.conn.execute("INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?, ?)", (run_id, judge, label, note, when))
    s.conn.commit()


def judge_validation(s: LabStore) -> pd.DataFrame:
    """Latest validation result per judge and split."""
    df = pd.read_sql_query("SELECT * FROM judge_validation ORDER BY created_at, id", s.conn)
    if df.empty:
        return df
    df = df.groupby(["judge", "split"], as_index=False).last()
    df["tpr"] = df["tp"] / (df["tp"] + df["fn"]).where(lambda x: x > 0)
    df["tnr"] = df["tn"] / (df["tn"] + df["fp"]).where(lambda x: x > 0)
    return df


def disagreements(split: str) -> list[dict[str, Any]]:
    path = REPORTS / f"judge_validation_{split}.json"
    if not path.exists():
        return []
    report = json.loads(path.read_text())
    return [{"judge": j, **d} for j, m in report["judges"].items() for d in m["disagreements"]]


def live_calls(s: LabStore) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM calls ORDER BY started_at DESC", s.conn)
    if df.empty:
        return df
    fields = df["analysis_json"].map(lambda x: (json.loads(x).get("custom_analysis_data") or {}) if x else {})
    return pd.concat([df, pd.json_normalize(fields.tolist()).add_prefix("pc_")], axis=1)


def tool_events(s: LabStore, call_id: str) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM tool_events WHERE call_id = ? ORDER BY id", s.conn, params=(call_id,))
