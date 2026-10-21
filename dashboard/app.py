"""Kaveri Finance collections QA dashboard.

Run: streamlit run dashboard/app.py
Reads the SQLite file at DB_PATH (default data/collections.db).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import pages  # noqa: E402

st.set_page_config(page_title="Collections QA", layout="wide")
st.markdown(f"<style>{(Path(__file__).parent / 'theme.css').read_text()}</style>", unsafe_allow_html=True)

nav = st.navigation(
    {
        "Results": [
            st.Page(pages.overview, title="Overview", url_path="overview", default=True),
            st.Page(pages.compliance, title="Compliance", url_path="compliance"),
            st.Page(pages.personas, title="Personas", url_path="personas"),
            st.Page(pages.transcripts, title="Transcripts", url_path="transcripts"),
        ],
        "Judges": [
            st.Page(pages.labeling, title="Labeling", url_path="labeling"),
            st.Page(pages.judge_quality, title="Judge quality", url_path="judge-quality"),
        ],
        "Production": [
            st.Page(pages.live_calls, title="Live calls", url_path="live-calls"),
        ],
    }
)
st.sidebar.markdown('<p class="muted">Kaveri Finance<br>Early-stage collections agent</p>', unsafe_allow_html=True)
nav.run()
