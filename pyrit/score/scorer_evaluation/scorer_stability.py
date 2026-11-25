# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Measure how much a true/false scorer's verdicts depend on how the question is put to it.

A presentation is one way of asking a scorer the same question: the order its options are
listed in, the wording of a label, a paraphrase of the rubric, or whether the objective comes
before the response. Each presentation is a scorer already configured for it. A scorer that
measures the response should give the same verdict under each of them.

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
scorer. The shift in the positive rate is tested separately, because a presentation can move
as many verdicts one way as the other and leave the reported rate where it was. The repeats
must be independent runs: a cached judge repeats itself, so its sampling noise would read as
a presentation effect.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from pyrit.models import ObjectiveTargetEvaluationIdentifier, ScoringExpectation
from pyrit.score.scorer_evaluation.scorer_evaluator import ObjectiveScorerEvaluator, ScoringTrials
from pyrit.score.scorer_evaluation.scorer_metrics import non_finite_to_none
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models import ComponentIdentifier, Message
    from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset
    from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics

__all__ = [
    "PresentationEffect",
    "ScorerPresentation",
    "ScorerStabilityEvaluator",
    "ScorerStabilityMetrics",
    "compute_scorer_stability",
]

# The name the scorer under test is recorded under, presented the way it is normally used.
_REFERENCE_PRESENTATION = "reference"
# Permutations are drawn in blocks so the working array stays small for large datasets.
_PERMUTATION_BLOCK = 200
# Statistics are means of a few simple fractions, so equal values can differ in the last bits.
_TIE_TOLERANCE = 1e-12


@dataclass(frozen=True, kw_only=True)
class ScorerPresentation:
    """
    One way of putting the same question to a scorer.

    The scorer is configured for the presentation, for example with its options listed in
    another order or its fields in another order. The caller is responsible for the change
    preserving meaning: a presentation that changes the criterion measures a different
    question, not stability.

    Attributes:
        name (str): A unique name for the presentation.
        scorer (TrueFalseScorer): The scorer, configured for this presentation.
    """

    name: str
    scorer: TrueFalseScorer


@dataclass(frozen=True, kw_only=True)
class PresentationEffect:
    """
    How one presentation moved a scorer's verdicts away from the reference presentation.

    Every rate is over the responses that reached a verdict in every run of both this
    presentation and the reference, so another presentation's missing verdicts do not change
    it. The p-values are Holm-adjusted across the presentations of one evaluation.

    Attributes:
        name (str): The presentation.
        eval_hash (str | None): Evaluation hash of the presentation's scorer, when the verdicts
            came from ``ScorerStabilityEvaluator``.
        num_items (int): Responses compared: those with a verdict in every run of both.
        num_undetermined_items (int): Responses with a run under this presentation that reached
            no verdict. They are left out rather than counted as false.
        positive_rate (float): Share of true verdicts under this presentation, over the compared
            responses. For an objective scorer this is the attack success rate it would report.
        positive_rate_shift (float): How far the presentation moves the positive rate from the
            reference's on the same compared responses.
        positive_rate_shift_p_value (float): Holm-adjusted two-sided permutation p-value for a
            shift this far from zero when the presentation moves verdicts in either direction
            equally. With single runs it is the exact sign test on the responses whose verdict
            changed.
        flip_rate (float): Share of run pairs, one under each presentation, whose verdicts
            differ. It includes the scorer's ordinary run-to-run noise.
        repeat_flip_rate (float | None): Share of run pairs under one presentation whose verdicts
            differ, averaged over the reference and this presentation. This is the noise floor.
            None with a single run.
        presentation_effect (float | None): ``flip_rate`` minus ``repeat_flip_rate``: an unbiased
            estimate of the mean squared change in a response's chance of a true verdict. Chance
            can put the estimate slightly below zero, and it is not clipped. None with a single run.
        presentation_effect_p_value (float | None): Holm-adjusted permutation p-value for an
            effect this large when the presentation changes no response's chance of a true
            verdict. None with a single run.
        metrics (ObjectiveScorerMetrics | None): The presentation's scorer metrics against human
            labels, as ``ObjectiveScorerEvaluator`` computes them, when the dataset was labeled.
    """

    name: str
    eval_hash: str | None = None
    num_items: int
    num_undetermined_items: int
    positive_rate: float
    positive_rate_shift: float
    positive_rate_shift_p_value: float
    flip_rate: float
    repeat_flip_rate: float | None = None
    presentation_effect: float | None = None
    presentation_effect_p_value: float | None = None
    metrics: ObjectiveScorerMetrics | None = None


