# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Statistics for adversarial-generator settings experiments (GitHub issue #365).

These helpers consume ``AdversarialTrialRecord`` rows, one per (generator, sampling value,
repetition, objective), and report attack success rate (ASR) with uncertainty. They are pure
functions over records: they never query memory or call targets, so they can be validated on
synthetic fixtures and re-run on exported JSONL files.

Statistical choices and their limits:

* Cell ASR intervals use the Wilson score interval. Trials inside a cell are treated as
  independent Bernoulli draws, which understates uncertainty when the same objective is
  repeated; use the objective-cluster bootstrap for comparisons.
* Differences against the baseline value are paired by objective and resampled by objective
  (cluster bootstrap), because repetitions of one objective are correlated and the experiment's
  unit of generalization is the objective set, not the individual call.
* Model-by-setting interaction is reported as a difference-in-differences of ASR deltas with the
  same paired bootstrap. With a 12-objective pilot, intervals are wide; a null result is not
  evidence of no effect.
* No multiple-comparison correction is applied. Treat pilot comparisons as exploratory.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from pyrit.analytics.outcome_statistics import compute_outcome_statistics
from pyrit.models import AttackOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

RECORD_SCHEMA_VERSION = 1
_Z_95 = statistics.NormalDist().inv_cdf(0.975)

SuccessDenominator = Literal["decided", "all"]


@dataclass(frozen=True)
class AdversarialTrialRecord:
    """
    One attack attempt in an adversarial-settings experiment.

    Records hold identifiers and measurements only. Objective text and conversation content stay
    in PyRIT memory so exported files can be shared without copying red-teaming content.

    Args:
        experiment_id (str): Identifier shared by every record in the experiment.
        trial_id (str): Deterministic identifier for this (cell, objective) pair.
        cell_id (str): Deterministic identifier for the (generator, value, repetition) cell.
        generator_name (str): Name of the adversarial generator configuration.
        technique (str): Attack technique name, for example ``"crescendo_simulated"``.
        parameter (str): Swept sampling parameter, for example ``"temperature"``.
        value (float): Value of the swept parameter used by the adversarial target.
        repetition (int): Zero-based independent repetition index.
        objective_id (str): Stable hash of the objective text.
        outcome (str): ``AttackOutcome`` value.
        outcome_reason (str | None): Reason reported by the attack, or the error description.
        error_type (str | None): Exception type when the cell failed before producing a result.
        executed_turns (int | None): Turns executed against the objective target.
        backtrack_count (int | None): Crescendo backtracks (objective-target refusals detected).
        execution_time_ms (int | None): Attack wall-clock time.
        adversarial_input_tokens (int | None): Prompt tokens consumed by the adversarial target.
        adversarial_output_tokens (int | None): Completion tokens produced by the adversarial target.
        objective_input_tokens (int | None): Prompt tokens consumed by the objective target.
        objective_output_tokens (int | None): Completion tokens produced by the objective target.
        attack_result_id (str | None): ``AttackResult`` id in PyRIT memory.
        superseded_attempts (int): Older results for the same objective in the same cell that a retry
            replaced. Only the newest result is recorded, matching the benchmark exporter.
        schema_version (int): Record schema version.
    """

    experiment_id: str
    trial_id: str
    cell_id: str
    generator_name: str
    technique: str
    parameter: str
    value: float
    repetition: int
    objective_id: str
    outcome: str
    outcome_reason: str | None = None
    error_type: str | None = None
    executed_turns: int | None = None
    backtrack_count: int | None = None
    execution_time_ms: int | None = None
    adversarial_input_tokens: int | None = None
    adversarial_output_tokens: int | None = None
    objective_input_tokens: int | None = None
    objective_output_tokens: int | None = None
    attack_result_id: str | None = None
    superseded_attempts: int = 0
    schema_version: int = RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """
        Validate the outcome and repetition.

        Raises:
            ValueError: If the outcome is not an ``AttackOutcome`` value or repetition is negative.
        """
        AttackOutcome(self.outcome)
        if self.repetition < 0:
            raise ValueError("repetition must be nonnegative.")


