# Metrics Dashboard

PyRIT tracks numeric "how good is this component" metrics for several parts of the framework.
This section renders those metrics as leaderboard tables, so you can compare configurations at
a glance instead of digging through JSONL files by hand.

## What's here today

- **[Scorer Quality](1_scorer_quality.md)** — an Objective Scorer Leaderboard (accuracy,
  F1, precision, recall) and a Harm Scorer Leaderboard (mean absolute error, Krippendorff's
  alpha), built from the evaluation registries the team already maintains under
  `pyrit/datasets/scorer_evals/`. See [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb)
  for what these numbers mean and [Scoring Scorers](../blog/2026_04_14_scoring_scorers.md) for
  the full evaluation framework.
- **[Benchmark Leaderboard](2_benchmark_leaderboard.md)** — an Adversarial Model Leaderboard
  (attack success rate by adversarial model, broken out by technique), built from
  `AdversarialBenchmark` scenario runs via
  `build_scripts/export_adversarial_benchmark_result.py --update-benchmark-store`. The data
  behind it today is a small demo-scale run (see the page's "Note on scope") — treat it as a
  preview of the mechanism, not a statistically robust evaluation yet.

## What's planned

An objective-target robustness leaderboard (comparing target models against a fixed
adversarial model, the mirror image of today's adversarial model leaderboard) reuses the same
scenario, exporter, and store — only the grouping changes. A larger, regularly-refreshed
sweep across more techniques, models, and dataset items is future work; see the "Note on
scope" section on the Benchmark Leaderboard page.

## Refreshing the data

Both pages `{include}` static HTML fragments under `_generated/` rather than running any Python
when the site builds, so refreshing the dashboard is a human-in-the-loop process: regenerate
the underlying JSONL data, regenerate the fragments, then commit both.

**Scorer Quality:**

1. Regenerate scorer metrics locally:

   ```bash
   python -m build_scripts.evaluate_scorers
   ```

   This is safe to re-run — scorer configurations that already have up-to-date metrics are
   skipped automatically (see [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb)).
   Commit the updated files under `pyrit/datasets/scorer_evals/` through a normal PR.
2. Regenerate the HTML fragments (see below) and commit them alongside the data.

**Benchmark Leaderboard:**

1. Run the scenario and refresh the committed store:

   ```bash
   python -m build_scripts.run_adversarial_benchmark \
     --objective-target <target> \
     --adversarial-targets <target> [<target> ...] \
     --output-dir <dir>
   ```

   This is safe to re-run — combinations already present in the committed store, or already
   completed in memory, are skipped automatically, so re-running after adding a new adversarial
   target only executes the new combinations. Pass `--force` to ignore both caches and re-run
   everything. Omit `--output-dir` to only run the scenario and print its `scenario_result_id`
   (export separately with the command below).

   For finer control over dataset selection or techniques, run the scenario directly instead
   (see [Benchmark Scenarios](../scanner/benchmark.ipynb) for the full `pyrit_scan` invocation
   and available techniques/targets), then export and upsert its result into the committed
   store:

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

   Already have a `technique-metrics.json` from elsewhere — for example downloaded from an
   Azure DevOps `adversarial-benchmark-<BuildId>` pipeline artifact (see
   `.azuredevops/adversarial-benchmark.yml`) — rather than a scenario result in local memory?
   Import it directly instead of re-running the scenario:

   ```bash
   python -m build_scripts.import_adversarial_benchmark_snapshot \
     --technique-metrics-json <path-to-technique-metrics.json>
   ```

   This upserts into the same committed store using the same key, so it composes with either
   path above.
2. Regenerate the HTML fragments (see below) and commit them alongside the data.

**Regenerating the HTML fragments:**

```bash
python -m build_scripts.generate_dashboard_html
```

This re-reads all of the committed JSONL files above and (re)writes every fragment under
`doc/dashboard/_generated/` — both pages' tables at once, regardless of which data changed.
Commit the updated fragment files through a normal PR. The site itself rebuilds automatically
on every merge to `main`, but only re-renders whatever HTML fragments are already committed —
it never runs this generator for you.

There's no CI automation that runs any of these steps or opens a PR for you yet — the
dashboard is refreshed manually today.