@dataclass(frozen=True, kw_only=True)
class ScorerStabilityMetrics:
    """
    How stable a true/false scorer's verdicts are across presentations of the same question.

    Attributes:
        reference (str): The presentation the others are compared with.
        reference_eval_hash (str | None): Evaluation hash of the scorer under test, when the
            verdicts came from ``ScorerStabilityEvaluator``.
        num_input_items (int): Responses supplied.
        num_repeats (int): How many times every presentation scored each response.
        num_permutations (int): Random reassignments behind every p-value. The smallest p-value
            that can be reported, before adjustment, is ``1 / (num_permutations + 1)``.
        reference_num_undetermined_items (int): Responses with a reference run that reached no
            verdict.
        reference_positive_rate (float): Share of true verdicts under the reference, over the
            responses that reached a verdict in every reference run.
        reference_repeat_flip_rate (float | None): Share of pairs of reference runs whose verdicts
            differ, over the same responses. None with a single run.
        reference_metrics (ObjectiveScorerMetrics | None): The reference scorer's metrics against
            human labels, as ``ObjectiveScorerEvaluator`` computes them, when the dataset was labeled.
        effects (list[PresentationEffect]): One entry per presentation, in the order given.
        verdicts (dict[str, np.ndarray]): Every recorded verdict, by presentation, as an array of
            shape ``(num_repeats, num_input_items)`` holding 1.0, 0.0, or NaN for no verdict.
        dataset_name (str | None): Name of the dataset, when the verdicts came from one.
        dataset_version (str | None): Version of the dataset, when the verdicts came from one.
    """

    reference: str
    reference_eval_hash: str | None = None
    num_input_items: int
    num_repeats: int
    num_permutations: int
    reference_num_undetermined_items: int
    reference_positive_rate: float
    reference_repeat_flip_rate: float | None = None
    reference_metrics: ObjectiveScorerMetrics | None = None
    effects: list[PresentationEffect] = field(default_factory=list)
    verdicts: dict[str, np.ndarray] = field(default_factory=dict)
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
    *, reference_counts: np.ndarray, counts: np.ndarray, num_repeats: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """
    Compute, for each response, how its runs under two presentations disagree.

    Args:
        reference_counts (np.ndarray): True verdicts per response under the reference.
        counts (np.ndarray): True verdicts per response under the presentation.
        num_repeats (int): Runs per response under each.

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray | None]: The share of run pairs that go from
            false to true, the share that go from true to false, and the repeat noise, which is
            None with a single run.
    """
    reference_share = reference_counts / num_repeats
    share = counts / num_repeats
    to_true = (1.0 - reference_share) * share
    to_false = reference_share * (1.0 - share)
    reference_within = _repeat_disagreement(true_counts=reference_counts, num_repeats=num_repeats)
    within = _repeat_disagreement(true_counts=counts, num_repeats=num_repeats)
    noise = (reference_within + within) / 2.0 if reference_within is not None and within is not None else None
    return to_true, to_false, noise


