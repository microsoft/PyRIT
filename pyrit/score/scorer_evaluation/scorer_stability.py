# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Measure how much a true/false scorer's verdicts depend on how the question is put to it.

A presentation is one way of asking a scorer the same question: the order its options are
listed in, the wording of a label, a paraphrase of the rubric, whether the objective comes
before the response, or harmless formatting around the response. A scorer that measures the
response should give the same verdict under each of them.

Counting how often the verdict differs between two presentations is not enough, because a
scorer that samples also differs from itself when nothing changes. For one response, let
``a`` be the chance the scorer answers true under the reference presentation and ``b`` the
chance under another. Two runs, one under each, differ with chance ``a(1 - b) + b(1 - a)``.
Two runs under the same presentation differ with chance ``2a(1 - a)`` or ``2b(1 - b)``.
The first minus the average of the other two is exactly ``(a - b) ** 2``, so::

    flip rate = repeat flip rate + presentation effect

where the presentation effect is the mean squared change in a response's chance of a true
verdict. It is zero when the presentation changes nothing, however noisy the scorer is, and
it equals the flip rate for a scorer that always answers the same way. Repeated runs give an
unbiased estimate of each term.

Whether an effect is there at all is settled by a permutation test. If a presentation changes
nothing, a response's runs are interchangeable between the reference and the presentation, so
reassigning them at random gives the distribution of the estimate with no assumption about the
scorer. The intervals describe the size of an effect and rest on a normal approximation over
responses.

A presentation can change many verdicts and leave the reported rate where it was, when it
moves as many one way as the other. The shift in the positive rate is therefore reported and
tested separately from the effect.

The repeats must be independent runs. A judge that is cached, or sampled with a pinned seed,
repeats itself exactly, so the repeat flip rate reads zero and its sampling noise is counted
as a presentation effect, because a change of prompt redraws the sample.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from scipy.stats import t as student_t

from pyrit.memory import CentralMemory
from pyrit.models import MessageScorable, ScoringExpectation, UndeterminedScoreError
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target.batch_helper import batch_task_async
from pyrit.score.scorer_evaluation.krippendorff import krippendorff_alpha
from pyrit.score.scorer_evaluation.metrics_type import MetricsType
from pyrit.score.scorer_evaluation.scorer_metrics import non_finite_to_none
from pyrit.score.true_false.multi_label_true_false_scorer import MultiLabelTrueFalseScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.converter import Converter
    from pyrit.models import Message, Score
    from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset, ObjectiveHumanLabeledEntry

__all__ = [
    "REFERENCE_PRESENTATION",
    "PresentationEffect",
    "PresentationKind",
    "ScorerPresentation",
    "ScorerStabilityEvaluator",
    "ScorerStabilityMetrics",
    "compute_scorer_stability",
]

#: The name the scorer under test is recorded under, presented the way it is normally used.
REFERENCE_PRESENTATION = "reference"

# Permutations are drawn in blocks so the working array stays small for large datasets.
_PERMUTATION_BLOCK = 200
# Statistics are means of a few simple fractions, so equal values can differ in the last bits.
_TIE_TOLERANCE = 1e-12


class PresentationKind(str, Enum):
    """What a presentation changes about the way the question is asked."""

    #: The order in which the answer options are listed.
    OPTION_ORDER = "option_order"
    #: The words used for an option or a field, such as "refuses" for "declines".
    LABEL_WORDING = "label_wording"
    #: The same criterion written another way.
    RUBRIC_PARAPHRASE = "rubric_paraphrase"
    #: The order of the fields the scorer is shown, such as the objective before the response.
    FIELD_ORDER = "field_order"
    #: Formatting that carries no meaning, such as trailing whitespace around the response.
    FORMATTING = "formatting"
    #: Any other change the caller holds to be meaning-preserving.
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class ScorerPresentation:
    """
    One way of putting the same question to a scorer.

    The scorer carries whatever is changed on the scorer's side, such as a system prompt with
    its options in another order. ``response_converters`` change how the response is shown to
    it and work with any scorer. The caller is responsible for the change preserving meaning:
    a presentation that changes the criterion measures a different question, not stability.

    Attributes:
        name (str): A unique name for the presentation.
        scorer (TrueFalseScorer): The scorer, configured for this presentation.
        kind (PresentationKind): What the presentation changes.
        response_converters (Sequence[Converter]): Converters applied, in order, to the response
            before it is scored. The stored original value is left as it was.
    """

    name: str
    scorer: TrueFalseScorer
    kind: PresentationKind = PresentationKind.OTHER
    response_converters: Sequence[Converter] = ()


