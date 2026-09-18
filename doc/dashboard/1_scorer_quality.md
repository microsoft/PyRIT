# Scorer Quality

Leaderboards for the scorer evaluation metrics PyRIT already tracks under
`pyrit/datasets/scorer_evals/`. See [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb) for
what each metric means and how these numbers are produced.

## Objective Scorer Leaderboard

Objective scorers answer a true/false question (e.g. "was the objective achieved?"). Ranked by
F1 score, the harmonic mean of precision and recall.

```{include} _generated/objective_scorer_leaderboard.html
```

## Harm Scorer Leaderboard

Harm scorers produce a severity score (0.0-1.0). Ranked by `krippendorff_alpha_combined` —
agreement between the model's scores and human raters, ranging from -1.0 (systematic
disagreement) to 1.0 (perfect agreement) — across every harm category PyRIT currently has
metrics for. Alpha isn't comparable *across* categories (each has its own human-labeled
dataset), so treat this as one leaderboard per category, stacked into a single table for
convenience.

```{include} _generated/harm_scorer_leaderboard.html
```

## Note on scope

`get_all_objective_metrics()` reads `objective/objective_achieved_metrics.jsonl` only, matching
how it's documented and used elsewhere in PyRIT. A separate `refusal_scorer/refusal_metrics.jsonl`
registry evaluates refusal scorers against its own human-labeled dataset, using the same
`ObjectiveScorerMetrics` shape. It isn't merged into the leaderboard above because it measures a
different task (refusal detection, not objective achievement) against a different ground truth
set, and mixing the two would make the F1 ranking misleading. A follow-up could add it as its own
leaderboard.

These leaderboards are static snapshots, not live views: they're rendered from committed JSONL
data by `python -m build_scripts.generate_dashboard_html` (see
[Refreshing the data](0_dashboard.md#refreshing-the-data)), not computed when this page loads.