def _permutation_p_values(
    *,
    reference_counts: np.ndarray,
    counts: np.ndarray,
    num_repeats: int,
    num_permutations: int,
    rng: np.random.Generator,
) -> tuple[float, float | None]:
    """
    Test the shift in the positive rate and the presentation effect by permutation.

    The effect is tested against a presentation that changes no response's chance of a true
    verdict. The runs of a response are then interchangeable between the reference and the
    presentation, and given how many of them are true, the number that land in the reference's
    runs is hypergeometric, which is drawn here for every response at once.

    The shift is tested against a presentation that moves verdicts in either direction equally,
    by reversing each response's shift at random. This is the weaker claim that the reported
    rate does not move, which can hold while verdicts change, and with single runs it is the
    exact sign test on the responses whose verdict changed.

    Args:
        reference_counts (np.ndarray): True verdicts per response under the reference.
        counts (np.ndarray): True verdicts per response under the presentation.
        num_repeats (int): Runs per response under each.
        num_permutations (int): Random permutations to draw for each test.
        rng (np.random.Generator): Source of the permutations.

    Returns:
        tuple[float, float | None]: The unadjusted two-sided p-value for the shift in the
            positive rate, and the unadjusted p-value for the presentation effect, which is None
            with a single run.
    """
    to_true, to_false, noise = _flips(reference_counts=reference_counts, counts=counts, num_repeats=num_repeats)
    shifts = to_true - to_false
    shifts = shifts[shifts != 0.0]
    observed_shift = abs(float(shifts.sum()))
    observed_effect = float((to_true + to_false - noise).sum()) if noise is not None else None

    # A response whose runs all agree reads the same under every reassignment and adds nothing.
    pooled = (reference_counts + counts).astype(np.int64)
    total = 2 * num_repeats
    pooled = pooled[(pooled > 0) & (pooled < total)]
    shift_hits = 0
    effect_hits = 0
    for start in range(0, num_permutations, _PERMUTATION_BLOCK):
        block = min(_PERMUTATION_BLOCK, num_permutations - start)
        signs = rng.integers(0, 2, size=(block, shifts.size)) * 2 - 1
        shift_hits += int(np.count_nonzero(np.abs((signs * shifts).sum(axis=1)) >= observed_shift - _TIE_TOLERANCE))
        if observed_effect is None:
            continue
        drawn_reference = (
            rng.hypergeometric(pooled, total - pooled, num_repeats, size=(block, pooled.size))
            if pooled.size
            else np.zeros((block, 0), dtype=np.int64)
        )
        drawn_to_true, drawn_to_false, drawn_noise = _flips(
            reference_counts=drawn_reference, counts=pooled - drawn_reference, num_repeats=num_repeats
        )
        drawn_effect = (drawn_to_true + drawn_to_false - cast("np.ndarray", drawn_noise)).sum(axis=1)
        effect_hits += int(np.count_nonzero(drawn_effect >= observed_effect - _TIE_TOLERANCE))
    shift_p_value = (1 + shift_hits) / (1 + num_permutations)
    effect_p_value = (1 + effect_hits) / (1 + num_permutations) if observed_effect is not None else None
    return shift_p_value, effect_p_value


def _holm(p_values: Sequence[float | None]) -> list[float | None]:
    """
    Adjust p-values for testing several presentations at once, by Holm's step-down method.

    The method holds the chance of any false finding to the level used, whatever the dependence
    between the tests. A None is left in place and not counted.

    Args:
        p_values (Sequence[float | None]): Unadjusted p-values.

    Returns:
        list[float | None]: The adjusted p-values, in the same order.
    """
    tested = sorted((p_value, index) for index, p_value in enumerate(p_values) if p_value is not None)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    for rank, (p_value, index) in enumerate(tested):
        running = max(running, min(1.0, (len(tested) - rank) * p_value))
        adjusted[index] = running
    return adjusted


def _compare(
    *, name: str, reference_runs: np.ndarray, runs: np.ndarray, num_permutations: int, seed: int
) -> PresentationEffect:
    """
    Compare one presentation with the reference on the responses both reached a verdict on.

    Args:
        name (str): The presentation.
        reference_runs (np.ndarray): The reference's verdicts, NaN where missing.
        runs (np.ndarray): The presentation's verdicts, of the same shape.
        num_permutations (int): Random reassignments for each p-value.
        seed (int): Seed for the reassignments.

    Returns:
        PresentationEffect: The effect, with unadjusted p-values.

    Raises:
        ValueError: If no response reached a verdict in every run of both.
    """
    undetermined = np.isnan(runs).any(axis=0)
    compared = ~undetermined & ~np.isnan(reference_runs).any(axis=0)
    if not compared.any():
        raise ValueError(f"No response reached a verdict in every run of both the reference and '{name}'.")
    num_repeats = runs.shape[0]
    reference_counts = reference_runs[:, compared].sum(axis=0)
    counts = runs[:, compared].sum(axis=0)
    to_true, to_false, noise = _flips(reference_counts=reference_counts, counts=counts, num_repeats=num_repeats)
    # Seeded by the presentation's name as well, so its p-values do not depend on which
    # other presentations are compared or in what order.
    shift_p_value, effect_p_value = _permutation_p_values(
        reference_counts=reference_counts,
        counts=counts,
        num_repeats=num_repeats,
        num_permutations=num_permutations,
        rng=np.random.default_rng([seed, *name.encode("utf-8")]),
    )
    return PresentationEffect(
        name=name,
        num_items=int(compared.sum()),
        num_undetermined_items=int(undetermined.sum()),
        positive_rate=float((counts / num_repeats).mean()),
        positive_rate_shift=float((to_true - to_false).mean()),
        positive_rate_shift_p_value=shift_p_value,
        flip_rate=float((to_true + to_false).mean()),
        repeat_flip_rate=float(noise.mean()) if noise is not None else None,
        presentation_effect=float((to_true + to_false - noise).mean()) if noise is not None else None,
        presentation_effect_p_value=effect_p_value,
    )


