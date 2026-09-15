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
# # Scorer Quality
#
# Leaderboards for the scorer evaluation metrics PyRIT already tracks under
# `pyrit/datasets/scorer_evals/`. See [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb) for
# what each metric means and how these numbers are produced.

# %% [markdown]
# ## Objective Scorer Leaderboard
#
# Objective scorers answer a true/false question (e.g. "was the objective achieved?"). Ranked by
# F1 score, the harmonic mean of precision and recall.

# %%
import html

import pandas as pd
from IPython.display import HTML

from pyrit.score import get_all_objective_metrics
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)  # type: ignore

# Static (non-interactive) dark leaderboard-card styling shared by every table on this page.
# MyST renders notebook HTML output via React's `dangerouslySetInnerHTML`, and browsers never
# execute <script> tags inserted that way, so cards are pre-sorted/pre-formatted here rather than
# offering the live search/sort a browser-side script would normally provide.
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


objective_metrics = get_all_objective_metrics()
objective_metrics.sort(key=lambda entry: entry.metrics.f1_score, reverse=True)

objective_rows = [
    {
        "Name": entry.scorer_identifier.unique_name,
        "Accuracy": entry.metrics.accuracy,
        "F1 Score": entry.metrics.f1_score,
        "Precision": entry.metrics.precision,
        "Recall": entry.metrics.recall,
        "Samples": entry.metrics.num_responses,
    }
    for entry in objective_metrics
]

objective_df = pd.DataFrame(objective_rows)
render_leaderboard_card(
    objective_df,
    title="Objective Scorer Leaderboard",
    note="All metrics computed against human-labeled ground truth. Ranked by F1 (higher is better across all "
    "four metrics).",
    percent_columns=frozenset({"Accuracy", "F1 Score", "Precision", "Recall"}),
)

# %% [markdown]
# ## Harm Scorer Leaderboard
#
# Harm scorers produce a severity score (0.0-1.0). Ranked by `krippendorff_alpha_combined` —
# agreement between the model's scores and human raters, ranging from -1.0 (systematic
# disagreement) to 1.0 (perfect agreement) — across every harm category PyRIT currently has
# metrics for. Alpha isn't comparable *across* categories (each has its own human-labeled
# dataset), so treat this as one leaderboard per category, stacked into a single table for
# convenience.

# %%
from pyrit.common.path import SCORER_EVALS_HARM_PATH
from pyrit.score import get_all_harm_metrics

# Harm categories are discovered from the files present on disk rather than a hardcoded list,
# so a newly added category shows up here without a code change.
harm_categories = sorted(
    path.name.removesuffix("_metrics.jsonl") for path in SCORER_EVALS_HARM_PATH.glob("*_metrics.jsonl")
)

harm_metrics = [
    (harm_category, entry)
    for harm_category in harm_categories
    for entry in get_all_harm_metrics(harm_category=harm_category)
]
harm_metrics.sort(key=lambda item: item[1].metrics.krippendorff_alpha_combined, reverse=True)

harm_rows = [
    {
        "Name": entry.scorer_identifier.unique_name,
        "Harm Category": harm_category,
        "MAE": entry.metrics.mean_absolute_error,
        "Alpha Combined": entry.metrics.krippendorff_alpha_combined,
        "Alpha Humans": entry.metrics.krippendorff_alpha_humans,
        "Alpha Model": entry.metrics.krippendorff_alpha_model,
        "Samples": entry.metrics.num_responses,
    }
    for harm_category, entry in harm_metrics
]

harm_df = pd.DataFrame(harm_rows)
render_leaderboard_card(
    harm_df,
    title="Harm Scorer Leaderboard",
    note="MAE: lower is better (0-1 scale). Alpha = Krippendorff's alpha: higher is better (agreement between "
    "scorer and ground truth). Not comparable across harm categories.",
)

# %% [markdown]
# ## Note on scope
#
# `get_all_objective_metrics()` reads `objective/objective_achieved_metrics.jsonl` only, matching
# how it's documented and used elsewhere in PyRIT. A separate `refusal_scorer/refusal_metrics.jsonl`
# registry evaluates refusal scorers against its own human-labeled dataset, using the same
# `ObjectiveScorerMetrics` shape. It isn't merged into the leaderboard above because it measures a
# different task (refusal detection, not objective achievement) against a different ground truth
# set, and mixing the two would make the F1 ranking misleading. A follow-up could add it as its own
# leaderboard.
