"""Plotly figures in one plain style: one blue accent, neutral greys, no 3D, no gradients.

Version comparisons draw the latest version in the accent and earlier ones in grey,
with value labels on every bar so colour is never the only cue.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

ACCENT = "#2a78d6"
PREVIOUS = "#8a8983"  # passes 3:1 contrast and colour-blind separation against ACCENT
TEXT = "#1f2328"
TEXT_MUTED = "#52514e"
GRID = "#e6e5e1"
FAIL = "#d03b3b"
FONT = "IBM Plex Sans, Inter, system-ui, sans-serif"

# Single-hue sequential ramp (blue), light to dark. Near zero recedes toward the surface.
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281"]


def _style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=TEXT),
        hoverlabel=dict(font_family=FONT, bgcolor="white", bordercolor=GRID),
        bargap=0.35,
        bargroupgap=0.08,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, font=dict(color=TEXT_MUTED)),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=TEXT_MUTED), title=None)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=TEXT_MUTED), title=None)
    return fig


def version_bars(kpis: pd.DataFrame, metrics: dict[str, str]) -> go.Figure:
    """Grouped bars: one group per metric, one bar per prompt version. Rates 0 to 1."""
    fig = go.Figure()
    versions = list(kpis["version"])
    for i, row in kpis.reset_index(drop=True).iterrows():
        latest = i == len(versions) - 1
        values = [row[m] for m in metrics]
        fig.add_bar(
            name=row["version"],
            x=list(metrics.values()),
            y=values,
            marker=dict(color=ACCENT if latest else PREVIOUS, cornerradius=4),
            text=[f"{v:.0%}" for v in values],
            textposition="outside",
            textfont=dict(color=TEXT_MUTED, size=12),
            hovertemplate=f"{row['version']}<br>%{{x}}: %{{y:.1%}}<extra></extra>",
        )
    fig.update_yaxes(range=[0, 1.12], tickformat=".0%")
    fig.update_layout(showlegend=len(versions) > 1, barmode="group")
    return _style(fig, 300)


def horizontal_bars(labels: list[str], values: list[float], as_rate: bool, height: int | None = None) -> go.Figure:
    """Single-series horizontal bars, largest at the top."""
    text = [f"{v:.0%}" if as_rate else f"{v:,.0f}" for v in values]
    fig = go.Figure(
        go.Bar(
            x=values, y=labels, orientation="h",
            marker=dict(color=ACCENT, cornerradius=4),
            text=text, textposition="outside", textfont=dict(color=TEXT_MUTED, size=12),
            hovertemplate="%{y}: %{text}<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showticklabels=False, range=[0, (1.15 if as_rate else max(values or [1]) * 1.2)])
    return _style(fig, height or max(160, 34 * len(labels) + 30))


def heatmap(fail_rate: pd.DataFrame, x_labels: list[str], y_labels: list[str]) -> go.Figure:
    """Fail rate 0 to 1 per persona and criterion. Passing cells recede; only failures carry a label.
    Blank where the criterion does not apply."""
    z = fail_rate.values
    text = [["" if pd.isna(v) or v == 0 else f"{v:.0%}" for v in row] for row in z]
    hover = [["not applicable" if pd.isna(v) else f"fails {v:.0%}" for v in row] for row in z]
    fig = go.Figure(
        go.Heatmap(
            z=z, x=x_labels, y=y_labels, zmin=0, zmax=1, xgap=2, ygap=2,
            colorscale=[[i / (len(BLUES) - 1), c] for i, c in enumerate(BLUES)],
            text=text, texttemplate="%{text}", textfont=dict(size=11),
            customdata=hover, hovertemplate="%{y}<br>%{x}: %{customdata}<extra></extra>",
            colorbar=dict(thickness=10, tickformat=".0%", outlinewidth=0, len=0.6, title=dict(text="Fail rate", side="top")),
        )
    )
    fig.update_xaxes(side="top", tickangle=-40, showgrid=False, tickfont=dict(size=11))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=11))
    return _style(fig, max(360, 22 * len(y_labels) + 190))