def _validate_settings(*, num_repeats: int, num_permutations: int, seed: int) -> None:
    """
    Reject settings that cannot be used, before any scoring is paid for.

    Args:
        num_repeats (int): Runs per presentation.
        num_permutations (int): Random reassignments for each p-value.
        seed (int): Seed for the reassignments.

    Raises:
        ValueError: If a setting is out of range.
    """
    if num_repeats < 1:
        raise ValueError("num_repeats must be at least 1.")
    if num_permutations < 1:
        raise ValueError("num_permutations must be at least 1.")
    if seed < 0:
        raise ValueError("seed must not be negative.")


def compute_scorer_stability(
    *,
    verdicts: Mapping[str, Any],
    reference: str = _REFERENCE_PRESENTATION,
    num_permutations: int = 2000,
    seed: int = 0,
) -> ScorerStabilityMetrics:
    """
    Compute stability metrics from recorded verdicts, without calling a scorer.

    Use this to analyse verdicts recorded earlier, such as those ``ScorerStabilityMetrics.to_json``
    writes, or by another tool. Each presentation maps to its runs, one row per run and one column
    per response, in the same response order for every presentation. Every presentation must have
    the same number of runs.

    Args:
        verdicts (Mapping[str, Any]): Runs by presentation name. A verdict is true, false, or
            None / NaN when the run reached no verdict.
        reference (str): The presentation the others are compared with. Defaults to "reference".
        num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
        seed (int): Non-negative seed for the reassignments, so the p-values can be reproduced.
            Defaults to 0.

    Returns:
        ScorerStabilityMetrics: The metrics, with the verdicts they were computed from.

    Raises:
        ValueError: If the reference or any other presentation is missing, the runs do not line
            up, a setting is out of range, or a comparison has no response with a verdict in
            every run.
    """
    if reference not in verdicts:
        raise ValueError(f"The reference presentation '{reference}' has no verdicts.")
    if len(verdicts) < 2:
        raise ValueError("At least one presentation besides the reference is required.")
    matrices = {name: _as_verdict_matrix(name=name, runs=runs) for name, runs in verdicts.items()}
    reference_runs = matrices[reference]
    num_repeats, num_input_items = reference_runs.shape
    _validate_settings(num_repeats=num_repeats, num_permutations=num_permutations, seed=seed)
    for name, matrix in matrices.items():
        if matrix.shape != reference_runs.shape:
            raise ValueError(
                f"Presentation '{name}' has {matrix.shape[0]} runs of {matrix.shape[1]} responses, but the "
                f"reference has {num_repeats} runs of {num_input_items}. Every presentation needs the same runs."
            )

    reference_undetermined = np.isnan(reference_runs).any(axis=0)
    if reference_undetermined.all():
        raise ValueError("No response reached a verdict in every run of the reference.")
    reference_counts = reference_runs[:, ~reference_undetermined].sum(axis=0)
    reference_noise = _repeat_disagreement(true_counts=reference_counts, num_repeats=num_repeats)

    effects = [
        _compare(name=name, reference_runs=reference_runs, runs=runs, num_permutations=num_permutations, seed=seed)
        for name, runs in matrices.items()
        if name != reference
    ]
    shift_p_values = _holm([effect.positive_rate_shift_p_value for effect in effects])
    effect_p_values = _holm([effect.presentation_effect_p_value for effect in effects])

    return ScorerStabilityMetrics(
        reference=reference,
        num_input_items=num_input_items,
        num_repeats=num_repeats,
        num_permutations=num_permutations,
        reference_num_undetermined_items=int(reference_undetermined.sum()),
        reference_positive_rate=float((reference_counts / num_repeats).mean()),
        reference_repeat_flip_rate=float(reference_noise.mean()) if reference_noise is not None else None,
        effects=[
            replace(effect, positive_rate_shift_p_value=cast("float", shift), presentation_effect_p_value=effect_p)
            for effect, shift, effect_p in zip(effects, shift_p_values, effect_p_values, strict=True)
        ],
        verdicts=matrices,
    )