@dataclass(frozen=True, kw_only=True)
class PresentationEffect:
    """
    How one presentation moved a scorer's verdicts away from the reference presentation.

    Every rate is over the responses that reached a verdict in every run of every presentation,
    and counts each pair of runs, one under each presentation, equally. The p-values are for
    this presentation alone and are not adjusted for the number of presentations compared.

    Attributes:
        name (str): The presentation.
        kind (str | None): What the presentation changes, when recorded.
        num_repeats (int): How many times the presentation scored each response.
        num_undetermined_items (int): Responses with a run under this presentation that reached
            no verdict. They are left out of every rate rather than counted as false.
        positive_rate (float): Share of true verdicts under this presentation. For an objective
            scorer this is the attack success rate the presentation would report.
        positive_rate_shift (float): ``positive_rate`` minus the reference's. This is how far the
            reported rate moves, and it equals ``flip_to_true_rate - flip_to_false_rate``.
        positive_rate_shift_interval (tuple[float, float] | None): Interval for the shift. None
            with fewer than two responses.
        positive_rate_shift_p_value (float | None): Two-sided permutation p-value for a shift this
            far from zero when the presentation moves verdicts in either direction equally. With
            single runs it is the exact sign test on the responses whose verdict changed. None
            when the presentation and the reference ran a different number of times, where a
            response's shift is not symmetric about zero even if the presentation changes nothing.
        flip_rate (float): Share of run pairs whose verdicts differ. It includes the scorer's
            ordinary run-to-run noise.
        flip_to_true_rate (float): Share of run pairs that are false under the reference and
            true under this presentation.
        flip_to_false_rate (float): Share of run pairs that are true under the reference and
            false under this presentation.
        repeat_flip_rate (float | None): Share of run pairs under one presentation whose verdicts
            differ, averaged over the reference and this presentation. This is the noise floor.
            None unless both were run at least twice.
        presentation_effect (float | None): ``flip_rate`` minus ``repeat_flip_rate``: an unbiased
            estimate of the mean squared change in a response's chance of a true verdict. Chance
            can put the estimate slightly below zero, and it is not clipped. None when the noise
            floor was not measured.
        presentation_effect_interval (tuple[float, float] | None): Interval for the effect. It
            can be too narrow when few responses ever change verdict, so use the p-value to
            decide whether an effect is there.
        presentation_effect_p_value (float | None): Permutation p-value for an effect this large
            when the presentation changes no response's chance of a true verdict. None when the
            noise floor was not measured.
        accuracy (float | None): Share of single runs that agree with the gold label, when gold
            labels were supplied.
        accuracy_shift (float | None): ``accuracy`` minus the reference's.
        accuracy_shift_interval (tuple[float, float] | None): Interval for the accuracy shift.
        scorer_identifier_hash (str | None): Hash of the scorer that produced the verdicts, when
            they came from ``ScorerStabilityEvaluator``.
    """

    name: str
    kind: str | None = None
    num_repeats: int
    num_undetermined_items: int
    positive_rate: float
    positive_rate_shift: float
    positive_rate_shift_interval: tuple[float, float] | None
    positive_rate_shift_p_value: float | None
    flip_rate: float
    flip_to_true_rate: float
    flip_to_false_rate: float
    repeat_flip_rate: float | None = None
    presentation_effect: float | None = None
    presentation_effect_interval: tuple[float, float] | None = None
    presentation_effect_p_value: float | None = None
    accuracy: float | None = None
    accuracy_shift: float | None = None
    accuracy_shift_interval: tuple[float, float] | None = None
    scorer_identifier_hash: str | None = None


