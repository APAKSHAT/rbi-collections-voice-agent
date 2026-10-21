"""The seven dashboard pages. Each is a plain function that Streamlit's navigation calls."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from itertools import zip_longest
from typing import Any

import pandas as pd
import streamlit as st

from backend.config import IST, ROOT, Settings
from dashboard import charts, data
from dashboard.fmt import count, day, day_time, field_value, label, pct
from lab.judges import JUDGES
from lab.simulate import load_personas

LABEL_TARGET_PER_JUDGE = 10  # about 100 labels across the 10 judges


# Shared helpers


def _db_path() -> str:
    return str(Settings.from_env().db_path)


@st.cache_resource
def _store_at(path: str) -> Any:
    return data.store(path)


def _store() -> Any:
    return _store_at(_db_path())


@st.cache_data(ttl=15)
def _load_at(path: str) -> dict[str, Any]:
    s = _store_at(path)
    rt = data.run_table(s)
    long = data.results_long(s)
    return {"runs": rt, "long": long, "kpis": data.version_kpis(rt)}


def _load() -> dict[str, Any]:
    return _load_at(_db_path())


def _refresh() -> None:
    _load_at.clear()


def empty(message: str, command: str = "") -> None:
    cmd = f"<br><code>{html.escape(command)}</code>" if command else ""
    st.markdown(f'<div class="empty">{html.escape(message)}{cmd}</div>', unsafe_allow_html=True)


def version_picker(versions: list[str], key: str) -> str:
    return st.selectbox("Prompt version", versions, index=len(versions) - 1, key=key)


def render_transcript(events: list[dict[str, Any]]) -> None:
    rows = []
    for e in events:
        if e["role"] == "agent":
            rows.append(f'<div class="turn"><div class="who agent">Meera</div><div>{html.escape(e["text"])}</div></div>')
        elif e["role"] in ("borrower", "user"):
            text = "(silence)" if e.get("silent") else e.get("text") or e.get("content", "")
            cut = "(cuts in) " if e.get("interrupts") else ""
            rows.append(f'<div class="turn"><div class="who">Caller</div><div>{cut}{html.escape(text)}</div></div>')
        elif e["role"] == "tool":
            args = html.escape(json.dumps(e.get("args", {}), ensure_ascii=False))
            res = html.escape(json.dumps(e.get("result", {}), ensure_ascii=False))
            rows.append(f'<div class="turn"><div class="who">Tool</div><div class="tool">{e["name"]} {args}<br>&rarr; {res}</div></div>')
    st.markdown(f'<div class="transcript">{"".join(rows)}</div>', unsafe_allow_html=True)


def render_results(long: pd.DataFrame, run_id: str) -> None:
    sub = long[long["run_id"] == run_id].copy()
    sub["Result"] = sub["passed"].map({True: "Pass", False: "Fail"}).fillna("Not applicable")
    sub = sub.sort_values(["Result", "kind"], key=lambda c: c.map({"Fail": 0, "Pass": 1, "Not applicable": 2}) if c.name == "Result" else c)
    st.dataframe(
        sub.assign(Criterion=sub["criterion"].map(label), Type=sub["kind"].str.capitalize())[["Result", "Criterion", "Type", "detail"]]
        .rename(columns={"detail": "Detail or reasoning"}),
        hide_index=True, width="stretch",
    )


def _selected_row(event: Any) -> int | None:
    rows = event.selection.rows if event is not None else []
    return rows[0] if rows else None


# 1. Overview


def overview() -> None:
    st.title("Overview")
    d = _load()
    rt, kpis = d["runs"], d["kpis"]
    live = data.live_calls(_store())
    if rt.empty:
        empty("No test suites have run yet. Run the suite, then refresh.", "python -m lab.run_suite --prompt v1 --seeds 1 --critical-seeds 3")
        st.metric("Live calls", count(len(live)))
        return

    versions = list(kpis["version"])
    version = version_picker(versions, "ov_version")
    row = kpis[kpis["version"] == version].iloc[0]
    prev = kpis.iloc[versions.index(version) - 1] if versions.index(version) > 0 else None

    def delta(metric: str) -> str | None:
        return None if prev is None else f"{(row[metric] - prev[metric]) * 100:+.0f} pts"

    top, bottom = st.columns(3), st.columns(3)
    top[0].metric("Simulated calls", count(int(row["calls"])))
    top[1].metric("Persona pass rate", pct(row["persona_pass"]), delta("persona_pass"),
                  help=None if prev is None else f"Change since {prev['version']}")
    top[2].metric("Compliance pass rate", pct(row["compliance"]), delta("compliance"),
                  help=None if prev is None else f"Change since {prev['version']}")
    bottom[0].metric("Promise to pay", pct(row["promise_to_pay"]))
    bottom[1].metric("Hardship flagged", pct(row["hardship"]))
    bottom[2].metric("Escalated to a person", pct(row["escalation"]))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("By prompt version")
        st.plotly_chart(
            charts.version_bars(kpis, {"persona_pass": "Persona pass", "compliance": "Compliance", "outcome_correct": "Correct outcome"}),
            width="stretch", config={"displayModeBar": False},
        )
    with right:
        st.subheader("Outcome mix")
        mix = rt[rt["version"] == version]["outcome"].value_counts()
        st.plotly_chart(charts.horizontal_bars([label(o) for o in mix.index], list(mix.values), as_rate=False),
                        width="stretch", config={"displayModeBar": False})

    st.subheader("Versions")
    table = kpis.rename(columns={"version": "Version", "calls": "Calls", "persona_pass": "Persona pass", "compliance": "Compliance",
                                 "outcome_correct": "Correct outcome", "promise_to_pay": "Promise to pay", "hardship": "Hardship", "escalation": "Escalated"})
    rate_cols = ["Persona pass", "Compliance", "Correct outcome", "Promise to pay", "Hardship", "Escalated"]
    st.dataframe(table.assign(**{c: table[c] * 100 for c in rate_cols}), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(c, format="%.0f%%") for c in rate_cols})
    st.markdown(f'<p class="muted">Live calls recorded: {count(len(live))}</p>', unsafe_allow_html=True)


# 2. Compliance


def compliance() -> None:
    st.title("Compliance")
    d = _load()
    rt, long = d["runs"], d["long"]
    if rt.empty:
        empty("No results yet.")
        return
    rates = data.criterion_rates(rt, long)
    versions = list(d["kpis"]["version"])

    wide = rates.pivot_table(index=["kind", "criterion"], columns="version", values="rate").reindex(columns=versions)
    n = rates[rates["version"] == versions[-1]].set_index(["kind", "criterion"])["n"]
    wide = wide.reset_index()
    wide.insert(2, "Runs", wide.set_index(["kind", "criterion"]).index.map(n).fillna(0).astype(int))
    wide["Criterion"] = wide["criterion"].map(label)
    wide["Type"] = wide["kind"].str.capitalize()
    st.dataframe(
        wide[["Criterion", "Type", "Runs"] + versions].assign(**{v: wide[v] * 100 for v in versions}),
        hide_index=True, width="stretch",
        column_config={v: st.column_config.NumberColumn(v, format="%.0f%%") for v in versions},
    )

    st.subheader("Failures")
    c1, c2 = st.columns([1, 2])
    version = c1.selectbox("Prompt version", versions, index=len(versions) - 1, key="cmp_version")
    failing = rates[(rates["version"] == version) & (rates["rate"] < 1)].sort_values("rate")
    if failing.empty:
        empty(f"No failures in {version}.")
        return
    crit = c2.selectbox("Criterion", list(failing["criterion"]), format_func=lambda c: f"{label(c)} ({pct(float(failing.set_index('criterion').loc[c, 'rate']))})")
    rows = long[(long["criterion"] == crit) & (long["passed"] == False)].merge(  # noqa: E712
        rt[rt["version"] == version][["run_id", "persona_id", "seed", "outcome"]], on="run_id"
    )
    event = st.dataframe(
        rows.assign(Persona=rows["persona_id"], Seed=rows["seed"], Outcome=rows["outcome"].map(label))[["Persona", "Seed", "Outcome", "detail"]]
        .rename(columns={"detail": "Detail or reasoning"}),
        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="cmp_fail",
    )
    i = _selected_row(event)
    if i is not None:
        run_id = rows.iloc[i]["run_id"]
        render_transcript(json.loads(rt.set_index("run_id").loc[run_id, "events_json"]))


# 3. Personas


def personas() -> None:
    st.title("Personas")
    d = _load()
    rt, long = d["runs"], d["long"]
    if rt.empty:
        empty("No results yet.")
        return
    versions = list(d["kpis"]["version"])
    version = version_picker(versions, "ps_version")
    m = data.persona_matrix(rt, long, version)
    if m.empty:
        empty(f"No results for {version}.")
        return
    order = [c for c in data.COMPLIANCE_CHECKS + data.COMPLIANCE_JUDGES if c in m.columns] + sorted(
        c for c in m.columns if c not in data.COMPLIANCE_CHECKS + data.COMPLIANCE_JUDGES)
    m = m[order]
    st.plotly_chart(charts.heatmap(1 - m, [label(c) for c in m.columns], list(m.index)), width="stretch", config={"displayModeBar": False})
    st.markdown('<p class="muted">Darker means more failures. Blank: the criterion does not apply to that persona.</p>', unsafe_allow_html=True)

    sub = rt[rt["version"] == version]
    by = sub.groupby("persona_id").agg(Runs=("run_id", "size"), Passed=("persona_passed", "sum")).reset_index()
    meta = {p["id"]: p for p in load_personas()}
    by["Language"] = by["persona_id"].map(lambda p: meta.get(p, {}).get("language", ""))
    by["Critical"] = by["persona_id"].map(lambda p: "Yes" if meta.get(p, {}).get("critical") else "")
    by["Pass rate"] = by["Passed"] / by["Runs"] * 100
    st.dataframe(by.rename(columns={"persona_id": "Persona"}).sort_values("Pass rate"), hide_index=True, width="stretch",
                 column_config={"Pass rate": st.column_config.NumberColumn(format="%.0f%%")})


# 4. Transcripts


def transcripts() -> None:
    st.title("Transcripts")
    d = _load()
    rt, long = d["runs"], d["long"]
    if rt.empty:
        empty("No transcripts yet.")
        return
    versions = list(d["kpis"]["version"])
    f = st.columns([1, 1.4, 1.4, 1, 2])
    version = f[0].selectbox("Version", versions, index=len(versions) - 1, key="tr_version")
    sub = rt[rt["version"] == version]
    persona = f[1].selectbox("Persona", ["All"] + sorted(sub["persona_id"].unique()))
    outcome = f[2].selectbox("Outcome", ["All"] + sorted(sub["outcome"].unique()), format_func=lambda o: o if o == "All" else label(o))
    result = f[3].selectbox("Result", ["All", "Passed", "Failed"])
    search = f[4].text_input("Search text")
    if persona != "All":
        sub = sub[sub["persona_id"] == persona]
    if outcome != "All":
        sub = sub[sub["outcome"] == outcome]
    if result != "All":
        sub = sub[sub["persona_passed"] == (result == "Passed")]
    if search:
        sub = sub[sub["events_json"].str.contains(re.escape(search), case=False)]

    st.markdown(f'<p class="muted">{count(len(sub))} calls</p>', unsafe_allow_html=True)
    view = sub.assign(Persona=sub["persona_id"], Seed=sub["seed"], Outcome=sub["outcome"].map(label),
                      Result=sub["persona_passed"].map({True: "Pass", False: "Fail"}), Turns=sub["turns"],
                      Ended=sub["ended_by"].map(label))
    event = st.dataframe(view[["Persona", "Seed", "Outcome", "Result", "Turns", "Ended"]], hide_index=True, width="stretch",
                         on_select="rerun", selection_mode="single-row", key="tr_table", height=260)
    i = _selected_row(event)
    if i is None:
        return
    run = sub.iloc[i]
    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"{run['persona_id']}, seed {run['seed']}")
        render_transcript(json.loads(run["events_json"]))
    with right:
        st.subheader("Checks and verdicts")
        render_results(long, run["run_id"])


# 5. Labeling


def _rule_text(judge: str) -> str:
    text = (ROOT / "prompts" / "judges" / f"{judge}.md").read_text(encoding="utf-8")
    keep = text.split("## Example of a Pass")[0]
    return re.sub(r"^# Judge: .*\n", "", keep).strip()


def labeling() -> None:
    st.title("Labeling")
    s = _store()
    d = _load()
    rt = d["runs"]
    labels = data.labels(s)
    done = labels.groupby("judge").size() if not labels.empty else pd.Series(dtype=int)
    st.markdown(f'<p class="muted">Labels so far: {count(len(labels))}. Aim for about {LABEL_TARGET_PER_JUDGE} per judge, with both Pass and Fail.</p>',
                unsafe_allow_html=True)
    if rt.empty:
        empty("Nothing to label yet. Run the suite first.")
        return

    judge = st.selectbox("Judge", JUDGES, format_func=lambda j: f"{label(j)} ({int(done.get(j, 0))} labelled)")
    verdicts = data.verdicts(s)
    pool = verdicts[verdicts["criterion"] == judge].merge(rt[["run_id", "persona_id", "events_json"]], on="run_id")
    labelled = set(labels[labels["judge"] == judge]["run_id"]) if not labels.empty else set()
    pool = pool[~pool["run_id"].isin(labelled)]
    if pool.empty:
        empty("No unlabelled calls for this judge.")
        return
    # Alternate the judge's Fail and Pass verdicts so the labelled set holds both. The verdict stays hidden until you label.
    fails = list(pool.index[pool["verdict"] == "Fail"])
    passes = list(pool.index[pool["verdict"] == "Pass"])
    order = [i for pair in zip_longest(fails, passes) for i in pair if i is not None]
    item = pool.loc[order[0]]

    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"{item['persona_id']}")
        render_transcript(json.loads(item["events_json"]))
    with right:
        with st.container(border=True):
            st.markdown(_rule_text(judge))
        with st.form("label_form", clear_on_submit=True):
            choice = st.radio("Your verdict", ["Pass", "Fail"], horizontal=True, index=None)
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Save label", type="primary"):
                if choice is None:
                    st.warning("Pick Pass or Fail.")
                else:
                    data.save_label(s, item["run_id"], judge, choice, note, datetime.now(IST).isoformat(timespec="seconds"))
                    st.session_state["last_label"] = (judge, item["run_id"], choice, item["verdict"], item["reasoning"])
                    _refresh()
                    st.rerun()
        last = st.session_state.get("last_label")
        if last and last[0] == judge:
            agree = "agreed" if last[2] == last[3] else "disagreed"
            st.markdown(f'<p class="muted">Last call: you said {last[2]}, the judge said {last[3]} ({agree}).<br>{html.escape(last[4])}</p>',
                        unsafe_allow_html=True)


# 6. Judge quality


def judge_quality() -> None:
    st.title("Judge quality")
    s = _store()
    v = data.judge_validation(s)
    labels = data.labels(s)
    st.markdown(f'<p class="muted">Pass is the positive class. TPR: share of human Pass the judge also passed. '
                f'TNR: share of human Fail the judge also failed. Target 0.9 for both. Labels: {count(len(labels))}.</p>',
                unsafe_allow_html=True)
    if v.empty:
        empty("No validation run yet. Label calls first, then run:", "python -m lab.validate_judges --split dev")
        return
    split = st.segmented_control("Split", sorted(v["split"].unique()), default="dev" if "dev" in set(v["split"]) else None)
    sub = v[v["split"] == (split or v["split"].iloc[0])].copy()
    sub["Status"] = sub.apply(lambda r: "Meets target" if (r["tpr"] or 0) >= 0.9 and (r["tnr"] or 0) >= 0.9 else "Below target", axis=1)
    st.dataframe(
        sub.assign(Judge=sub["judge"].map(label), TPR=sub["tpr"], TNR=sub["tnr"])[["Judge", "n", "TPR", "TNR", "tp", "fn", "tn", "fp", "Status", "prompt_sha"]]
        .rename(columns={"n": "Labelled", "tp": "TP", "fn": "FN", "tn": "TN", "fp": "FP", "prompt_sha": "Prompt"}),
        hide_index=True, width="stretch",
        column_config={"TPR": st.column_config.NumberColumn(format="%.2f"), "TNR": st.column_config.NumberColumn(format="%.2f")},
    )
    st.subheader("Disagreements")
    rows = data.disagreements(split or "dev")
    if not rows:
        empty("No disagreements in the latest report.")
        return
    for r in rows:
        with st.expander(f"{label(r['judge'])}: you said {r['human']}, judge said {r['judge']} ({r['run_id']})"):
            st.markdown(f"**Judge reasoning.** {r['judge_reasoning']}")
            if r.get("human_note"):
                st.markdown(f"**Your note.** {r['human_note']}")


# 7. Live calls


def live_calls() -> None:
    st.title("Live calls")
    s = _store()
    calls = data.live_calls(s)
    if calls.empty:
        empty("No live calls yet. Place a test call, then refresh.", "python -m backend.cli call KF-TW-000184")
        return

    def duration(r: pd.Series) -> str:
        if not r.get("started_at") or not r.get("ended_at"):
            return ""
        secs = (datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds()
        return f"{int(secs // 60)}m {int(secs % 60):02d}s"

    view = pd.DataFrame({
        "Started": calls["started_at"].map(day_time),
        "Borrower": calls["borrower_id"],
        "Type": calls["call_type"].map(lambda t: label(t or "")),
        "Duration": calls.apply(duration, axis=1),
        "Outcome": calls.get("pc_outcome", pd.Series([""] * len(calls))).fillna("").map(lambda o: label(o) if o else ""),
        "Recording notice": calls["recording_notice_given"].map({1: "Yes", 0: "No"}).fillna(""),
        "Sentiment": calls["user_sentiment"].fillna(""),
    })
    event = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="live_table")
    i = _selected_row(event)
    if i is None:
        return
    call = calls.iloc[i]
    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"Call on {day(call['started_at'])}")
        transcript = json.loads(call["transcript_json"]) if call.get("transcript_json") else []
        render_transcript([{"role": "agent" if t.get("role") == "agent" else "user", "text": t.get("content", "")} for t in transcript])
        if call.get("recording_url"):
            st.markdown(f"[Recording]({call['recording_url']})")
    with right:
        st.subheader("Post-call fields")
        fields = {k[3:]: v for k, v in call.items() if k.startswith("pc_") and pd.notna(v)}
        st.dataframe(pd.DataFrame({"Field": [label(k) for k in fields], "Value": [field_value(k, v) for k, v in fields.items()]}),
                     hide_index=True, width="stretch")
        if call.get("call_summary"):
            st.markdown(f'<p class="muted">{html.escape(call["call_summary"])}</p>', unsafe_allow_html=True)
        st.subheader("Tool calls")
        events = data.tool_events(s, call["call_id"])
        if events.empty:
            st.markdown('<p class="muted">None</p>', unsafe_allow_html=True)
        else:
            st.dataframe(events.assign(Tool=events["tool_name"], Time=events["created_at"].map(day_time))[["Time", "Tool", "args_json", "result_json"]]
                         .rename(columns={"args_json": "Arguments", "result_json": "Result"}), hide_index=True, width="stretch")
