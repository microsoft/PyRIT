# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.3
# ---
# %% [markdown]
# # Benchmark Leaderboard
#
# Attack success rate (ASR) for adversarial techniques and models, built from `AdversarialBenchmark`
# scenario runs. See [Benchmark Scenarios](../scanner/benchmark.ipynb) for how to run the scenario
# yourself and `build_scripts/export_adversarial_benchmark_result.py` for how a run's results land
# in the committed store this page reads.

# %% [markdown]
# ## Technique / Adversarial Model Leaderboard
#
# Ranked by success rate — the share of objectives where the target's response was scored as
# achieving the objective — descending. `N` is the number of objectives attempted for that
# (technique, adversarial model, objective target, objective scorer, dataset) combination; treat
# rows with a small `N` as directional, not statistically robust.

# %%
import html

import pandas as pd
from IPython.display import HTML, display

from pyrit.common.path import BENCHMARK_RESULTS_PATH

# Static (non-interactive) dark leaderboard-card styling. MyST renders notebook HTML output via
# React's `dangerouslySetInnerHTML`, and browsers never execute <script> tags inserted that way,
# so this card is pre-sorted/pre-formatted here rather than offering the live search/sort a
# browser-side script would normally provide.
_CARD_STYLE = """<style>
.pyrit-leaderboard-card {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  background: #0d1117;
  color: #e6edf3;
  border: 1px solid #30363d;
  border-radius: 6px;
  max-width: 900px;
  overflow: hidden;
}
.pyrit-leaderboard-card .lb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 16px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
}
.pyrit-leaderboard-card .lb-head h4 {
  margin: 0;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #8b949e;
  font-weight: 600;
}
.pyrit-leaderboard-card .lb-meta { font-size: 11px; color: #8b949e; white-space: nowrap; }
.pyrit-leaderboard-card table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.pyrit-leaderboard-card th {
  text-align: left;
  padding: 8px 14px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #3fb950;
  border-bottom: 1px solid #30363d;
  white-space: nowrap;
}
.pyrit-leaderboard-card th.lb-rank { text-align: center; }
.pyrit-leaderboard-card td { padding: 8px 14px; border-bottom: 1px solid #21262d; }
.pyrit-leaderboard-card td.lb-rank { color: #6e7681; text-align: center; }
.pyrit-leaderboard-card .lb-note {
  font-size: 11px;
  color: #8b949e;
  padding: 8px 16px;
  border-top: 1px solid #30363d;
}
</style>
"""


def _format_cell(value: object, *, as_percent: bool) -> str:
    """Format a single leaderboard cell value as display text, escaping any string content."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if as_percent:
            return f"{float(value):.0%}"
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    return html.escape(str(value))


def render_leaderboard_card(
    df: pd.DataFrame, *, title: str, note: str, percent_columns: frozenset[str] = frozenset()
) -> HTML:
    """Render a pre-sorted DataFrame as a static dark leaderboard card (HTML/CSS only, no JS)."""
    header_cells = "".join(f"<th>{html.escape(str(column))}</th>" for column in df.columns)
    body_rows = "".join(
        "<tr><td class='lb-rank'>{rank}</td>{cells}</tr>".format(
            rank=rank,
            cells="".join(
                f"<td>{_format_cell(value, as_percent=column in percent_columns)}</td>" for column, value in row.items()
            ),
        )
        for rank, (_, row) in enumerate(df.iterrows(), start=1)
    )
    row_count = len(df)
    meta = f"{row_count} row{'s' if row_count != 1 else ''}"
    card = (
        f'{_CARD_STYLE}<div class="pyrit-leaderboard-card">'
        f'<div class="lb-head"><h4>{html.escape(title)}</h4><span class="lb-meta">{meta}</span></div>'
        f'<table><thead><tr><th class="lb-rank">#</th>{header_cells}</tr></thead>'
        f"<tbody>{body_rows}</tbody></table>"
        f'<div class="lb-note">{html.escape(note)}</div></div>'
    )
    return HTML(card)


_STORE_PATH = BENCHMARK_RESULTS_PATH / "adversarial_benchmark_metrics.jsonl"

_COLUMN_LABELS = {
    "technique": "Technique",
    "adversarial_model": "Adversarial Model",
    "objective_target": "Objective Target",
    "objective_scorer": "Objective Scorer",
    "dataset": "Dataset",
    "total": "N",
    "success": "Success",
    "failure": "Failure",
    "error": "Error",
    "undetermined": "Undetermined",
    "retry_records": "Retries",
    "success_rate": "Success Rate",
}

if _STORE_PATH.exists():
    benchmark_df = pd.read_json(_STORE_PATH, lines=True)
    benchmark_df = benchmark_df.sort_values("success_rate", ascending=False)
    display_df = benchmark_df.rename(columns=_COLUMN_LABELS)[list(_COLUMN_LABELS.values())]
    # A bare trailing expression only auto-displays when it's the *entire* cell's last top-level
    # statement; nested inside this if/else it would otherwise be silently discarded.
    display(
        render_leaderboard_card(
            display_df,
            title="Technique / Adversarial Model Leaderboard",
            note="Ranked by success rate. N is the number of objectives attempted for that combination; treat rows "
            "with a small N as directional, not statistically robust.",
            percent_columns=frozenset({"Success Rate"}),
        )
    )
else:
    print(f"No benchmark data yet at {_STORE_PATH}.")

# %% [markdown]
# ## Note on scope
#
# This table is a snapshot upserted by `--update-benchmark-store`, keyed on
# `(technique, adversarial_model, objective_target, objective_scorer, dataset)` — a fresh run of
# the same combination replaces its row rather than appending, so this stays a single
# best-known-result table rather than an unbounded history. Small `N` values (as in the rows
# above) reflect small demo-scale runs (`--max-dataset-size`), not a statistically robust
# evaluation; treat them as a preview of the mechanism rather than a final verdict on any
# technique or model. Widening this into a larger, regularly-refreshed sweep — and adding a
# native objective-target comparison alongside the current adversarial-model comparison — is
# future work.