@dataclass(frozen=True)
class CellSummary:
    """ASR and operational measurements for one (generator, parameter value) cell."""

    generator_name: str
    parameter: str
    value: float
    trials: int
    successes: int
    failures: int
    undetermined: int
    errors: int
    asr: float | None
    asr_ci_low: float | None
    asr_ci_high: float | None
    error_rate: float
    undetermined_rate: float
    repetition_asr_mean: float | None
    repetition_asr_stdev: float | None
    mean_turns: float | None
    mean_backtracks: float | None
    mean_execution_time_ms: float | None
    adversarial_input_tokens: int | None
    adversarial_output_tokens: int | None
    estimated_cost: float | None = None


@dataclass(frozen=True)
class BaselineComparison:
    """ASR difference between a setting and the baseline setting for one generator."""

    generator_name: str
    value: float
    baseline_value: float
    asr: float | None
    baseline_asr: float | None
    delta: float | None
    delta_ci_low: float | None
    delta_ci_high: float | None
    paired_objectives: int


@dataclass(frozen=True)
class InteractionEstimate:
    """Difference between two generators' ASR deltas at one value (difference-in-differences)."""

    generator_a: str
    generator_b: str
    value: float
    baseline_value: float
    delta_a: float | None
    delta_b: float | None
    interaction: float | None
    ci_low: float | None
    ci_high: float | None
    paired_objectives: int


@dataclass(frozen=True)
class TokenPrice:
    """Price per one million tokens for a generator, in any single currency."""

    input_per_million: float
    output_per_million: float


@dataclass
class ExperimentReport:
    """All summaries produced from one experiment's records."""

    denominator: SuccessDenominator
    cells: list[CellSummary] = field(default_factory=list)
    comparisons: list[BaselineComparison] = field(default_factory=list)
    interactions: list[InteractionEstimate] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """
        Return a JSON-serializable representation.

        Returns:
            dict[str, object]: The report as plain data.
        """
        return {
            "denominator": self.denominator,
            "cells": [asdict(cell) for cell in self.cells],
            "comparisons": [asdict(item) for item in self.comparisons],
            "interactions": [asdict(item) for item in self.interactions],
        }


