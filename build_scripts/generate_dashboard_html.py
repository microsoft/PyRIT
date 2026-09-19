# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Generate the static HTML leaderboard fragments used by the metrics dashboard docs pages.

The dashboard pages under `doc/dashboard/` are plain MyST Markdown files that `{include}`
pre-rendered HTML fragments rather than notebooks, so no Python source is shown to readers.
This script renders those fragments from the committed scorer/benchmark evaluation data and
writes them to `doc/dashboard/_generated/`. Re-run it and commit the output whenever the
underlying `pyrit/datasets/scorer_evals/` or `pyrit/datasets/benchmark_results/` data changes.

Usage:
    python -m build_scripts.generate_dashboard_html
"""

import asyncio
import html
import sys
from typing import TYPE_CHECKING

from pyrit.common.path import DOCS_PATH

if TYPE_CHECKING:
    import pandas as pd

_OUTPUT_DIR = DOCS_PATH / "dashboard" / "_generated"

# Static (non-interactive) dark leaderboard-card styling shared by every card this script
# renders. MyST includes each fragment's raw HTML directly into the page DOM (no <script>
# execution model to rely on), so cards are pre-sorted/pre-formatted here rather than offering
# the live search/sort a browser-side script would normally provide.
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
    if isinstance(value, float) and value != value:  # NaN: e.g. a pivoted table's missing cell
        return "—"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if as_percent:
            return f"{float(value):.0%}"
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    return html.escape(str(value))


def _render_leaderboard_card(
    df: "pd.DataFrame", *, title: str, note: str, percent_columns: frozenset[str] = frozenset()
) -> str:
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
    return (
        f'{_CARD_STYLE}<div class="pyrit-leaderboard-card">'
        f'<div class="lb-head"><h4>{html.escape(title)}</h4><span class="lb-meta">{meta}</span></div>'
        f'<table><thead><tr><th class="lb-rank">#</th>{header_cells}</tr></thead>'
        f"<tbody>{body_rows}</tbody></table>"
        f'<div class="lb-note">{html.escape(note)}</div></div>'
    )


def _render_empty_card(*, title: str, note: str) -> str:
    """Render a styled card with just a note, used in place of a table when there's no data yet."""
    return (
        f'{_CARD_STYLE}<div class="pyrit-leaderboard-card">'
        f'<div class="lb-head"><h4>{html.escape(title)}</h4></div>'
        f'<div class="lb-note">{html.escape(note)}</div></div>'
    )


def _render_objective_scorer_leaderboard() -> str:
    """Render the Objective Scorer Leaderboard, grouped by scorer name and ranked by F1 score, as an HTML fragment."""
    import pandas as pd

    from pyrit.score import get_all_objective_metrics

    objective_metrics = get_all_objective_metrics()
    # Group same-class scorers together first (a class can have several rows if it's been
    # evaluated under more than one hashed configuration; see ComponentIdentifier.unique_name),
    # then rank each group by F1 (higher is better) so the `::<hash>` suffix in "Name" is easy
    # to scan against its sibling rows instead of being scattered across the whole table.
    objective_metrics.sort(key=lambda entry: (entry.scorer_identifier.class_name, -entry.metrics.f1_score))

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
    return _render_leaderboard_card(
        objective_df,
        title="Objective Scorer Leaderboard",
        note="All metrics computed against human-labeled ground truth. Grouped by scorer name, ranked by F1 "
        "within each group (higher is better across all four metrics).",
        percent_columns=frozenset({"Accuracy", "F1 Score", "Precision", "Recall"}),
    )


def _render_harm_scorer_leaderboard() -> str:
    """Render the Harm Scorer Leaderboard, ranked by combined Krippendorff's alpha, as an HTML fragment."""
    import pandas as pd

    from pyrit.common.path import SCORER_EVALS_HARM_PATH
    from pyrit.score import get_all_harm_metrics

    # Harm categories are discovered from the files present on disk rather than a hardcoded
    # list, so a newly added category shows up here without a code change.
    harm_categories = sorted(
        path.name.removesuffix("_metrics.jsonl") for path in SCORER_EVALS_HARM_PATH.glob("*_metrics.jsonl")
    )

    harm_metrics = [
        (harm_category, entry)
        for harm_category in harm_categories
        for entry in get_all_harm_metrics(harm_category=harm_category)
    ]
    # Harm category first (alpha isn't comparable across categories, so each category's rows
    # must stay together), then scorer class name (a class can have several rows if it's been
    # evaluated under more than one hashed configuration; see ComponentIdentifier.unique_name),
    # then alpha descending (higher is better) within each (category, class) group.
    harm_metrics.sort(
        key=lambda item: (
            item[0],
            item[1].scorer_identifier.class_name,
            -item[1].metrics.krippendorff_alpha_combined,
        )
    )

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
    return _render_leaderboard_card(
        harm_df,
        title="Harm Scorer Leaderboard",
        note="MAE: lower is better (0-1 scale). Alpha = Krippendorff's alpha: higher is better (agreement "
        "between scorer and ground truth). Grouped by harm category then scorer name — not comparable "
        "across harm categories.",
    )