@dataclass(frozen=True, kw_only=True)
class ScorerStabilityMetrics:
    """
    How stable a true/false scorer's verdicts are across presentations of the same question.

    Attributes:
        reference (str): The presentation the others are compared with.
        reference_scorer_identifier_hash (str | None): Hash of the scorer under test, when the
            verdicts came from ``ScorerStabilityEvaluator``.
        num_input_items (int): Responses supplied.
        num_items (int): Responses that reached a verdict in every run of every presentation.
            Every rate is over these.
        num_repeats (int): How many times the reference scored each response.
        confidence_level (float): The level of every interval.
        num_permutations (int): Random reassignments behind every p-value. The smallest p-value
            that can be reported is ``1 / (num_permutations + 1)``.
        reference_num_undetermined_items (int): Responses with a reference run that reached no
            verdict.
        reference_positive_rate (float): Share of true verdicts under the reference.
        reference_repeat_flip_rate (float | None): Share of pairs of reference runs whose verdicts
            differ. None when the reference ran once.
        reference_accuracy (float | None): Share of single reference runs that agree with the gold
            label, when gold labels were supplied.
        positive_rate_range (tuple[float, float]): Lowest and highest ``positive_rate`` over the
            reference and every presentation: the range of rates the same scorer reports on the
            same responses. The width grows with the number of presentations, so judge a single
            presentation by its own ``positive_rate_shift``.
        accuracy_range (tuple[float, float] | None): Lowest and highest accuracy over the same
            presentations, when gold labels were supplied.
        consistent_rate (float): Share of responses given one verdict by every run of every
            presentation. It falls as runs are added, so compare it only between evaluations
            with the same presentations and repeats.
        accuracy_on_consistent_items (float | None): Share of those responses whose verdict agrees
            with the gold label. None without gold labels or when no response is consistent.
        accuracy_on_inconsistent_items (float | None): Share of single runs that agree with the
            gold label on the remaining responses. None without gold labels or when every
            response is consistent.
        krippendorff_alpha_runs (float | None): Krippendorff's alpha with every run of every
            presentation as a rater. None when undefined.
        krippendorff_alpha_repeats (float | None): Krippendorff's alpha between the reference's own
            runs, for comparison. None when the reference ran once or it is undefined.
        effects (list[PresentationEffect]): One entry per presentation, in the order given.
        verdicts (dict[str, np.ndarray]): Every recorded verdict, by presentation, as an array of
            shape ``(num_repeats, num_input_items)`` holding 1.0, 0.0, or NaN for no verdict.
        gold_labels (list[bool] | None): The gold label of each response, when supplied.
        dataset_name (str | None): Name of the dataset, when the verdicts came from one.
        dataset_version (str | None): Version of the dataset, when the verdicts came from one.
    """

    reference: str
    reference_scorer_identifier_hash: str | None = None
    num_input_items: int
    num_items: int
    num_repeats: int
    confidence_level: float
    num_permutations: int
    reference_num_undetermined_items: int
    reference_positive_rate: float
    reference_repeat_flip_rate: float | None = None
    reference_accuracy: float | None = None
    positive_rate_range: tuple[float, float]
    accuracy_range: tuple[float, float] | None = None
    consistent_rate: float
    accuracy_on_consistent_items: float | None = None
    accuracy_on_inconsistent_items: float | None = None
    krippendorff_alpha_runs: float | None = None
    krippendorff_alpha_repeats: float | None = None
    effects: list[PresentationEffect] = field(default_factory=list)
    verdicts: dict[str, np.ndarray] = field(default_factory=dict)
    gold_labels: list[bool] | None = None
    dataset_name: str | None = None
    dataset_version: str | None = None

    def get_effect(self, *, name: str) -> PresentationEffect:
        """
        Return the effect of one presentation.

        Args:
            name (str): The presentation's name.

        Returns:
            PresentationEffect: Its effect.

        Raises:
            KeyError: If no presentation has that name.
        """
        for effect in self.effects:
            if effect.name == name:
                return effect
        raise KeyError(f"No presentation named '{name}'.")

    def to_json(self) -> str:
        """
        Serialize the metrics and every recorded verdict to a JSON string.

        Returns:
            str: The JSON string. A missing verdict or an undefined statistic is written as
                ``null``, because JSON has no literal for NaN.
        """
        return json.dumps(non_finite_to_none(asdict(self)), allow_nan=False)