def write_trial_records(*, records: Iterable[AdversarialTrialRecord], path: Path, append: bool = True) -> None:
    """
    Write records as JSON lines.

    Args:
        records (Iterable[AdversarialTrialRecord]): Records to write.
        path (Path): Destination file.
        append (bool): Append instead of overwriting. Defaults to True so long runs can resume.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def read_trial_records(*, path: Path) -> list[AdversarialTrialRecord]:
    """
    Read records written by ``write_trial_records``.

    Args:
        path (Path): Source file.

    Returns:
        list[AdversarialTrialRecord]: The records, in file order.

    Raises:
        ValueError: If a line has an unsupported schema version or invalid fields.
    """
    if not path.exists():
        return []
    records: list[AdversarialTrialRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("schema_version") != RECORD_SCHEMA_VERSION:
                raise ValueError(f"Unsupported record schema version on line {line_number}.")
            try:
                records.append(AdversarialTrialRecord(**data))
            except TypeError as error:
                raise ValueError(f"Invalid record on line {line_number}.") from error
    return records


def wilson_interval(*, successes: int, total: int, z: float = _Z_95) -> tuple[float, float] | None:
    """
    Compute the Wilson score interval for a binomial proportion.

    Args:
        successes (int): Number of successes.
        total (int): Number of trials.
        z (float): Normal quantile. Defaults to the two-sided 95% value.

    Returns:
        tuple[float, float] | None: Lower and upper bounds, or None when ``total`` is zero.

    Raises:
        ValueError: If counts are negative or successes exceed total.
    """
    if successes < 0 or total < 0 or successes > total:
        raise ValueError("Counts must satisfy 0 <= successes <= total.")
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def build_experiment_report(
    *,
    records: Sequence[AdversarialTrialRecord],
    baseline_value: float,
    denominator: SuccessDenominator = "decided",
    prices: Mapping[str, TokenPrice] | None = None,
    bootstrap_samples: int = 2000,
    bootstrap_seed: int = 0,
) -> ExperimentReport:
    """
    Summarize cells, compare each setting with the baseline, and estimate interactions.

    Args:
        records (Sequence[AdversarialTrialRecord]): Records from one experiment and one parameter.
        baseline_value (float): The parameter value treated as the baseline (for example 1.2).
        denominator (SuccessDenominator): ``"decided"`` excludes error and undetermined outcomes
            from ASR (PyRIT's ``success_rate``); ``"all"`` counts them as non-successes.
        prices (Mapping[str, TokenPrice] | None): Optional adversarial-generator prices for cost estimates.
        bootstrap_samples (int): Number of objective-cluster bootstrap resamples.
        bootstrap_seed (int): Seed for the bootstrap so reports are reproducible.

    Returns:
        ExperimentReport: Cell summaries, baseline comparisons, and pairwise interactions.

    Raises:
        ValueError: If records mix experiments or parameters, or contain duplicate trial ids.
    """
    _validate_population(records)
    report = ExperimentReport(denominator=denominator)
    groups = _group_by(records, key=lambda r: (r.generator_name, r.value))
    for (generator_name, value), cell_records in sorted(groups.items()):
        report.cells.append(_summarize_cell(cell_records, denominator=denominator, prices=prices))
        if value == baseline_value:
            continue
        baseline_records = groups.get((generator_name, baseline_value), [])
        report.comparisons.append(
            _compare(
                generator_name=generator_name,
                value=value,
                baseline_value=baseline_value,
                treatment=cell_records,
                baseline=baseline_records,
                denominator=denominator,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            )
        )
    generators = sorted({r.generator_name for r in records})
    values = sorted({r.value for r in records if r.value != baseline_value})
    for index, generator_a in enumerate(generators):
        for generator_b in generators[index + 1 :]:
            for value in values:
                report.interactions.append(
                    _interaction(
                        groups=groups,
                        generator_a=generator_a,
                        generator_b=generator_b,
                        value=value,
                        baseline_value=baseline_value,
                        denominator=denominator,
                        samples=bootstrap_samples,
                        seed=bootstrap_seed,
                    )
                )
    return report


def minimum_detectable_difference(*, trials_per_arm: int, baseline_asr: float, power: float = 0.8) -> float:
    """
    Approximate the smallest ASR difference a two-arm comparison can detect.

    Uses the normal approximation for two independent proportions at a two-sided 5% level. It
    ignores clustering by objective, so the real detectable difference is larger.

    Args:
        trials_per_arm (int): Decided trials per arm.
        baseline_asr (float): Expected baseline ASR in [0, 1].
        power (float): Desired power. Defaults to 0.8.

    Returns:
        float: Approximate minimum detectable absolute ASR difference.

    Raises:
        ValueError: If inputs are out of range.
    """
    if trials_per_arm <= 0 or not 0 <= baseline_asr <= 1 or not 0 < power < 1:
        raise ValueError("Invalid design inputs.")
    z_power = statistics.NormalDist().inv_cdf(power)
    variance = max(baseline_asr * (1 - baseline_asr), 0.01)
    return (_Z_95 + z_power) * math.sqrt(2 * variance / trials_per_arm)


def format_report_markdown(*, report: ExperimentReport, synthetic: bool = False) -> str:
    """
    Render a report as Markdown tables.

    Args:
        report (ExperimentReport): The report to render.
        synthetic (bool): Label the output as synthetic fixture data, not experimental evidence.

    Returns:
        str: Markdown text.
    """
    lines: list[str] = []
    if synthetic:
        lines += ["> **SYNTHETIC FIXTURE DATA — not experimental evidence.**", ""]
    lines += [
        f"ASR denominator: `{report.denominator}`. Intervals are 95%.",
        "",
        "| Generator | Value | n | ASR | 95% CI (Wilson) | Rep. mean ± sd | Errors | Undet. | Turns | Backtracks |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cell in report.cells:
        lines.append(
            f"| {cell.generator_name} | {cell.value:g} | {cell.trials} | {_pct(cell.asr)} | "
            f"{_interval(cell.asr_ci_low, cell.asr_ci_high)} | "
            f"{_pct(cell.repetition_asr_mean)} ± {_pct(cell.repetition_asr_stdev)} | "
            f"{_pct(cell.error_rate)} | {_pct(cell.undetermined_rate)} | "
            f"{_num(cell.mean_turns)} | {_num(cell.mean_backtracks)} |"
        )
    if report.comparisons:
        lines += [
            "",
            "| Generator | Value vs baseline | ΔASR | 95% CI (objective bootstrap) | Paired objectives |",
            "|---|---|---|---|---|",
        ]
        for item in report.comparisons:
            lines.append(
                f"| {item.generator_name} | {item.value:g} vs {item.baseline_value:g} | {_signed(item.delta)} | "
                f"{_interval(item.delta_ci_low, item.delta_ci_high, signed=True)} | {item.paired_objectives} |"
            )
    if report.interactions:
        lines += [
            "",
            "| Generators | Value | Δ(A) − Δ(B) | 95% CI | Paired objectives |",
            "|---|---|---|---|---|",
        ]
        for item in report.interactions:
            lines.append(
                f"| {item.generator_a} − {item.generator_b} | {item.value:g} | {_signed(item.interaction)} | "
                f"{_interval(item.ci_low, item.ci_high, signed=True)} | {item.paired_objectives} |"
            )
    return "\n".join(lines) + "\n"


def _validate_population(records: Sequence[AdversarialTrialRecord]) -> None:
    if len({r.experiment_id for r in records}) > 1:
        raise ValueError("Records must come from a single experiment.")
    if len({r.parameter for r in records}) > 1:
        raise ValueError("Records must sweep a single parameter; analyze temperature and top_p separately.")
    if len({r.technique for r in records}) > 1:
        raise ValueError("Records must use a single technique; ASR is not comparable across techniques.")
    trial_ids = [r.trial_id for r in records]
    if len(trial_ids) != len(set(trial_ids)):
        raise ValueError("Records contain duplicate trial ids.")


def _group_by(
    records: Iterable[AdversarialTrialRecord], *, key: Callable[[AdversarialTrialRecord], tuple[str, float]]
) -> dict[tuple[str, float], list[AdversarialTrialRecord]]:
    groups: dict[tuple[str, float], list[AdversarialTrialRecord]] = defaultdict(list)
    for record in records:
        groups[key(record)].append(record)
    return groups


def _rate(records: Sequence[AdversarialTrialRecord], *, denominator: SuccessDenominator) -> float | None:
    stats = compute_outcome_statistics(_counts(records))
    return stats.success_rate if denominator == "decided" else stats.success_rate_all


def _counts(records: Iterable[AdversarialTrialRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record.outcome] += 1
    return dict(counts)


def _summarize_cell(
    records: Sequence[AdversarialTrialRecord],
    *,
    denominator: SuccessDenominator,
    prices: Mapping[str, TokenPrice] | None,
) -> CellSummary:
    first = records[0]
    stats = compute_outcome_statistics(_counts(records))
    asr_total = stats.total_decided if denominator == "decided" else stats.total_results
    interval = wilson_interval(successes=stats.successes, total=asr_total)
    by_repetition: dict[int, list[AdversarialTrialRecord]] = defaultdict(list)
    for record in records:
        by_repetition[record.repetition].append(record)
    repetition_rates = [
        rate for rate in (_rate(group, denominator=denominator) for group in by_repetition.values()) if rate is not None
    ]
    input_tokens = _sum_optional(r.adversarial_input_tokens for r in records)
    output_tokens = _sum_optional(r.adversarial_output_tokens for r in records)
    price = (prices or {}).get(first.generator_name)
    cost = None
    if price is not None and input_tokens is not None and output_tokens is not None:
        cost = (input_tokens * price.input_per_million + output_tokens * price.output_per_million) / 1_000_000
    return CellSummary(
        generator_name=first.generator_name,
        parameter=first.parameter,
        value=first.value,
        trials=stats.total_results,
        successes=stats.successes,
        failures=stats.failures,
        undetermined=stats.undetermined,
        errors=stats.errors,
        asr=stats.success_rate if denominator == "decided" else stats.success_rate_all,
        asr_ci_low=interval[0] if interval else None,
        asr_ci_high=interval[1] if interval else None,
        error_rate=stats.outcome_shares[AttackOutcome.ERROR],
        undetermined_rate=stats.outcome_shares[AttackOutcome.UNDETERMINED],
        repetition_asr_mean=statistics.fmean(repetition_rates) if repetition_rates else None,
        repetition_asr_stdev=statistics.stdev(repetition_rates) if len(repetition_rates) > 1 else None,
        mean_turns=_mean_optional(r.executed_turns for r in records),
        mean_backtracks=_mean_optional(r.backtrack_count for r in records),
        mean_execution_time_ms=_mean_optional(r.execution_time_ms for r in records),
        adversarial_input_tokens=input_tokens,
        adversarial_output_tokens=output_tokens,
        estimated_cost=cost,
    )


def _by_objective(records: Iterable[AdversarialTrialRecord]) -> dict[str, list[AdversarialTrialRecord]]:
    grouped: dict[str, list[AdversarialTrialRecord]] = defaultdict(list)
    for record in records:
        grouped[record.objective_id].append(record)
    return grouped


def _pooled_rate(
    groups: Sequence[Mapping[str, list[AdversarialTrialRecord]]],
    objective_ids: Sequence[str],
    *,
    denominator: SuccessDenominator,
) -> list[float | None]:
    return [
        _rate([r for objective_id in objective_ids for r in group[objective_id]], denominator=denominator)
        for group in groups
    ]


def _paired_bootstrap(
    *,
    groups: Sequence[Mapping[str, list[AdversarialTrialRecord]]],
    statistic: Callable[[list[float | None]], float | None],
    denominator: SuccessDenominator,
    samples: int,
    seed: int,
) -> tuple[float | None, float | None, float | None, int]:
    shared = sorted(set.intersection(*(set(group) for group in groups)))
    if not shared:
        return None, None, None, 0
    point = statistic(_pooled_rate(groups, shared, denominator=denominator))
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        resampled = [rng.choice(shared) for _ in shared]
        value = statistic(_pooled_rate(groups, resampled, denominator=denominator))
        if value is not None:
            estimates.append(value)
    if not estimates:
        return point, None, None, len(shared)
    estimates.sort()
    low = estimates[int(0.025 * (len(estimates) - 1))]
    high = estimates[int(math.ceil(0.975 * (len(estimates) - 1)))]
    return point, low, high, len(shared)


def _compare(
    *,
    generator_name: str,
    value: float,
    baseline_value: float,
    treatment: Sequence[AdversarialTrialRecord],
    baseline: Sequence[AdversarialTrialRecord],
    denominator: SuccessDenominator,
    samples: int,
    seed: int,
) -> BaselineComparison:
    asr = _rate(treatment, denominator=denominator)
    baseline_asr = _rate(baseline, denominator=denominator) if baseline else None
    if not baseline:
        return BaselineComparison(generator_name, value, baseline_value, asr, None, None, None, None, 0)
    delta, low, high, paired = _paired_bootstrap(
        groups=[_by_objective(treatment), _by_objective(baseline)],
        statistic=_difference,
        denominator=denominator,
        samples=samples,
        seed=seed,
    )
    return BaselineComparison(generator_name, value, baseline_value, asr, baseline_asr, delta, low, high, paired)


def _interaction(
    *,
    groups: Mapping[tuple[str, float], list[AdversarialTrialRecord]],
    generator_a: str,
    generator_b: str,
    value: float,
    baseline_value: float,
    denominator: SuccessDenominator,
    samples: int,
    seed: int,
) -> InteractionEstimate:
    keys = [(generator_a, value), (generator_a, baseline_value), (generator_b, value), (generator_b, baseline_value)]
    if any(key not in groups for key in keys):
        return InteractionEstimate(generator_a, generator_b, value, baseline_value, None, None, None, None, None, 0)
    by_objective = [_by_objective(groups[key]) for key in keys]
    delta_a = _difference(
        [_rate(groups[keys[0]], denominator=denominator), _rate(groups[keys[1]], denominator=denominator)]
    )
    delta_b = _difference(
        [_rate(groups[keys[2]], denominator=denominator), _rate(groups[keys[3]], denominator=denominator)]
    )
    interaction, low, high, paired = _paired_bootstrap(
        groups=by_objective,
        statistic=_difference_in_differences,
        denominator=denominator,
        samples=samples,
        seed=seed,
    )
    return InteractionEstimate(
        generator_a, generator_b, value, baseline_value, delta_a, delta_b, interaction, low, high, paired
    )


def _difference(rates: list[float | None]) -> float | None:
    if rates[0] is None or rates[1] is None:
        return None
    return rates[0] - rates[1]


def _difference_in_differences(rates: list[float | None]) -> float | None:
    first = _difference(rates[:2])
    second = _difference(rates[2:])
    if first is None or second is None:
        return None
    return first - second


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _mean_optional(values: Iterable[int | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f} pp"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _interval(low: float | None, high: float | None, *, signed: bool = False) -> str:
    if low is None or high is None:
        return "n/a"
    if signed:
        return f"[{low * 100:+.1f}, {high * 100:+.1f}] pp"
    return f"[{low * 100:.1f}%, {high * 100:.1f}%]"
