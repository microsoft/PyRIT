# Adversarial Sampling Sweeps

PyRIT's adversarial chat targets default to `temperature=1.2` (both the `get_default_adversarial_target`
fallback and the `adversarial_chat*` entries registered by `TargetInitializer`). A sampling sweep measures
whether that choice, or any other value, changes attack success rate (ASR) for a given adversarial model.
It is the experiment requested in [issue #365](https://github.com/microsoft/PyRIT/issues/365).

A sweep does not add a new scenario. It runs [`AdversarialBenchmark`](benchmark.ipynb) unchanged, with one
adversarial target per (model, value) pair:

1. Each adversarial model is described by an `AdversarialGeneratorSpec` whose factory builds a fresh target
   for a given value, for example `{"temperature": 0.7}`.
2. The sweep builds every variant and checks that variants of one model differ **only** in the swept
   parameter, that each target reports the requested value in its identifier, and that no variant is also
   the objective target or the scorer's judge.
3. Variants are registered in `TargetRegistry` under deterministic names such as `gpt4o__temperature_0p7`
   for the duration of the sweep, then removed. Existing registry entries are never replaced.
4. `AdversarialBenchmark` runs once per repetition with cache reuse disabled, the same objective target,
   scorer, technique and pinned objectives, and a seeded per-repetition variant order.
5. Each repetition becomes one `AdversarialTrialRecord` per (variant, objective). Only the newest result per
   objective is kept, as in `build_scripts/export_adversarial_benchmark_result.py`, and planned objectives
   with no result are recorded as `error`.

Temperature and `top_p` are swept in separate plans so their effects are not confounded.

## Running a sweep

Pin the objectives first. `build_scripts/prepare_adversarial_benchmark_dataset.py` stores the
`harmbench-balanced-14-v1` subset (two HarmBench behaviors per semantic category). Records identify
objectives by HarmBench `BehaviorID` and never store objective text.

```python
from pathlib import Path

from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.benchmark import (
    AdversarialGeneratorSpec,
    SamplingParameter,
    SamplingSweepPlan,
    run_sampling_sweep_async,
)

def openai_generator(name: str, *, endpoint: str, model_name: str) -> AdversarialGeneratorSpec:
    return AdversarialGeneratorSpec(
        name=name,
        factory=lambda sampling: OpenAIChatTarget(endpoint=endpoint, model_name=model_name, **sampling),
    )

plan = SamplingSweepPlan(
    generators=[
        openai_generator("model_a", endpoint="<endpoint-a>", model_name="<model-a>"),
        openai_generator("model_b", endpoint="<endpoint-b>", model_name="<model-b>"),
    ],
    parameter=SamplingParameter.TEMPERATURE,
    values=[0.2, 0.7, 1.2],
    baseline_value=1.2,
    repetitions=3,
    technique="crescendo_simulated",
)

records = await run_sampling_sweep_async(
    plan=plan,
    experiment_id="issue-365-temperature-pilot",
    objective_target=objective_target,  # fixed for the whole sweep
    objective_scorer=objective_scorer,  # fixed; must not use an adversarial variant as its judge
    dataset_config=DatasetAttackConfiguration(
        sources=[DatasetSource(name="harmbench-balanced-14-v1")], max_per_dataset="all", max_total="all"
    ),
    output_path=Path("results/issue-365-temperature-pilot.jsonl"),
)
```

`plan.planned_attack_count(objective_count=14)` gives the attack count before retries
(6 variants × 14 objectives × 3 repetitions = 252 for the plan above). Each attack makes several adversarial,
objective and scorer calls, so estimate cost before running. If the sweep is interrupted, run it again with
the same arguments: repetitions already in `output_path` are skipped and an incomplete repetition is rerun.

Models that reject a parameter (for example reasoning models that only accept the default temperature)
should omit it from `supported_parameters`. The plan then raises, or with
`UnsupportedParameterPolicy.SKIP` leaves the model out and reports it in `plan.skipped_generators`.

### Technique choice

Use one technique per sweep. The sweep stops before running if a technique scores with the adversarial
target: TAP, for example, replaces a true/false benchmark scorer with a `SelfAskScaleScorer` on
`adversarial_chat`, so its judge would change together with the attacker.

## Analyzing results

```python
from pyrit.analytics import build_experiment_report, format_report_markdown, read_trial_records

records = read_trial_records(path=Path("results/issue-365-temperature-pilot.jsonl"))
report = build_experiment_report(records=records, baseline_value=1.2)
print(format_report_markdown(report=report))
```

The report contains, for each (model, value) cell, ASR with a Wilson 95% interval, mean and standard
deviation of per-repetition ASR, error and undetermined rates, mean turns and Crescendo backtracks, and
provider-reported adversarial token counts with an optional cost estimate (`TokenPrice`). It also contains
each value's ASR difference from the baseline and, for each pair of models, the difference between their
differences (a model-by-value interaction).

`denominator="decided"` (the default) matches PyRIT's `success_rate` and excludes `error` and
`undetermined` outcomes. `denominator="all"` matches the benchmark exporter and counts them as
non-successes. Report both when error rates differ between cells.

### Statistical limitations

- Differences and interactions use a paired bootstrap that resamples objectives, because repetitions of
  one objective are correlated. Wilson intervals for single cells treat trials as independent and are
  therefore too narrow when objectives repeat.
- No multiple-comparison correction is applied. With two models and three values the pilot makes several
  comparisons; treat them as exploratory.
- `minimum_detectable_difference(trials_per_arm=42, baseline_asr=0.3)` is about 28 percentage points, and
  clustering makes the real figure larger. A 14-objective pilot can rule out only large effects; a null
  result is not evidence that temperature does not matter.
- Results hold for the chosen objective target, scorer, technique and objectives. Changing PyRIT's default
  adversarial settings should wait for evidence across several of each.
