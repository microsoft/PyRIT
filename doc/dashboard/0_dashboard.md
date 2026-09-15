# Metrics Dashboard

PyRIT tracks numeric "how good is this component" metrics for several parts of the framework.
This section renders those metrics as leaderboard tables, so you can compare configurations at
a glance instead of digging through JSONL files by hand.

## What's here today

- **[Scorer Quality](1_scorer_quality.ipynb)** — an Objective Scorer Leaderboard (accuracy,
  F1, precision, recall) and a Harm Scorer Leaderboard (mean absolute error, Krippendorff's
  alpha), built from the evaluation registries the team already maintains under
  `pyrit/datasets/scorer_evals/`. See [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb)
  for what these numbers mean and [Scoring Scorers](../blog/2026_04_14_scoring_scorers.md) for
  the full evaluation framework.
- **[Benchmark Leaderboard](2_benchmark_leaderboard.ipynb)** — attack success rate by technique
  and adversarial model, built from `AdversarialBenchmark` scenario runs via
  `build_scripts/export_adversarial_benchmark_result.py --update-benchmark-store`. The data
  behind it today is a small demo-scale run (see the page's "Note on scope") — treat it as a
  preview of the mechanism, not a statistically robust evaluation yet.

## What's planned

An objective-target robustness leaderboard (comparing target models against a fixed
adversarial model, the mirror image of today's benchmark leaderboard) reuses the same
scenario, exporter, and store — only the grouping changes. A larger, regularly-refreshed
sweep across more techniques, models, and dataset items is future work; see the "Note on
scope" section on the Benchmark Leaderboard page.

## Refreshing the data

These pages read committed JSONL files, not live services, so refreshing the dashboard is a
two-step, human-in-the-loop process: regenerate the data, then re-render the page.

**Scorer Quality:**

1. Regenerate scorer metrics locally:

   ```bash
   python -m build_scripts.evaluate_scorers
   ```

   This is safe to re-run — scorer configurations that already have up-to-date metrics are
   skipped automatically (see [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb)).
   Commit the updated files under `pyrit/datasets/scorer_evals/` through a normal PR.
2. Rebuild this page. The notebook re-reads the committed JSONL files each time it runs, and
   the site rebuilds automatically on every merge to `main`.

**Benchmark Leaderboard:**

1. Run the scenario (see [Benchmark Scenarios](../scanner/benchmark.ipynb) for the full
   `pyrit_scan` invocation and available techniques/targets).
2. Export and upsert its result into the committed store:

   ```bash
   python -m build_scripts.export_adversarial_benchmark_result \
     --scenario-result-id <id> \
     --output-dir <dir> \
     --update-benchmark-store
   ```

   Upserting is keyed on `(technique, adversarial_model, objective_target, objective_scorer,
   dataset)`, so re-running the same combination replaces its row instead of appending. Commit
   the updated `pyrit/datasets/benchmark_results/adversarial_benchmark_metrics.jsonl` through a
   normal PR.
3. Rebuild this page.

There's no CI automation that runs either of these steps or opens a PR for you yet — both
pages are refreshed manually today.