def _as_verdict_matrix(*, name: str, runs: Any) -> np.ndarray:
    """
    Read one presentation's runs as a float matrix with NaN for a missing verdict.

    Args:
        name (str): The presentation, for error messages.
        runs (Any): Rows of verdicts, one row per run: true, false, or None / NaN for no verdict.

    Returns:
        np.ndarray: An array of shape ``(num_repeats, num_items)`` holding 1.0, 0.0 or NaN.

    Raises:
        ValueError: If the runs are not a non-empty matrix of verdicts.
    """
    try:
        matrix = np.array(
            [[np.nan if verdict is None else verdict for verdict in run] for run in runs], dtype=np.float64
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Presentation '{name}' must hold rows of true/false verdicts of equal length.") from exc
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError(f"Presentation '{name}' must hold at least one run of at least one verdict.")
    if not np.all(np.isnan(matrix) | (matrix == 0.0) | (matrix == 1.0)):
        raise ValueError(f"Presentation '{name}' holds a value that is not true, false or missing.")
    return matrix


def _repeat_disagreement(*, true_counts: np.ndarray, num_repeats: int) -> np.ndarray | None:
    """
    Estimate, for each response, the chance that two runs under one presentation differ.

    With ``m`` true verdicts in ``k`` runs, ``2m(k - m) / (k(k - 1))`` is the share of ordered
    pairs of distinct runs that differ, which is unbiased for ``2p(1 - p)``.

    Args:
        true_counts (np.ndarray): True verdicts per response. A leading axis of permutations is
            carried through.
        num_repeats (int): Runs per response.

    Returns:
        np.ndarray | None: The per-response estimate, or None when there is a single run.
    """
    if num_repeats < 2:
        return None
    return 2.0 * true_counts * (num_repeats - true_counts) / (num_repeats * (num_repeats - 1))


def _flips(
    *, reference_counts: np.ndarray, counts: np.ndarray, reference_repeats: int, repeats: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """
    Compute, for each response, how its runs under two presentations disagree.

    Args:
        reference_counts (np.ndarray): True verdicts per response under the reference.
        counts (np.ndarray): True verdicts per response under the presentation.
        reference_repeats (int): Runs per response under the reference.
        repeats (int): Runs per response under the presentation.

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray | None]: The share of run pairs that go from
            false to true, the share that go from true to false, and the repeat noise, which is
            None unless both presentations ran at least twice.
    """
    reference_share = reference_counts / reference_repeats
    share = counts / repeats
    to_true = (1.0 - reference_share) * share
    to_false = reference_share * (1.0 - share)
    reference_within = _repeat_disagreement(true_counts=reference_counts, num_repeats=reference_repeats)
    within = _repeat_disagreement(true_counts=counts, num_repeats=repeats)
    noise = (reference_within + within) / 2.0 if reference_within is not None and within is not None else None
    return to_true, to_false, noise


def _interval(*, contributions: np.ndarray, confidence_level: float) -> tuple[float, float] | None:
    """
    Compute a Student t interval for the mean of per-response contributions.

    Responses are the unit, so a statistic built from several runs of one response keeps those
    runs together.

    Args:
        contributions (np.ndarray): One value per response; the statistic is their mean.
        confidence_level (float): The level of the interval.

    Returns:
        tuple[float, float] | None: The lower and upper limits, or None with fewer than two
            responses.
    """
    num_items = contributions.size
    if num_items < 2:
        return None
    mean = float(contributions.mean())
    half_width = float(
        student_t.ppf((1.0 + confidence_level) / 2.0, num_items - 1) * contributions.std(ddof=1) / np.sqrt(num_items)
    )
    return mean - half_width, mean + half_width


def _permutation_p_values(
    *,
    reference_counts: np.ndarray,
    counts: np.ndarray,
    reference_repeats: int,
    repeats: int,
    num_permutations: int,
    rng: np.random.Generator,
) -> tuple[float | None, float | None]:
    """
    Test the shift in the positive rate and the presentation effect by permutation.

    The effect is tested against a presentation that changes no response's chance of a true
    verdict. The runs of a response are then interchangeable between the reference and the
    presentation, and given how many of them are true, the number that land in the reference's
    runs is hypergeometric, which is drawn here for every response at once.

    The shift is tested against a presentation that moves verdicts in either direction equally,
    by reversing each response's shift at random. This is the weaker claim that the reported
    rate does not move, which can hold while verdicts change, and with single runs it is the
    exact sign test on the responses whose verdict changed. It needs the same number of runs
    under both: with one run against many, a rarely true verdict gives mostly small shifts
    one way and a few large ones the other, so reversing them is not a fair comparison.

    Args:
        reference_counts (np.ndarray): True verdicts per response under the reference.
        counts (np.ndarray): True verdicts per response under the presentation.
        reference_repeats (int): Runs per response under the reference.
        repeats (int): Runs per response under the presentation.
        num_permutations (int): Random permutations to draw for each test.
        rng (np.random.Generator): Source of the permutations.

    Returns:
        tuple[float | None, float | None]: The two-sided p-value for the shift in the positive
            rate, which is None when the run counts differ, and the p-value for the presentation
            effect, which is None when the noise floor was not measured.
    """
    to_true, to_false, noise = _flips(
        reference_counts=reference_counts, counts=counts, reference_repeats=reference_repeats, repeats=repeats
    )
    test_shift = reference_repeats == repeats
    shifts = to_true - to_false
    shifts = shifts[shifts != 0.0]
    observed_shift = abs(float(shifts.sum()))
    observed_effect = float((to_true + to_false - noise).sum()) if noise is not None else None

    # A response whose runs all agree reads the same under every reassignment and adds nothing.
    pooled = (reference_counts + counts).astype(np.int64)
    total = reference_repeats + repeats
    pooled = pooled[(pooled > 0) & (pooled < total)]
    shift_hits = 0
    effect_hits = 0
    for start in range(0, num_permutations, _PERMUTATION_BLOCK):
        block = min(_PERMUTATION_BLOCK, num_permutations - start)
        if test_shift:
            signs = rng.integers(0, 2, size=(block, shifts.size)) * 2 - 1
            shift_hits += int(np.count_nonzero(np.abs((signs * shifts).sum(axis=1)) >= observed_shift - _TIE_TOLERANCE))
        if observed_effect is None:
            continue
        drawn_reference = (
            rng.hypergeometric(pooled, total - pooled, reference_repeats, size=(block, pooled.size))
            if pooled.size
            else np.zeros((block, 0), dtype=np.int64)
        )
        drawn_to_true, drawn_to_false, drawn_noise = _flips(
            reference_counts=drawn_reference,
            counts=pooled - drawn_reference,
            reference_repeats=reference_repeats,
            repeats=repeats,
        )
        drawn_effect = (drawn_to_true + drawn_to_false - cast("np.ndarray", drawn_noise)).sum(axis=1)
        effect_hits += int(np.count_nonzero(drawn_effect >= observed_effect - _TIE_TOLERANCE))
    shift_p_value = (1 + shift_hits) / (1 + num_permutations) if test_shift else None
    effect_p_value = (1 + effect_hits) / (1 + num_permutations) if observed_effect is not None else None
    return shift_p_value, effect_p_value


def _alpha(*, runs: np.ndarray) -> float | None:
    """
    Compute Krippendorff's alpha between runs, or None when it is undefined.

    Args:
        runs (np.ndarray): Verdicts of shape ``(num_runs, num_items)``.

    Returns:
        float | None: The alpha, or None when it is undefined, as it is for a single run.
    """
    alpha = float(krippendorff_alpha(reliability_data=runs, level_of_measurement="ordinal"))
    return alpha if np.isfinite(alpha) else None


def _validate_settings(*, confidence_level: float, num_permutations: int, seed: int) -> None:
    """
    Reject settings that cannot be used, before any scoring is paid for.

    Args:
        confidence_level (float): The level of each interval.
        num_permutations (int): Random reassignments for each p-value.
        seed (int): Seed for the reassignments.

    Raises:
        ValueError: If a setting is out of range.
    """
    if num_permutations < 1:
        raise ValueError("num_permutations must be at least 1.")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1.")
    if seed < 0:
        raise ValueError("seed must not be negative.")


def compute_scorer_stability(
    *,
    verdicts: Mapping[str, Any],
    reference: str = REFERENCE_PRESENTATION,
    gold_labels: Sequence[bool] | None = None,
    kinds: Mapping[str, PresentationKind | str] | None = None,
    confidence_level: float = 0.95,
    num_permutations: int = 2000,
    seed: int = 0,
) -> ScorerStabilityMetrics:
    """
    Compute stability metrics from recorded verdicts, without calling a scorer.

    Use this to analyse verdicts recorded earlier or by another tool. Each presentation maps to
    its runs, one row per run and one column per response, in the same response order for every
    presentation. Presentations may have different numbers of runs.

    Args:
        verdicts (Mapping[str, Any]): Runs by presentation name. A verdict is true, false, or
            None / NaN when the run reached no verdict.
        reference (str): The presentation the others are compared with. Defaults to "reference".
        gold_labels (Sequence[bool] | None): The gold label of each response. Defaults to None,
            which leaves the accuracy fields empty.
        kinds (Mapping[str, PresentationKind | str] | None): What each presentation changes, for
            the record. Defaults to None.
        confidence_level (float): The level of each interval. Defaults to 0.95.
        num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
        seed (int): Non-negative seed for the reassignments, so the p-values can be reproduced.
            Defaults to 0.

    Returns:
        ScorerStabilityMetrics: The metrics, with the verdicts they were computed from.

    Raises:
        ValueError: If the reference or any other presentation is missing, the runs do not line
            up, a setting is out of range, or no response reached a verdict in every run.
    """
    if reference not in verdicts:
        raise ValueError(f"The reference presentation '{reference}' has no verdicts.")
    if len(verdicts) < 2:
        raise ValueError("At least one presentation besides the reference is required.")
    _validate_settings(confidence_level=confidence_level, num_permutations=num_permutations, seed=seed)

    matrices = {name: _as_verdict_matrix(name=name, runs=runs) for name, runs in verdicts.items()}
    num_input_items = matrices[reference].shape[1]
    for name, matrix in matrices.items():
        if matrix.shape[1] != num_input_items:
            raise ValueError(
                f"Presentation '{name}' has {matrix.shape[1]} responses, but the reference has {num_input_items}."
            )
    gold: np.ndarray | None = None
    if gold_labels is not None:
        gold = np.asarray(list(gold_labels), dtype=bool).astype(np.float64)
        if gold.shape != (num_input_items,):
            raise ValueError(f"gold_labels has {gold.size} labels for {num_input_items} responses.")

    # A run that reached no verdict says nothing about which way it would have gone, so the
    # response is left out everywhere rather than counted as false under one presentation.
    undetermined = {name: np.isnan(matrix).any(axis=0) for name, matrix in matrices.items()}
    kept = ~np.any(list(undetermined.values()), axis=0)
    num_items = int(kept.sum())
    if num_items == 0:
        raise ValueError("No response reached a verdict in every run of every presentation.")

    runs = {name: matrix[:, kept] for name, matrix in matrices.items()}
    if gold is not None:
        gold = gold[kept]
    true_counts = {name: matrix.sum(axis=0) for name, matrix in runs.items()}
    repeats = {name: matrix.shape[0] for name, matrix in runs.items()}
    true_share = {name: true_counts[name] / repeats[name] for name in runs}
    # Share of a response's runs that agree with its gold label.
    correct_share = (
        {name: np.where(gold == 1.0, true_share[name], 1.0 - true_share[name]) for name in runs}
        if gold is not None
        else None
    )

    effects: list[PresentationEffect] = []
    for name in runs:
        if name == reference:
            continue
        # Seeded by the presentation's name as well, so its p-values do not depend on which
        # other presentations are compared or in what order.
        rng = np.random.default_rng([seed, *name.encode("utf-8")])
        to_true, to_false, noise = _flips(
            reference_counts=true_counts[reference],
            counts=true_counts[name],
            reference_repeats=repeats[reference],
            repeats=repeats[name],
        )
        shift_p_value, effect_p_value = _permutation_p_values(
            reference_counts=true_counts[reference],
            counts=true_counts[name],
            reference_repeats=repeats[reference],
            repeats=repeats[name],
            num_permutations=num_permutations,
            rng=rng,
        )
        effect_contributions = to_true + to_false - noise if noise is not None else None
        accuracy_contributions = correct_share[name] - correct_share[reference] if correct_share is not None else None
        kind = kinds.get(name) if kinds is not None else None
        effects.append(
            PresentationEffect(
                name=name,
                kind=kind.value if isinstance(kind, PresentationKind) else kind,
                num_repeats=repeats[name],
                num_undetermined_items=int(undetermined[name].sum()),
                positive_rate=float(true_share[name].mean()),
                positive_rate_shift=float((to_true - to_false).mean()),
                positive_rate_shift_interval=_interval(
                    contributions=to_true - to_false, confidence_level=confidence_level
                ),
                positive_rate_shift_p_value=shift_p_value,
                flip_rate=float((to_true + to_false).mean()),
                flip_to_true_rate=float(to_true.mean()),
                flip_to_false_rate=float(to_false.mean()),
                repeat_flip_rate=float(noise.mean()) if noise is not None else None,
                presentation_effect=(float(effect_contributions.mean()) if effect_contributions is not None else None),
                presentation_effect_interval=(
                    _interval(contributions=effect_contributions, confidence_level=confidence_level)
                    if effect_contributions is not None
                    else None
                ),
                presentation_effect_p_value=effect_p_value,
                accuracy=float(correct_share[name].mean()) if correct_share is not None else None,
                accuracy_shift=(float(accuracy_contributions.mean()) if accuracy_contributions is not None else None),
                accuracy_shift_interval=(
                    _interval(contributions=accuracy_contributions, confidence_level=confidence_level)
                    if accuracy_contributions is not None
                    else None
                ),
            )
        )

    all_runs = np.concatenate(list(runs.values()), axis=0)
    consistent = np.all(all_runs == all_runs[0], axis=0)
    accuracy_on_consistent: float | None = None
    accuracy_on_inconsistent: float | None = None
    accuracy_range: tuple[float, float] | None = None
    if gold is not None and correct_share is not None:
        accuracies = [float(share.mean()) for share in correct_share.values()]
        accuracy_range = (min(accuracies), max(accuracies))
        if consistent.any():
            accuracy_on_consistent = float(np.mean(all_runs[0, consistent] == gold[consistent]))
        if not consistent.all():
            accuracy_on_inconsistent = float(np.mean(all_runs[:, ~consistent] == gold[~consistent]))
    positive_rates = [float(share.mean()) for share in true_share.values()]
    reference_within = _repeat_disagreement(true_counts=true_counts[reference], num_repeats=repeats[reference])

    return ScorerStabilityMetrics(
        reference=reference,
        num_input_items=num_input_items,
        num_items=num_items,
        num_repeats=repeats[reference],
        confidence_level=confidence_level,
        num_permutations=num_permutations,
        reference_num_undetermined_items=int(undetermined[reference].sum()),
        reference_positive_rate=float(true_share[reference].mean()),
        reference_repeat_flip_rate=float(reference_within.mean()) if reference_within is not None else None,
        reference_accuracy=float(correct_share[reference].mean()) if correct_share is not None else None,
        positive_rate_range=(min(positive_rates), max(positive_rates)),
        accuracy_range=accuracy_range,
        consistent_rate=float(consistent.mean()),
        accuracy_on_consistent_items=accuracy_on_consistent,
        accuracy_on_inconsistent_items=accuracy_on_inconsistent,
        krippendorff_alpha_runs=_alpha(runs=all_runs),
        krippendorff_alpha_repeats=_alpha(runs=runs[reference]),
        effects=effects,
        verdicts=matrices,
        gold_labels=[bool(label) for label in gold_labels] if gold_labels is not None else None,
    )


class ScorerStabilityEvaluator:
    """
    Score the same responses under several presentations and report how the verdicts move.

    The scorer under test is the reference. Each presentation is scored on its own stored copy
    of every conversation, and every presentation is scored once before any is scored again, so
    a change in the judge model over time falls on all of them alike.
    """

    def __init__(self, *, scorer: TrueFalseScorer, presentations: Sequence[ScorerPresentation]) -> None:
        """
        Initialize the evaluator.

        Args:
            scorer (TrueFalseScorer): The scorer under test, as it is normally used.
            presentations (Sequence[ScorerPresentation]): Other ways of putting the same question.

        Raises:
            ValueError: If there is no presentation, a name is repeated or is the reference's, or
                a scorer is not a single-verdict true/false scorer.
        """
        if not presentations:
            raise ValueError("At least one presentation is required.")
        names = [presentation.name for presentation in presentations]
        if REFERENCE_PRESENTATION in names or len(set(names)) != len(names):
            raise ValueError(f"Presentation names must be unique and must not be '{REFERENCE_PRESENTATION}'.")
        self._presentations = [ScorerPresentation(name=REFERENCE_PRESENTATION, scorer=scorer), *presentations]
        for presentation in self._presentations:
            if isinstance(presentation.scorer, MultiLabelTrueFalseScorer):
                raise ValueError("Select a label with TrueFalseScoreSelector before evaluating a multi-label scorer.")
            if not isinstance(presentation.scorer, TrueFalseScorer):
                raise ValueError(f"Presentation '{presentation.name}' must use a TrueFalseScorer.")

    async def evaluate_dataset_async(
        self,
        *,
        labeled_dataset: HumanLabeledDataset,
        num_repeats: int = 2,
        max_concurrency: int = 10,
        confidence_level: float = 0.95,
        num_permutations: int = 2000,
        seed: int = 0,
    ) -> ScorerStabilityMetrics:
        """
        Evaluate stability on an objective human-labeled dataset.

        The gold label of a response is the majority of its human labels, with a split vote
        counted as false, as in ``ObjectiveScorerEvaluator``.

        Args:
            labeled_dataset (HumanLabeledDataset): An OBJECTIVE dataset whose entries each hold
                one assistant message.
            num_repeats (int): Times each presentation scores each response. Defaults to 2, the
                fewest that measure the scorer's run-to-run noise.
            max_concurrency (int): Maximum concurrent scoring requests. Defaults to 10.
            confidence_level (float): The level of each interval. Defaults to 0.95.
            num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
            seed (int): Seed for the reassignments. Defaults to 0.

        Returns:
            ScorerStabilityMetrics: The metrics, with every recorded verdict.

        Raises:
            ValueError: If the dataset is not an OBJECTIVE dataset.
        """
        if labeled_dataset.metrics_type != MetricsType.OBJECTIVE:
            raise ValueError("The HumanLabeledDataset must be of type OBJECTIVE to evaluate scorer stability.")
        labeled_dataset.validate()
        entries = [cast("ObjectiveHumanLabeledEntry", entry) for entry in labeled_dataset.entries]
        metrics = await self.evaluate_conversations_async(
            conversations=[entry.conversation for entry in entries],
            expectations=[ScoringExpectation(objective=entry.objective) for entry in entries],
            gold_labels=[
                bool(np.round(np.median([float(score) for score in entry.human_scores]))) for entry in entries
            ],
            num_repeats=num_repeats,
            max_concurrency=max_concurrency,
            confidence_level=confidence_level,
            num_permutations=num_permutations,
            seed=seed,
        )
        return replace(metrics, dataset_name=labeled_dataset.name, dataset_version=labeled_dataset.version)

    async def evaluate_conversations_async(
        self,
        *,
        conversations: Sequence[Sequence[Message]],
        expectations: Sequence[ScoringExpectation],
        gold_labels: Sequence[bool] | None = None,
        num_repeats: int = 2,
        max_concurrency: int = 10,
        confidence_level: float = 0.95,
        num_permutations: int = 2000,
        seed: int = 0,
    ) -> ScorerStabilityMetrics:
        """
        Evaluate stability on conversations, with or without gold labels.

        Stability needs no labels, so this also runs on the responses of an attack run. Each
        conversation must hold exactly one assistant message, which is the response scored; the
        other messages give context to scorers that read the conversation. For a scorer that
        judges against an objective, pass ``ScoringExpectation(objective=...)`` for each response.

        Args:
            conversations (Sequence[Sequence[Message]]): The conversations to score.
            expectations (Sequence[ScoringExpectation]): What each response is scored against:
                its objective, and any conditions the scorers need. Each is passed to every
                presentation's scorer as it is.
            gold_labels (Sequence[bool] | None): The gold label of each response. Defaults to None.
            num_repeats (int): Times each presentation scores each response. Defaults to 2, the
                fewest that measure the scorer's run-to-run noise.
            max_concurrency (int): Maximum concurrent scoring requests. Defaults to 10.
            confidence_level (float): The level of each interval. Defaults to 0.95.
            num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
            seed (int): Seed for the reassignments. Defaults to 0.

        Returns:
            ScorerStabilityMetrics: The metrics, with every recorded verdict.

        Raises:
            ValueError: If there is no conversation, the inputs do not line up, a conversation
                does not hold exactly one assistant message, or a setting is out of range. Settings
                are checked before anything is stored or scored.
        """
        _validate_settings(confidence_level=confidence_level, num_permutations=num_permutations, seed=seed)
        if num_repeats < 1:
            raise ValueError("num_repeats must be at least 1.")
        if not conversations:
            raise ValueError("At least one conversation is required.")
        if len(expectations) != len(conversations):
            raise ValueError(f"{len(expectations)} expectations were given for {len(conversations)} conversations.")
        if gold_labels is not None and len(gold_labels) != len(conversations):
            raise ValueError(f"{len(gold_labels)} gold labels were given for {len(conversations)} conversations.")

        responses: dict[str, list[Message]] = {}
        for presentation in self._presentations:
            responses[presentation.name] = [
                await self._store_presented_copy_async(conversation=conversation, presentation=presentation)
                for conversation in conversations
            ]
        verdicts: dict[str, list[list[float]]] = {presentation.name: [] for presentation in self._presentations}
        for _ in range(num_repeats):
            for presentation in self._presentations:
                verdicts[presentation.name].append(
                    await self._score_run_async(
                        scorer=presentation.scorer,
                        responses=responses[presentation.name],
                        expectations=expectations,
                        max_concurrency=max_concurrency,
                    )
                )

        metrics = compute_scorer_stability(
            verdicts=verdicts,
            gold_labels=gold_labels,
            kinds={presentation.name: presentation.kind for presentation in self._presentations[1:]},
            confidence_level=confidence_level,
            num_permutations=num_permutations,
            seed=seed,
        )
        hashes = {presentation.name: presentation.scorer.get_identifier().hash for presentation in self._presentations}
        return replace(
            metrics,
            reference_scorer_identifier_hash=hashes[REFERENCE_PRESENTATION],
            effects=[replace(effect, scorer_identifier_hash=hashes[effect.name]) for effect in metrics.effects],
        )

    @staticmethod
    async def _store_presented_copy_async(
        *, conversation: Sequence[Message], presentation: ScorerPresentation
    ) -> Message:
        """
        Store a copy of a conversation as one presentation shows it, and return its response.

        Each presentation scores its own copy, so its scores attach to its own messages and a
        converted response never replaces what another presentation reads.

        Args:
            conversation (Sequence[Message]): The conversation to copy.
            presentation (ScorerPresentation): The presentation whose converters are applied.

        Returns:
            Message: The copy's assistant message.

        Raises:
            ValueError: If the conversation does not hold exactly one assistant message.
        """
        conversation_id = str(uuid.uuid4())
        copies: list[Message] = []
        for message in conversation:
            copy = message.duplicate()
            for piece in copy.message_pieces:
                piece.conversation_id = conversation_id
            copies.append(copy)
        assistant_messages = [copy for copy in copies if copy.api_role == "assistant"]
        if len(assistant_messages) != 1:
            raise ValueError(
                f"Each conversation must contain exactly one assistant message, but found {len(assistant_messages)}."
            )
        if presentation.response_converters:
            await PromptNormalizer().convert_values_async(
                converter_configurations=ConverterConfiguration.from_converters(
                    converters=list(presentation.response_converters)
                ),
                message=assistant_messages[0],
            )
        memory = CentralMemory.get_memory_instance()
        for copy in copies:
            await memory.add_message_to_memory_async(request=copy)
        return assistant_messages[0]

    @staticmethod
    async def _score_run_async(
        *,
        scorer: TrueFalseScorer,
        responses: Sequence[Message],
        expectations: Sequence[ScoringExpectation],
        max_concurrency: int,
    ) -> list[float]:
        """
        Score every response once and read each verdict.

        Args:
            scorer (TrueFalseScorer): The scorer for this presentation.
            responses (Sequence[Message]): The responses as this presentation shows them.
            expectations (Sequence[ScoringExpectation]): What each response is scored against.
            max_concurrency (int): Maximum concurrent scoring requests.

        Returns:
            list[float]: 1.0 or 0.0 per response, or NaN when the scorer reached no verdict or
                does not apply to the response.
        """
        results: list[list[Score]] = await batch_task_async(
            task_func=scorer.score_async,
            task_arguments=["scorable", "expectation"],
            prompt_target=scorer.get_chat_target(),
            batch_size=max_concurrency,
            items_to_batch=[
                [MessageScorable.from_message(response) for response in responses],
                list(expectations),
            ],
        )
        run: list[float] = []
        for scores in results:
            # A true/false scorer returns one score, or none when it does not apply.
            try:
                run.append(float(scores[0].get_value()) if scores else float("nan"))
            except UndeterminedScoreError:
                run.append(float("nan"))
        return run