def _judge_settings(identifier: ComponentIdentifier) -> list[str]:
    """
    Return the behavioral settings of every judge model a scorer calls, wrapped scorers included.

    Each is the judge target's evaluation hash, which covers its model, temperature and top_p.

    Args:
        identifier (ComponentIdentifier): The scorer's identifier.

    Returns:
        list[str]: The settings, sorted.
    """
    settings = [
        ObjectiveTargetEvaluationIdentifier(target).eval_hash for target in identifier.get_child_list("prompt_target")
    ]
    for name in identifier.children:
        if name != "prompt_target":
            for child in identifier.get_child_list(name):
                settings.extend(_judge_settings(child))
    return sorted(settings)


class ScorerStabilityEvaluator:
    """
    Score the same responses under several presentations and report how the verdicts move.

    The scorer under test is the reference. Scoring runs in rounds, and each round scores every
    response once under every presentation, so a change in the judge model over time falls on all
    of them alike. Each scorer is scored through its own ``ObjectiveScorerEvaluator``, so on a
    labeled dataset each also gets the metrics that evaluator would report for it.
    """

    def __init__(self, *, scorer: TrueFalseScorer, presentations: Sequence[ScorerPresentation]) -> None:
        """
        Initialize the evaluator.

        Args:
            scorer (TrueFalseScorer): The scorer under test, as it is normally used.
            presentations (Sequence[ScorerPresentation]): Other ways of putting the same question.

        Raises:
            ValueError: If there is no presentation, a name is repeated or is the reference's, a
                scorer is not a single-verdict true/false scorer, two scorers share a configuration,
                or a presentation's judge model settings differ from the reference's.
        """
        if not presentations:
            raise ValueError("At least one presentation is required.")
        names = [presentation.name for presentation in presentations]
        if _REFERENCE_PRESENTATION in names or len(set(names)) != len(names):
            raise ValueError(f"Presentation names must be unique and must not be '{_REFERENCE_PRESENTATION}'.")
        all_presentations = [ScorerPresentation(name=_REFERENCE_PRESENTATION, scorer=scorer), *presentations]
        # The evaluator rejects a multi-label scorer that has no label selected.
        self._evaluators = {
            presentation.name: ObjectiveScorerEvaluator(presentation.scorer) for presentation in all_presentations
        }
        self._validate_presentations(all_presentations)

    async def evaluate_dataset_async(
        self,
        *,
        labeled_dataset: HumanLabeledDataset,
        num_repeats: int = 3,
        max_concurrency: int = 10,
        num_permutations: int = 2000,
        seed: int = 0,
    ) -> ScorerStabilityMetrics:
        """
        Evaluate stability on an objective human-labeled dataset.

        The dataset's messages are added to memory once. Each scorer's metrics against the human
        labels are those ``ObjectiveScorerEvaluator`` reports for it with ``num_repeats`` trials.

        Args:
            labeled_dataset (HumanLabeledDataset): An OBJECTIVE dataset whose entries each hold
                one assistant message.
            num_repeats (int): Times each presentation scores each response. Defaults to 3, the
                number of trials ``Scorer.evaluate_async`` records.
            max_concurrency (int): Maximum concurrent scoring requests. Defaults to 10.
            num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
            seed (int): Seed for the reassignments. Defaults to 0.

        Returns:
            ScorerStabilityMetrics: The metrics, with every recorded verdict.

        Raises:
            ValueError: If a setting is out of range, checked before anything is stored or scored,
                or the dataset is not an OBJECTIVE dataset.
        """
        _validate_settings(num_repeats=num_repeats, num_permutations=num_permutations, seed=seed)
        reference = self._evaluators[_REFERENCE_PRESENTATION]
        responses, human_scores, objectives = await reference._validate_and_extract_data_async(labeled_dataset)
        trials = await self._score_rounds_async(
            responses=responses,
            expectations=[ScoringExpectation(objective=objective) for objective in objectives or []],
            num_repeats=num_repeats,
            max_concurrency=max_concurrency,
        )
        metrics = self._compute_stability(trials=trials, num_permutations=num_permutations, seed=seed)
        all_human_scores = np.array(human_scores).T
        scorer_metrics = {
            name: cast(
                "ObjectiveScorerMetrics",
                self._evaluators[name]._compute_trial_metrics(
                    trials=trials[name], all_human_scores=all_human_scores, labeled_dataset=labeled_dataset
                ),
            )
            for name in trials
        }
        return replace(
            metrics,
            reference_metrics=scorer_metrics[_REFERENCE_PRESENTATION],
            effects=[replace(effect, metrics=scorer_metrics[effect.name]) for effect in metrics.effects],
            dataset_name=labeled_dataset.name,
            dataset_version=labeled_dataset.version,
        )

    async def evaluate_conversations_async(
        self,
        *,
        conversations: Sequence[Sequence[Message]],
        expectations: Sequence[ScoringExpectation],
        num_repeats: int = 3,
        max_concurrency: int = 10,
        num_permutations: int = 2000,
        seed: int = 0,
    ) -> ScorerStabilityMetrics:
        """
        Evaluate stability on conversations already in memory, without labels.

        This also runs on the responses of an attack, which memory already holds. Each conversation
        must hold exactly one assistant message, the response scored, and be stored already, because
        each score refers to the stored response. Nothing is added to memory but the scores.

        Args:
            conversations (Sequence[Sequence[Message]]): The stored conversations to score.
            expectations (Sequence[ScoringExpectation]): What each response is scored against: its
                objective, and any conditions the scorers need. Each is passed to every
                presentation's scorer as it is.
            num_repeats (int): Times each presentation scores each response. Defaults to 3.
            max_concurrency (int): Maximum concurrent scoring requests. Defaults to 10.
            num_permutations (int): Random reassignments for each p-value. Defaults to 2000.
            seed (int): Seed for the reassignments. Defaults to 0.

        Returns:
            ScorerStabilityMetrics: The metrics, with every recorded verdict.

        Raises:
            ValueError: If there is no conversation, the inputs do not line up, a conversation does
                not hold exactly one assistant message or is not in memory, or a setting is out of
                range. All of this is checked before anything is scored.
        """
        _validate_settings(num_repeats=num_repeats, num_permutations=num_permutations, seed=seed)
        if not conversations:
            raise ValueError("At least one conversation is required.")
        if len(expectations) != len(conversations):
            raise ValueError(f"{len(expectations)} expectations were given for {len(conversations)} conversations.")
        trials = await self._score_rounds_async(
            responses=await self._stored_responses_async(conversations=conversations),
            expectations=expectations,
            num_repeats=num_repeats,
            max_concurrency=max_concurrency,
        )
        return self._compute_stability(trials=trials, num_permutations=num_permutations, seed=seed)

    async def _stored_responses_async(self, *, conversations: Sequence[Sequence[Message]]) -> list[Message]:
        """
        Return each conversation's one assistant message, after checking memory holds every message.

        Args:
            conversations (Sequence[Sequence[Message]]): The conversations.

        Returns:
            list[Message]: The responses to score.

        Raises:
            ValueError: If a conversation does not hold exactly one assistant message, or a message
                piece is not in memory.
        """
        responses = []
        for conversation in conversations:
            assistant_messages = [message for message in conversation if message.api_role == "assistant"]
            if len(assistant_messages) != 1:
                raise ValueError(
                    "Each conversation must contain exactly one assistant message, "
                    f"but found {len(assistant_messages)}."
                )
            responses.append(assistant_messages[0])
        piece_ids = {
            str(piece.id)
            for conversation in conversations
            for message in conversation
            for piece in message.message_pieces
        }
        memory = self._evaluators[_REFERENCE_PRESENTATION].scorer._memory
        stored = await memory.get_message_pieces_async(prompt_ids=list(piece_ids))
        missing = piece_ids - {str(piece.id) for piece in stored}
        if missing:
            raise ValueError(
                f"{len(missing)} message pieces of the conversations are not in memory. Store the conversations "
                "before evaluating them, so each score can refer to its response."
            )
        return responses

    async def _score_rounds_async(
        self,
        *,
        responses: list[Message],
        expectations: Sequence[ScoringExpectation],
        num_repeats: int,
        max_concurrency: int,
    ) -> dict[str, ScoringTrials]:
        """
        Score every response once under every presentation, ``num_repeats`` times over.

        Args:
            responses (list[Message]): The responses.
            expectations (Sequence[ScoringExpectation]): What each response is scored against.
            num_repeats (int): The number of rounds.
            max_concurrency (int): Maximum concurrent scoring requests.

        Returns:
            dict[str, ScoringTrials]: Every presentation's trials.
        """
        rounds: dict[str, list[ScoringTrials]] = {name: [] for name in self._evaluators}
        for _ in range(num_repeats):
            for name, evaluator in self._evaluators.items():
                rounds[name].append(
                    await evaluator._run_scoring_trials_async(
                        responses=responses,
                        expectations=expectations,
                        harm_category=None,
                        num_scorer_trials=1,
                        max_concurrency=max_concurrency,
                    )
                )
        return {name: ScoringTrials.concatenate(trials) for name, trials in rounds.items()}

    def _compute_stability(
        self, *, trials: dict[str, ScoringTrials], num_permutations: int, seed: int
    ) -> ScorerStabilityMetrics:
        """
        Compute the stability metrics from every presentation's trials.

        Args:
            trials (dict[str, ScoringTrials]): Every presentation's trials.
            num_permutations (int): Random reassignments for each p-value.
            seed (int): Seed for the reassignments.

        Returns:
            ScorerStabilityMetrics: The metrics, with each scorer's evaluation hash.
        """
        metrics = compute_scorer_stability(
            verdicts={
                name: np.where(trial.undetermined, np.nan, trial.scores.astype(np.float64))
                for name, trial in trials.items()
            },
            num_permutations=num_permutations,
            seed=seed,
        )
        hashes = {name: evaluator.scorer.get_identifier().eval_hash for name, evaluator in self._evaluators.items()}
        return replace(
            metrics,
            reference_eval_hash=hashes[_REFERENCE_PRESENTATION],
            effects=[replace(effect, eval_hash=hashes[effect.name]) for effect in metrics.effects],
        )

    @staticmethod
    def _validate_presentations(presentations: Sequence[ScorerPresentation]) -> None:
        """
        Check that every presentation is a true/false scorer asking differently through the same judge.

        A presentation with the reference's configuration measures nothing, and one whose judge
        uses another model, temperature or top_p would count that change as a presentation effect.

        Args:
            presentations (Sequence[ScorerPresentation]): The reference, then the presentations.

        Raises:
            ValueError: If a scorer is not a true/false scorer, two scorers share an evaluation hash,
                or a judge setting differs.
        """
        for presentation in presentations:
            if not isinstance(presentation.scorer, TrueFalseScorer):
                raise ValueError(f"Presentation '{presentation.name}' must use a TrueFalseScorer.")
        reference_judges = _judge_settings(presentations[0].scorer.get_identifier())
        seen: dict[str | None, str] = {}
        for presentation in presentations:
            identifier = presentation.scorer.get_identifier()
            if identifier.eval_hash in seen:
                raise ValueError(
                    f"Presentations '{seen[identifier.eval_hash]}' and '{presentation.name}' have the same scorer "
                    "configuration, so they do not put the question differently."
                )
            seen[identifier.eval_hash] = presentation.name
            if _judge_settings(identifier) != reference_judges:
                raise ValueError(
                    f"Presentation '{presentation.name}' uses different judge settings (model, temperature or "
                    "top_p) from the reference. Use the same judge settings, or the change reads as a "
                    "presentation effect."
                )