def _render_adversarial_model_leaderboard() -> str:
    """Render the Adversarial Model Leaderboard, ranked by average success rate, as an HTML fragment."""
    import pandas as pd

    from pyrit.common.path import BENCHMARK_RESULTS_PATH

    title = "Adversarial Model Leaderboard"
    store_path = BENCHMARK_RESULTS_PATH / "adversarial_benchmark_metrics.jsonl"
    if not store_path.exists():
        return _render_empty_card(title=title, note=f"No benchmark data yet at {store_path}.")

    # Scoped to the systematic sweep dataset only, so a handful of unrelated single-sample rows
    # (e.g. ad hoc smoke tests against other datasets) can't skew the Avg ASR column.
    benchmark_df = pd.read_json(store_path, lines=True)
    benchmark_df = benchmark_df[benchmark_df["dataset"] == "adversarial_benchmark_v1"]
    if benchmark_df.empty:
        return _render_empty_card(title=title, note=f"No adversarial_benchmark_v1 data yet at {store_path}.")

    # A (technique, adversarial_model) pair can have two rows if it's been measured both before
    # and after full scorer-identity tracking landed (see "Note on scope" on the docs page) —
    # prefer the identified row so the pivot below doesn't need to pick a column to key on.
    is_identified = benchmark_df["objective_scorer"] != "<unknown>"
    benchmark_df = benchmark_df.assign(_is_identified=is_identified).sort_values("_is_identified", ascending=False)
    benchmark_df = benchmark_df.drop_duplicates(subset=["technique", "adversarial_model"], keep="first")

    pivot_df = benchmark_df.pivot(index="adversarial_model", columns="technique", values="success_rate")
    technique_columns = list(pivot_df.columns)
    pivot_df["Avg ASR"] = pivot_df[technique_columns].mean(axis=1)
    pivot_df = pivot_df.sort_values("Avg ASR", ascending=False)

    display_df = pivot_df.reset_index().rename(columns={"adversarial_model": "Adversarial Model"})
    display_df = display_df[["Adversarial Model", "Avg ASR", *technique_columns]]

    return _render_leaderboard_card(
        display_df,
        title=title,
        note="One row per adversarial model, ranked by Avg ASR (mean success rate across techniques) "
        "descending — higher means a more effective attacker. Each technique column is that model's success "
        "rate for that technique alone. Scoped to the adversarial_benchmark_v1 dataset; see the docs page for "
        "what's excluded and why.",
        percent_columns=frozenset({"Avg ASR", *technique_columns}),
    )


async def generate_dashboard_html_async() -> None:
    """
    Regenerate every HTML fragment the metrics dashboard docs pages `{include}`.

    Initializes PyRIT with an in-memory database, renders the Objective Scorer, Harm Scorer,
    and Adversarial Model leaderboards from the committed evaluation data, and writes each as a
    standalone HTML fragment to `doc/dashboard/_generated/`.
    """
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async

    print("Initializing PyRIT...")
    await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fragments = {
        "objective_scorer_leaderboard.html": _render_objective_scorer_leaderboard(),
        "harm_scorer_leaderboard.html": _render_harm_scorer_leaderboard(),
        "adversarial_model_leaderboard.html": _render_adversarial_model_leaderboard(),
    }
    for filename, content in fragments.items():
        output_path = _OUTPUT_DIR / filename
        output_path.write_text(content, encoding="utf-8")
        print(f"  Wrote {output_path}")

    print("Done. Commit the updated files under doc/dashboard/_generated/ through a normal PR.")


if __name__ == "__main__":
    print("=" * 60)
    print("PyRIT Dashboard HTML Generator")
    print("=" * 60)
    print(f"Fragments will be written to: {_OUTPUT_DIR}")
    print("=" * 60)
    print()

    try:
        asyncio.run(generate_dashboard_html_async())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
