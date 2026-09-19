# Benchmark Leaderboard

Attack success rate (ASR) for adversarial models, built from `AdversarialBenchmark` scenario
runs. See [Benchmark Scenarios](../scanner/benchmark.ipynb) for how to run the scenario yourself
and `build_scripts/export_adversarial_benchmark_result.py` for how a run's results land in the
committed store this page reads.

## Adversarial Model Leaderboard

One row per adversarial model, ranked by **Avg ASR** — the mean success rate (the share of
objectives where the target's response was scored as achieving the objective) across
techniques — descending; higher means a more effective attacker. Each technique column is that
model's success rate for that technique alone. Scoped to the `adversarial_benchmark_v1` dataset
(see "Note on scope" below for what's excluded and why).

```{include} _generated/adversarial_model_leaderboard.html
```

## Note on scope

The underlying store is a snapshot upserted by `--update-benchmark-store`, keyed on
`(technique, adversarial_model, objective_target, objective_scorer, dataset)` — a fresh run of
the same combination replaces its row rather than appending, so it stays a single
best-known-result table rather than an unbounded history. The leaderboard above pivots that
store into one row per adversarial model. It's scoped to the `adversarial_benchmark_v1` dataset
(excluding a handful of unrelated single-sample rows from other datasets so they don't skew Avg
ASR) and, when a `(technique, adversarial_model)` pair has more than one stored row, keeps the
one with a known `objective_scorer` (see below). Sample sizes behind these percentages are small
demo-scale runs (`--max-dataset-size`), not a statistically robust evaluation; treat this as a
preview of the mechanism rather than a final verdict on any technique or model. Widening this
into a larger, regularly-refreshed sweep — and adding a native objective-target comparison
alongside the current adversarial-model comparison — is future work.

The `crescendo_simulated` / `role_play_video_game` / `tap` rows on the `adversarial_benchmark_v1`
dataset were originally imported via `build_scripts/import_adversarial_benchmark_snapshot.py`
from an Azure DevOps pipeline run of PyRIT PR #2551's dataset and pipeline changes, which had not
yet merged to `main` at the time; those rows have `objective_scorer` reading `<unknown>` because
they predate this store's identity fields and their exact scorer wasn't captured alongside the
artifact. Each of those combinations now also has a fully-identified row from a subsequent run
through the export pipeline — the leaderboard above keeps that identified row and drops its
`<unknown>` predecessor, but the underlying JSONL store keeps both until the `<unknown>` rows are
cleaned up.

This leaderboard is a static snapshot, not a live view: it's rendered from the committed JSONL
store by `python -m build_scripts.generate_dashboard_html` (see
[Refreshing the data](0_dashboard.md#refreshing-the-data)), not computed when this page loads.
