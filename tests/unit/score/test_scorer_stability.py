# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer stability: the estimates, the checks on the instrument itself, and the evaluator.

The simulations give the instrument scorers whose answer is known in advance: one that is noisy
but indifferent to presentation, and ones with a planted effect of a known size.
"""

import itertools
import json
from collections import Counter
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from scipy.stats import hypergeom
from unit.mocks import get_mock_target_identifier

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.converter import SearchReplaceConverter, SuffixAppendConverter
from pyrit.memory import MemoryInterface
from pyrit.models import AnswerMatches, ComponentIdentifier, Message, MessagePiece, Score, ScoringExpectation
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    HarmHumanLabeledEntry,
    HumanLabeledDataset,
    MessageMultiLabelTrueFalseScorer,
    MessageTrueFalseScorer,
    MetricsType,
    ObjectiveHumanLabeledEntry,
    PresentationKind,
    QuestionAnswerScorer,
    ScorerPresentation,
    ScorerPromptValidator,
    ScorerStabilityEvaluator,
    ScorerStabilityMetrics,
    SelfAskRefusalScorer,
    SubStringScorer,
    compute_scorer_stability,
)

T, F = True, False


def _effect(reference: list[list[bool | None]], presentation: list[list[bool | None]], **kwargs):
    metrics = compute_scorer_stability(verdicts={"reference": reference, "other": presentation}, **kwargs)
    return metrics, metrics.get_effect(name="other")


# ---------------------------------------------------------------------------
# Estimates worked by hand
# ---------------------------------------------------------------------------


class TestEstimates:
    def test_single_runs_report_flips_but_cannot_separate_them_from_noise(self) -> None:
        metrics, effect = _effect([[T, T, F, F]], [[T, F, F, T]])

        assert effect.flip_rate == 0.5
        assert effect.flip_to_true_rate == 0.25
        assert effect.flip_to_false_rate == 0.25
        assert effect.positive_rate_shift == 0.0
        assert effect.repeat_flip_rate is None
        assert effect.presentation_effect is None
        assert effect.presentation_effect_interval is None
        assert effect.presentation_effect_p_value is None
        assert metrics.reference_repeat_flip_rate is None
        assert metrics.krippendorff_alpha_repeats is None

    def test_a_scorer_that_repeats_itself_has_an_effect_equal_to_its_flip_rate(self) -> None:
        _, effect = _effect([[T, T, F, F], [T, T, F, F]], [[T, F, F, F], [T, F, F, F]])

        assert effect.repeat_flip_rate == 0.0
        assert effect.flip_rate == 0.25
        assert effect.presentation_effect == 0.25
        assert effect.flip_to_false_rate == 0.25
        assert effect.positive_rate == 0.25
        assert effect.positive_rate_shift == -0.25

    def test_the_flip_rate_splits_into_repeat_noise_and_presentation_effect(self) -> None:
        # One response: the reference answers true in 1 of 2 runs, the presentation in 2 of 2.
        # Pairs across presentations: 2 of 4 differ. Pairs within: the reference's two runs
        # differ, the presentation's do not, so the noise is (1 + 0) / 2.
        _, effect = _effect([[T], [F]], [[T], [T]])

        assert effect.flip_rate == 0.5
        assert effect.repeat_flip_rate == 0.5
        assert effect.presentation_effect == 0.0
        assert effect.flip_to_true_rate == 0.5
        assert effect.flip_to_false_rate == 0.0

    def test_presentations_may_have_different_numbers_of_runs(self) -> None:
        # Reference 2 of 3 true, presentation 1 of 2 true.
        _, effect = _effect([[T], [T], [F]], [[T], [F]])

        assert effect.num_repeats == 2
        assert effect.flip_to_true_rate == pytest.approx((1 / 3) * (1 / 2))
        assert effect.flip_to_false_rate == pytest.approx((2 / 3) * (1 / 2))
        # Within the reference 2*2*1/(3*2); within the presentation 2*1*1/(2*1).
        assert effect.repeat_flip_rate == pytest.approx((2 / 3 + 1) / 2)
        assert effect.presentation_effect == pytest.approx(0.5 - 5 / 6)
        assert effect.presentation_effect_p_value is not None

    def test_the_shift_test_is_withheld_when_run_counts_differ(self) -> None:
        # One run against many: a rarely true scorer that ignores presentation gives ten small
        # shifts the same way, which reversing signs would call significant.
        rng = np.random.default_rng(1)
        reference = rng.random((1, 10)) < 0.01
        presentation = rng.random((1000, 10)) < 0.01
        assert not reference.any()
        assert presentation.any(axis=0).all()

        _, effect = _effect(reference, presentation)
        _, equal_runs = _effect(presentation[:500], presentation[500:])

        assert effect.positive_rate_shift > 0
        assert effect.positive_rate_shift_interval is not None
        assert effect.positive_rate_shift_p_value is None
        assert equal_runs.positive_rate_shift_p_value is not None

    def test_an_estimate_below_zero_is_reported_as_it_is(self) -> None:
        _, effect = _effect([[T, F], [F, T]], [[T, F], [F, T]])

        assert effect.presentation_effect == -0.5

    def test_equal_flips_each_way_leave_the_reported_rate_where_it_was(self) -> None:
        reference = [[T] * 10 + [F] * 10] * 2
        presentation = [[F] * 3 + [T] * 7 + [T] * 3 + [F] * 7] * 2
        metrics, effect = _effect(reference, presentation)

        assert effect.flip_rate == pytest.approx(0.3)
        assert effect.presentation_effect == pytest.approx(0.3)
        assert effect.positive_rate_shift == 0.0
        assert metrics.positive_rate_range == (0.5, 0.5)
        assert effect.presentation_effect_p_value < 0.01
        assert effect.positive_rate_shift_p_value == 1.0

    def test_one_way_flips_move_the_reported_rate_by_the_flip_rate(self) -> None:
        reference = [[T] * 10 + [F] * 10] * 2
        presentation = [[T] * 16 + [F] * 4] * 2
        metrics, effect = _effect(reference, presentation)

        assert effect.positive_rate_shift == pytest.approx(0.3)
        assert effect.flip_rate == pytest.approx(0.3)
        assert metrics.reference_positive_rate == 0.5
        assert metrics.positive_rate_range == pytest.approx((0.5, 0.8))
        low, high = effect.positive_rate_shift_interval
        assert low < 0.3 < high
        assert low > 0.0

    def test_with_single_runs_the_shift_test_is_the_exact_sign_test(self) -> None:
        # Eight responses change verdict, all from false to true: 2 * 0.5 ** 8 = 0.0078.
        _, effect = _effect([[F] * 8 + [T] * 12], [[T] * 20], num_permutations=20000, seed=3)

        assert effect.positive_rate_shift_p_value == pytest.approx(2 * 0.5**8, abs=0.003)

    def test_the_range_covers_the_reference_and_every_presentation(self) -> None:
        metrics = compute_scorer_stability(
            verdicts={"reference": [[T, T, F, F]], "low": [[T, F, F, F]], "high": [[T, T, T, F]]}
        )

        assert metrics.positive_rate_range == (0.25, 0.75)
        assert [effect.name for effect in metrics.effects] == ["low", "high"]

    def test_agreement_between_runs(self) -> None:
        stable, _ = _effect([[T, T, F, F]] * 2, [[T, T, F, F]] * 2)
        moved, _ = _effect([[T, T, F, F]] * 2, [[T, F, F, T]] * 2)

        assert stable.consistent_rate == 1.0
        assert stable.krippendorff_alpha_runs == 1.0
        assert stable.krippendorff_alpha_repeats == 1.0
        assert moved.consistent_rate == 0.5
        assert moved.krippendorff_alpha_repeats == 1.0
        assert moved.krippendorff_alpha_runs < 0.5


class TestIntervalsAndPValues:
    def test_the_interval_is_a_student_t_interval_over_responses(self) -> None:
        # Shifts of 1, 1, 0, 0: mean 0.5, standard deviation 0.57735, and t(0.975, 3) = 3.18245.
        _, effect = _effect([[F, F, F, T]], [[T, T, F, T]])

        assert effect.positive_rate_shift == 0.5
        assert effect.positive_rate_shift_interval == pytest.approx((0.5 - 0.918687, 0.5 + 0.918687), abs=1e-5)

    def test_a_lower_confidence_level_narrows_the_interval(self) -> None:
        # t(0.95, 3) = 2.35336.
        _, effect = _effect([[F, F, F, T]], [[T, T, F, T]], confidence_level=0.9)

        assert effect.positive_rate_shift_interval == pytest.approx((0.5 - 0.679357, 0.5 + 0.679357), abs=1e-5)

    def test_identical_shifts_give_an_interval_of_no_width(self) -> None:
        _, effect = _effect([[F, F, F]], [[T, T, T]])

        assert effect.positive_rate_shift_interval == (1.0, 1.0)

    def test_the_smallest_p_value_is_one_over_the_permutations_plus_one(self) -> None:
        reference = [[T] * 20 + [F] * 20] * 2
        _, effect = _effect(reference, [[F] * 40] * 2, num_permutations=9)

        assert effect.presentation_effect_p_value == 0.1
        assert effect.positive_rate_shift_p_value == 0.1

    def test_an_unchanged_scorer_has_p_values_of_one(self) -> None:
        _, effect = _effect([[T, F, T]] * 2, [[T, F, T]] * 2)

        assert effect.presentation_effect_p_value == 1.0
        assert effect.positive_rate_shift_p_value == 1.0

    @pytest.mark.parametrize(
        ("reference", "presentation"),
        [
            ([[T, T, F], [T, F, F]], [[F, T, T], [F, T, T], [F, F, T]]),
            ([[T, F, T], [T, F, F]], [[F, T, T], [F, T, F], [T, T, T]]),
            ([[T, F, T, T], [T, F, F, T]], [[F, T, T, T], [F, T, F, F], [F, T, T, F]]),
        ],
        ids=["one_way", "two_way", "four_responses"],
    )
    def test_the_effect_test_matches_exact_enumeration_with_unequal_runs(
        self, reference: list[list[bool]], presentation: list[list[bool]]
    ) -> None:
        # Two reference runs and three runs under the presentation.
        reference, presentation = np.array(reference), np.array(presentation)
        _, effect = _effect(reference, presentation, num_permutations=40000, seed=1)

        # Under no effect, the true verdicts of a response fall in the reference's runs
        # hypergeometrically. Enumerate every outcome and count pairs of runs directly.
        pooled = reference.sum(axis=0) + presentation.sum(axis=0)
        outcomes = [
            [(count, hypergeom.pmf(count, 5, int(total), 2)) for count in range(3) if 0 <= total - count <= 3]
            for total in pooled
        ]
        observed = _effect_by_counting_pairs(reference, presentation)
        exact = 0.0
        for combination in itertools.product(*outcomes):
            drawn_reference = np.array([[index < count for count, _ in combination] for index in range(2)])
            drawn = np.array(
                [
                    [index < total - count for (count, _), total in zip(combination, pooled, strict=True)]
                    for index in range(3)
                ]
            )
            if _effect_by_counting_pairs(drawn_reference, drawn) >= observed - 1e-12:
                exact += float(np.prod([chance for _, chance in combination]))

        assert effect.presentation_effect == pytest.approx(observed)
        assert 0.02 < exact < 0.98
        assert effect.presentation_effect_p_value == pytest.approx(exact, abs=0.01)


def _effect_by_counting_pairs(reference: np.ndarray, presentation: np.ndarray) -> float:
    """Compute the presentation effect the long way, by comparing every pair of runs."""
    per_response = []
    for response in range(reference.shape[1]):
        ours, theirs = reference[:, response], presentation[:, response]
        across = np.mean([first != second for first in ours for second in theirs])
        within = [
            np.mean([runs[i] != runs[j] for i in range(len(runs)) for j in range(len(runs)) if i != j])
            for runs in (ours, theirs)
        ]
        per_response.append(across - np.mean(within))
    return float(np.mean(per_response))


class TestGoldLabels:
    def test_accuracy_moves_with_the_presentation(self) -> None:
        gold = [T, T, F, F]
        metrics, effect = _effect([[T, T, F, F]] * 2, [[T, F, F, T]] * 2, gold_labels=gold)

        assert metrics.reference_accuracy == 1.0
        assert effect.accuracy == 0.5
        assert effect.accuracy_shift == -0.5
        assert metrics.accuracy_range == (0.5, 1.0)
        assert metrics.gold_labels == gold

    def test_accuracy_is_that_of_a_single_run(self) -> None:
        # The reference is right in one of its two runs on the only response.
        metrics, effect = _effect([[T], [F]], [[T], [T]], gold_labels=[T])

        assert metrics.reference_accuracy == 0.5
        assert effect.accuracy == 1.0
        assert effect.accuracy_shift == 0.5
        assert effect.accuracy_shift_interval is None

    def test_accuracy_is_split_by_whether_the_verdict_held(self) -> None:
        # Responses 0 and 1 keep their verdict in every run; 2 and 3 do not.
        metrics, _ = _effect(
            [[T, F, T, F], [T, F, T, F]],
            [[T, F, F, T], [T, F, F, F]],
            gold_labels=[T, T, T, F],
        )

        assert metrics.consistent_rate == 0.5
        assert metrics.accuracy_on_consistent_items == 0.5
        # Response 2 is right in 2 of 4 runs, response 3 in 3 of 4.
        assert metrics.accuracy_on_inconsistent_items == pytest.approx(5 / 8)

    def test_without_gold_labels_the_accuracy_fields_are_empty(self) -> None:
        metrics, effect = _effect([[T, F]], [[F, F]])

        assert metrics.reference_accuracy is None
        assert metrics.accuracy_range is None
        assert metrics.accuracy_on_consistent_items is None
        assert metrics.accuracy_on_inconsistent_items is None
        assert effect.accuracy is None
        assert effect.accuracy_shift is None
        assert effect.accuracy_shift_interval is None

    def test_the_split_is_empty_on_the_side_with_no_responses(self) -> None:
        all_consistent, _ = _effect([[T, F]], [[T, F]], gold_labels=[T, T])
        none_consistent, _ = _effect([[T, F]], [[F, T]], gold_labels=[T, T])

        assert all_consistent.accuracy_on_consistent_items == 0.5
        assert all_consistent.accuracy_on_inconsistent_items is None
        assert none_consistent.accuracy_on_consistent_items is None
        assert none_consistent.accuracy_on_inconsistent_items == 0.5


class TestMissingVerdicts:
    def test_a_run_without_a_verdict_removes_the_response_instead_of_counting_as_false(self) -> None:
        # Counted as false, the third response would look like a flip from true.
        metrics, effect = _effect([[T, F, T], [T, F, T]], [[T, F, None], [T, F, T]], gold_labels=[T, F, T])

        assert metrics.num_input_items == 3
        assert metrics.num_items == 2
        assert metrics.reference_num_undetermined_items == 0
        assert effect.num_undetermined_items == 1
        assert effect.flip_rate == 0.0
        assert effect.positive_rate == 0.5
        assert effect.accuracy == 1.0
        assert np.isnan(metrics.verdicts["other"][0, 2])

    def test_nan_marks_a_missing_verdict_too(self) -> None:
        metrics, effect = _effect(np.array([[1.0, np.nan, 0.0]]), np.array([[1.0, 1.0, 1.0]]))

        assert metrics.num_items == 2
        assert metrics.reference_num_undetermined_items == 1
        assert effect.num_undetermined_items == 0
        assert effect.flip_to_true_rate == 0.5

    def test_no_response_with_every_verdict_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="No response reached a verdict"):
            compute_scorer_stability(verdicts={"reference": [[T, None]], "other": [[None, T]]})


class TestInputValidation:
    @pytest.mark.parametrize(
        ("verdicts", "kwargs", "message"),
        [
            ({"other": [[T]]}, {}, "reference presentation 'reference' has no verdicts"),
            ({"reference": [[T]]}, {}, "At least one presentation besides the reference"),
            ({"reference": [[T, F]], "other": [[T]]}, {}, "'other' has 1 responses, but the reference has 2"),
            ({"reference": [[T, F]], "other": [[T, F], [T]]}, {}, "rows of true/false verdicts of equal length"),
            ({"reference": [[T, F]], "other": [[T, 0.5]]}, {}, "not true, false or missing"),
            ({"reference": [[T, F]], "other": [[T, "yes"]]}, {}, "rows of true/false verdicts of equal length"),
            ({"reference": [[T, F]], "other": []}, {}, "at least one run of at least one verdict"),
            ({"reference": [[]], "other": [[]]}, {}, "at least one run of at least one verdict"),
            ({"reference": [[T, F]], "other": [[T, F]]}, {"gold_labels": [T]}, "gold_labels has 1 labels for 2"),
            ({"reference": [[T]], "other": [[T]]}, {"num_permutations": 0}, "num_permutations must be at least 1"),
            ({"reference": [[T]], "other": [[T]]}, {"confidence_level": 1.0}, "confidence_level must be between"),
            ({"reference": [[T]], "other": [[T]]}, {"confidence_level": 0.0}, "confidence_level must be between"),
            ({"reference": [[T]], "other": [[T]]}, {"seed": -1}, "seed must not be negative"),
        ],
    )
    def test_malformed_input_is_rejected(self, verdicts, kwargs, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            compute_scorer_stability(verdicts=verdicts, **kwargs)

    def test_another_presentation_can_be_the_reference(self) -> None:
        metrics = compute_scorer_stability(verdicts={"a": [[T, T]], "b": [[T, F]]}, reference="b")

        assert metrics.reference == "b"
        assert metrics.get_effect(name="a").positive_rate_shift == 0.5
        with pytest.raises(KeyError, match="No presentation named 'b'"):
            metrics.get_effect(name="b")

    def test_kinds_are_recorded(self) -> None:
        metrics = compute_scorer_stability(
            verdicts={"reference": [[T]], "swapped": [[T]], "reworded": [[T]], "plain": [[T]]},
            kinds={"swapped": PresentationKind.OPTION_ORDER, "reworded": "my_rewording"},
        )

        assert [effect.kind for effect in metrics.effects] == ["option_order", "my_rewording", None]
        assert type(metrics.effects[0].kind) is str


class TestSerialization:
    def test_json_holds_every_verdict_and_writes_missing_values_as_null(self) -> None:
        metrics, _ = _effect([[T, F, None]], [[T, T, T]], gold_labels=[T, F, T])

        document = json.loads(metrics.to_json())

        assert document["verdicts"] == {"reference": [[1.0, 0.0, None]], "other": [[1.0, 1.0, 1.0]]}
        assert document["gold_labels"] == [True, False, True]
        assert document["effects"][0]["presentation_effect"] is None
        assert document["effects"][0]["positive_rate_shift_interval"] == pytest.approx(
            list(metrics.effects[0].positive_rate_shift_interval)
        )
        assert "NaN" not in metrics.to_json()

    def test_recorded_verdicts_reproduce_the_metrics(self) -> None:
        rng = np.random.default_rng(5)
        first = compute_scorer_stability(
            verdicts={"reference": rng.random((3, 40)) < 0.5, "other": rng.random((3, 40)) < 0.6},
            gold_labels=list(rng.random(40) < 0.5),
        )
        document = json.loads(first.to_json())

        again = compute_scorer_stability(verdicts=document["verdicts"], gold_labels=document["gold_labels"])

        assert json.loads(again.to_json()) == document


# ---------------------------------------------------------------------------
# The instrument held to the standard it measures: changes that carry no meaning
# must not change what it reports.
# ---------------------------------------------------------------------------


def _random_case(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    num_items = int(rng.integers(8, 40))
    reference = rng.random((int(rng.integers(2, 5)), num_items)) < rng.random(num_items)
    presentation = rng.random((int(rng.integers(2, 5)), num_items)) < rng.random(num_items)
    return reference, presentation, rng.random(num_items) < 0.5


_POINT_ESTIMATES = (
    "positive_rate",
    "positive_rate_shift",
    "flip_rate",
    "flip_to_true_rate",
    "flip_to_false_rate",
    "repeat_flip_rate",
    "presentation_effect",
    "accuracy",
    "accuracy_shift",
)


def _estimates(effect) -> list[float]:
    return [getattr(effect, name) for name in _POINT_ESTIMATES]


@pytest.mark.parametrize("seed", range(12))
class TestInstrumentInvariance:
    def test_flips_add_up(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)

        assert effect.flip_rate == pytest.approx(effect.flip_to_true_rate + effect.flip_to_false_rate)
        assert effect.positive_rate_shift == pytest.approx(effect.flip_to_true_rate - effect.flip_to_false_rate)
        assert effect.flip_rate == pytest.approx(effect.repeat_flip_rate + effect.presentation_effect)

    def test_the_order_of_responses_does_not_matter(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        order = np.random.default_rng(seed + 100).permutation(reference.shape[1])
        _, effect = _effect(reference, presentation, gold_labels=gold)
        _, shuffled = _effect(reference[:, order], presentation[:, order], gold_labels=gold[order])

        assert _estimates(shuffled) == pytest.approx(_estimates(effect))
        assert shuffled.positive_rate_shift_interval == pytest.approx(effect.positive_rate_shift_interval)
        assert shuffled.presentation_effect_interval == pytest.approx(effect.presentation_effect_interval)

    def test_the_order_of_runs_does_not_matter(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)
        _, reversed_runs = _effect(reference[::-1], presentation[::-1], gold_labels=gold)

        assert _estimates(reversed_runs) == pytest.approx(_estimates(effect))
        assert reversed_runs.presentation_effect_p_value == effect.presentation_effect_p_value
        assert reversed_runs.positive_rate_shift_p_value == effect.positive_rate_shift_p_value

    def test_exchanging_the_two_presentations_reverses_only_the_direction(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)
        _, exchanged = _effect(presentation, reference, gold_labels=gold)

        assert exchanged.flip_rate == pytest.approx(effect.flip_rate)
        assert exchanged.repeat_flip_rate == pytest.approx(effect.repeat_flip_rate)
        assert exchanged.presentation_effect == pytest.approx(effect.presentation_effect)
        assert exchanged.positive_rate_shift == pytest.approx(-effect.positive_rate_shift)
        assert exchanged.accuracy_shift == pytest.approx(-effect.accuracy_shift)
        assert exchanged.flip_to_true_rate == pytest.approx(effect.flip_to_false_rate)

    def test_renaming_true_and_false_reverses_only_the_direction(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)
        _, renamed = _effect(~reference, ~presentation, gold_labels=~gold)

        assert renamed.flip_rate == pytest.approx(effect.flip_rate)
        assert renamed.repeat_flip_rate == pytest.approx(effect.repeat_flip_rate)
        assert renamed.presentation_effect == pytest.approx(effect.presentation_effect)
        assert renamed.positive_rate_shift == pytest.approx(-effect.positive_rate_shift)
        assert renamed.accuracy == pytest.approx(effect.accuracy)
        assert renamed.accuracy_shift == pytest.approx(effect.accuracy_shift)

    def test_scoring_every_response_twice_changes_no_estimate(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)
        _, doubled = _effect(np.tile(reference, 2), np.tile(presentation, 2), gold_labels=np.tile(gold, 2))

        assert _estimates(doubled) == pytest.approx(_estimates(effect))

    def test_another_presentation_in_the_comparison_changes_no_estimate(self, seed: int) -> None:
        reference, presentation, gold = _random_case(seed)
        _, effect = _effect(reference, presentation, gold_labels=gold)
        with_third = compute_scorer_stability(
            verdicts={"reference": reference, "third": ~presentation, "other": presentation}, gold_labels=gold
        )

        other = with_third.get_effect(name="other")
        assert _estimates(other) == pytest.approx(_estimates(effect))
        assert other.presentation_effect_p_value == effect.presentation_effect_p_value
        assert other.positive_rate_shift_p_value == effect.positive_rate_shift_p_value

    def test_the_closed_form_agrees_with_counting_pairs_of_runs(self, seed: int) -> None:
        reference, presentation, _ = _random_case(seed)
        _, effect = _effect(reference, presentation)

        assert effect.presentation_effect == pytest.approx(_effect_by_counting_pairs(reference, presentation))
        assert effect.flip_rate == pytest.approx(
            np.mean([first != second for first in reference for second in presentation])
        )

    def test_the_same_seed_gives_the_same_p_values(self, seed: int) -> None:
        reference, presentation, _ = _random_case(seed)
        _, first = _effect(reference, presentation, seed=11)
        _, second = _effect(reference, presentation, seed=11)

        assert first.presentation_effect_p_value == second.presentation_effect_p_value
        assert first.positive_rate_shift_p_value == second.positive_rate_shift_p_value


# ---------------------------------------------------------------------------
# The instrument against scorers whose answer is known
# ---------------------------------------------------------------------------


def _noisy_but_indifferent(rng: np.random.Generator, num_items: int) -> tuple[np.ndarray, np.ndarray]:
    chance = rng.random(num_items)
    return chance, chance


def _rarely_noisy_but_indifferent(rng: np.random.Generator, num_items: int) -> tuple[np.ndarray, np.ndarray]:
    chance = np.where(rng.random(num_items) < 0.9, 0.0, 0.5)
    return chance, chance


def _one_way_effect(rng: np.random.Generator, num_items: int) -> tuple[np.ndarray, np.ndarray]:
    """On 30% of responses the chance of true falls from 0.8 to 0.3; the rest are confident and unmoved."""
    moved = rng.random(num_items) < 0.3
    settled = np.where(rng.random(num_items) < 0.5, 0.02, 0.98)
    return np.where(moved, 0.8, settled), np.where(moved, 0.3, settled)


def _two_way_effect(rng: np.random.Generator, num_items: int) -> tuple[np.ndarray, np.ndarray]:
    """A scorer that repeats itself reverses its verdict on 10% of responses, as often one way as the other."""
    moved = rng.random(num_items) < 0.1
    reference = (rng.random(num_items) < 0.5).astype(float)
    return reference, np.where(moved, 1.0 - reference, reference)


def _simulate(
    population: Callable[[np.random.Generator, int], tuple[np.ndarray, np.ndarray]],
    *,
    num_items: int,
    num_repeats: int,
    num_simulations: int,
    true_effect: float,
    true_shift: float,
) -> dict[str, float]:
    rng = np.random.default_rng(20261117)
    effects, flips = [], []
    counts: Counter[str] = Counter()
    for simulation in range(num_simulations):
        reference_chance, chance = population(rng, num_items)
        _, effect = _effect(
            rng.random((num_repeats, num_items)) < reference_chance,
            rng.random((num_repeats, num_items)) < chance,
            num_permutations=300,
            seed=simulation,
        )
        effects.append(effect.presentation_effect)
        flips.append(effect.flip_rate)
        low, high = effect.presentation_effect_interval
        counts["effect_covered"] += low <= true_effect <= high
        low, high = effect.positive_rate_shift_interval
        counts["shift_covered"] += low <= true_shift <= high
        counts["effect_rejected"] += effect.presentation_effect_p_value <= 0.05
        counts["shift_rejected"] += effect.positive_rate_shift_p_value <= 0.05
    return {
        "effect": float(np.mean(effects)),
        "effect_standard_error": float(np.std(effects) / np.sqrt(num_simulations)),
        "flip_rate": float(np.mean(flips)),
        **{name: count / num_simulations for name, count in counts.items()},
    }


def test_the_effect_test_holds_its_level_with_unequal_run_counts() -> None:
    rng = np.random.default_rng(20261117)
    rejected = 0
    for simulation in range(300):
        chance = np.where(rng.random(60) < 0.8, 0.02, 0.5)
        _, effect = _effect(
            rng.random((2, 60)) < chance, rng.random((6, 60)) < chance, num_permutations=300, seed=simulation
        )
        assert effect.positive_rate_shift_p_value is None
        rejected += effect.presentation_effect_p_value <= 0.05

    assert rejected / 300 <= 0.08


class TestAgainstKnownScorers:
    def test_a_noisy_scorer_that_ignores_presentation_shows_no_effect(self) -> None:
        result = _simulate(
            _noisy_but_indifferent, num_items=100, num_repeats=2, num_simulations=400, true_effect=0.0, true_shift=0.0
        )

        # A third of the run pairs differ, and none of it is the presentation.
        assert result["flip_rate"] == pytest.approx(1 / 3, abs=0.01)
        assert abs(result["effect"]) < 4 * result["effect_standard_error"]
        assert abs(result["effect"]) < 0.01
        assert result["effect_rejected"] <= 0.08
        assert result["shift_rejected"] <= 0.08
        assert result["effect_covered"] >= 0.9
        assert result["shift_covered"] >= 0.9

    def test_the_tests_hold_their_level_when_few_responses_ever_change(self) -> None:
        result = _simulate(
            _rarely_noisy_but_indifferent,
            num_items=100,
            num_repeats=2,
            num_simulations=400,
            true_effect=0.0,
            true_shift=0.0,
        )

        assert abs(result["effect"]) < 4 * result["effect_standard_error"]
        assert result["effect_rejected"] <= 0.08
        assert result["shift_rejected"] <= 0.08

    def test_a_planted_one_way_effect_is_recovered(self) -> None:
        result = _simulate(
            _one_way_effect,
            num_items=100,
            num_repeats=2,
            num_simulations=400,
            true_effect=0.3 * (0.8 - 0.3) ** 2,
            true_shift=0.3 * (0.3 - 0.8),
        )

        assert result["effect"] == pytest.approx(0.075, abs=4 * result["effect_standard_error"])
        # The raw flip rate is nearly three times the effect, because it carries the noise as well.
        assert result["flip_rate"] > 0.2
        assert result["effect_rejected"] >= 0.7
        assert result["shift_rejected"] >= 0.95
        assert result["effect_covered"] >= 0.88
        assert result["shift_covered"] >= 0.88

    def test_a_two_way_effect_is_found_though_the_reported_rate_does_not_move(self) -> None:
        result = _simulate(
            _two_way_effect, num_items=100, num_repeats=2, num_simulations=400, true_effect=0.1, true_shift=0.0
        )

        assert result["effect"] == pytest.approx(0.1, abs=4 * result["effect_standard_error"])
        assert result["flip_rate"] == pytest.approx(result["effect"])
        assert result["effect_rejected"] >= 0.95
        assert result["shift_rejected"] <= 0.08
        assert result["shift_covered"] >= 0.9


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


class _RuleScorer(MessageTrueFalseScorer):
    """Answers by a rule of the text it is shown and how often it has seen it, so a test plants the effect."""

    def __init__(self, *, rule: Callable[[str, int], bool | None | list[bool]], label: str = "rule") -> None:
        self._rule = rule
        self._label = label
        self.shown: list[str] = []
        self.objectives: list[str | None] = []
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"label": self._label})

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        text = message_piece.converted_value
        verdict = self._rule(text, self.shown.count(text))
        self.shown.append(text)
        self.objectives.append(expectation.objective if expectation else None)
        if verdict is None:
            return [self._build_undetermined_score(rationale="No verdict.", message_piece_id=message_piece.id)]
        return [
            Score(
                score_value=str(value),
                score_value_description="",
                score_metadata=None,
                score_type="true_false",
                score_category=None,
                score_rationale="",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=expectation.objective if expectation else None,
            )
            for value in (verdict if isinstance(verdict, list) else [verdict])
        ]


def _conversation(response: str, *, request: str = "request") -> list[Message]:
    return [
        MessagePiece(role="user", original_value=request).to_message(),
        MessagePiece(role="assistant", original_value=response).to_message(),
    ]


def _dataset(rows: list[tuple[str, list[bool]]], *, name: str = "planted") -> HumanLabeledDataset:
    return HumanLabeledDataset(
        name=name,
        version="1.0",
        metrics_type=MetricsType.OBJECTIVE,
        entries=[
            ObjectiveHumanLabeledEntry(conversation=_conversation(response), human_scores=labels, objective="objective")
            for response, labels in rows
        ],
    )


RESPONSES = ["yes one", "yes two", "no one", "no two"]
OBJECTIVE = ScoringExpectation(objective="o")


@pytest.mark.usefixtures("patch_central_database")
class TestEvaluator:
    async def test_a_scorer_side_presentation_with_a_planted_effect(self) -> None:
        reference = _RuleScorer(rule=lambda text, _: text.startswith("yes"), label="reference")
        # The other presentation reverses the verdict on the responses that end in "two".
        swapped = _RuleScorer(rule=lambda text, _: text.startswith("yes") != text.endswith("two"), label="swapped")
        evaluator = ScorerStabilityEvaluator(
            scorer=reference,
            presentations=[ScorerPresentation(name="swapped", scorer=swapped, kind=PresentationKind.OPTION_ORDER)],
        )

        metrics = await evaluator.evaluate_dataset_async(
            labeled_dataset=_dataset([(response, [response.startswith("yes")]) for response in RESPONSES])
        )

        effect = metrics.get_effect(name="swapped")
        assert metrics.num_items == metrics.num_input_items == 4
        assert metrics.num_repeats == effect.num_repeats == 2
        assert effect.kind == "option_order"
        assert effect.flip_rate == 0.5
        assert effect.repeat_flip_rate == 0.0
        assert effect.presentation_effect == 0.5
        assert effect.positive_rate_shift == 0.0
        assert metrics.reference_accuracy == 1.0
        assert effect.accuracy == 0.5
        assert metrics.dataset_name == "planted"
        assert metrics.dataset_version == "1.0"
        assert metrics.reference_scorer_identifier_hash == reference.get_identifier().hash
        assert effect.scorer_identifier_hash == swapped.get_identifier().hash
        assert effect.scorer_identifier_hash != metrics.reference_scorer_identifier_hash
        assert metrics.verdicts["swapped"].tolist() == [[1.0, 0.0, 0.0, 1.0]] * 2
        assert set(reference.objectives) == {"objective"}

    async def test_a_noisy_scorer_is_not_mistaken_for_a_presentation_effect(self) -> None:
        # Each scorer alternates its answer from one run to the next, whatever it is shown.
        reference = _RuleScorer(rule=lambda _, seen: seen % 2 == 0, label="reference")
        other = _RuleScorer(rule=lambda _, seen: seen % 2 == 1, label="other")
        evaluator = ScorerStabilityEvaluator(
            scorer=reference, presentations=[ScorerPresentation(name="other", scorer=other)]
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=[_conversation(response) for response in RESPONSES], expectations=[OBJECTIVE] * 4
        )

        effect = metrics.get_effect(name="other")
        assert effect.kind == "other"
        assert effect.flip_rate == 0.5
        assert effect.repeat_flip_rate == 1.0
        assert effect.presentation_effect == -0.5
        assert metrics.reference_repeat_flip_rate == 1.0
        assert metrics.gold_labels is None
        assert effect.accuracy is None

    async def test_response_converters_change_only_what_that_presentation_reads(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        reference = _RuleScorer(rule=lambda text, _: "yes" in text, label="reference")
        shown_converted = _RuleScorer(rule=lambda text, _: "yes" in text, label="converted")
        evaluator = ScorerStabilityEvaluator(
            scorer=reference,
            presentations=[
                ScorerPresentation(
                    name="rewritten",
                    scorer=shown_converted,
                    kind=PresentationKind.FORMATTING,
                    response_converters=[
                        SearchReplaceConverter(pattern="yes", replace="aye"),
                        SuffixAppendConverter(suffix="."),
                    ],
                )
            ],
        )
        conversations = [_conversation(response) for response in RESPONSES]

        metrics = await evaluator.evaluate_conversations_async(
            conversations=conversations, expectations=[OBJECTIVE] * 4, num_repeats=1
        )

        assert set(reference.shown) == set(RESPONSES)
        assert set(shown_converted.shown) == {"aye one .", "aye two .", "no one .", "no two ."}
        assert metrics.get_effect(name="rewritten").flip_to_false_rate == 0.5
        # The caller's messages are left alone, and each presentation scored its own stored copy.
        assert [conversation[1].get_value() for conversation in conversations] == RESPONSES
        stored = await sqlite_instance.get_message_pieces_async(role="assistant")
        assert len(stored) == 8
        assert len({piece.conversation_id for piece in stored}) == 8
        assert not {str(conversation[1].message_pieces[0].id) for conversation in conversations} & {
            str(piece.id) for piece in stored
        }
        converted = [piece for piece in stored if piece.converted_value != piece.original_value]
        assert sorted(piece.original_value for piece in converted) == sorted(RESPONSES)
        assert all(len(piece.converter_identifiers) == 2 for piece in converted)
        # The request travels with the response, for scorers that read the conversation.
        requests = await sqlite_instance.get_message_pieces_async(role="user")
        assert {piece.conversation_id for piece in requests} == {piece.conversation_id for piece in stored}

    async def test_every_presentation_is_scored_once_before_any_is_scored_again(self) -> None:
        order: list[str] = []

        def recorder(name: str) -> Callable[[str, int], bool]:
            def rule(text: str, seen: int) -> bool:
                order.append(f"{name}{seen}")
                return True

            return rule

        evaluator = ScorerStabilityEvaluator(
            scorer=_RuleScorer(rule=recorder("r"), label="r"),
            presentations=[
                ScorerPresentation(name="a", scorer=_RuleScorer(rule=recorder("a"), label="a")),
                ScorerPresentation(name="b", scorer=_RuleScorer(rule=recorder("b"), label="b")),
            ],
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=[_conversation("only")], expectations=[OBJECTIVE], num_repeats=3
        )

        assert order == ["r0", "a0", "b0", "r1", "a1", "b1", "r2", "a2", "b2"]
        assert [effect.name for effect in metrics.effects] == ["a", "b"]
        assert metrics.num_repeats == 3

    async def test_a_response_without_a_verdict_is_recorded_as_missing(self) -> None:
        reference = _RuleScorer(rule=lambda text, _: text.startswith("yes"), label="reference")
        silent = _RuleScorer(rule=lambda text, _: None if text == "yes two" else text.startswith("yes"), label="s")
        evaluator = ScorerStabilityEvaluator(
            scorer=reference, presentations=[ScorerPresentation(name="silent", scorer=silent)]
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=[_conversation(response) for response in RESPONSES], expectations=[OBJECTIVE] * 4
        )

        effect = metrics.get_effect(name="silent")
        assert metrics.num_items == 3
        assert effect.num_undetermined_items == 1
        assert effect.flip_rate == 0.0
        assert np.isnan(metrics.verdicts["silent"][:, 1]).all()

    async def test_a_scorer_that_does_not_apply_is_recorded_as_missing(self) -> None:
        reference = _RuleScorer(rule=lambda text, _: True, label="reference")
        absent = _RuleScorer(rule=lambda text, _: [] if text == "no two" else True, label="absent")
        evaluator = ScorerStabilityEvaluator(
            scorer=reference, presentations=[ScorerPresentation(name="absent", scorer=absent)]
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=[_conversation(response) for response in RESPONSES],
            expectations=[OBJECTIVE] * 4,
            num_repeats=1,
        )

        assert metrics.num_items == 3
        assert metrics.get_effect(name="absent").num_undetermined_items == 1

    async def test_the_gold_label_is_the_majority_with_a_split_counted_as_false(self) -> None:
        scorer = _RuleScorer(rule=lambda text, _: True, label="reference")
        evaluator = ScorerStabilityEvaluator(
            scorer=scorer,
            presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda text, _: True))],
        )

        metrics = await evaluator.evaluate_dataset_async(
            labeled_dataset=_dataset([("a", [T, T, F]), ("b", [T, F]), ("c", [F, F, T])]), num_repeats=1
        )

        assert metrics.gold_labels == [True, False, False]
        assert metrics.reference_accuracy == pytest.approx(1 / 3)

    async def test_settings_reach_the_computation(self) -> None:
        evaluator = ScorerStabilityEvaluator(
            scorer=_RuleScorer(rule=lambda text, _: True, label="reference"),
            presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda text, _: True))],
        )

        metrics = await evaluator.evaluate_dataset_async(
            labeled_dataset=_dataset([("a", [T]), ("b", [F])]),
            num_repeats=3,
            confidence_level=0.9,
            num_permutations=50,
            seed=4,
        )

        assert metrics.confidence_level == 0.9
        assert metrics.num_permutations == 50
        assert metrics.get_effect(name="same").num_repeats == 3

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"conversations": [], "expectations": []}, "At least one conversation is required"),
            ({"expectations": [OBJECTIVE]}, "1 expectations were given for 2 conversations"),
            ({"gold_labels": [T]}, "1 gold labels were given for 2 conversations"),
            ({"num_repeats": 0}, "num_repeats must be at least 1"),
            ({"confidence_level": 1.0}, "confidence_level must be between 0 and 1"),
            ({"num_permutations": 0}, "num_permutations must be at least 1"),
            ({"seed": -1}, "seed must not be negative"),
        ],
    )
    async def test_unusable_inputs_are_rejected_before_anything_is_stored_or_scored(
        self, kwargs, message: str, sqlite_instance: MemoryInterface
    ) -> None:
        scorer = _RuleScorer(rule=lambda text, _: True)
        evaluator = ScorerStabilityEvaluator(
            scorer=scorer, presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda t, _: True))]
        )
        arguments = {
            "conversations": [_conversation("a"), _conversation("b")],
            "expectations": [OBJECTIVE, OBJECTIVE],
            **kwargs,
        }

        with pytest.raises(ValueError, match=message):
            await evaluator.evaluate_conversations_async(**arguments)
        assert scorer.shown == []
        assert await sqlite_instance.get_message_pieces_async() == []

    async def test_typed_conditions_reach_scorers_that_need_them(self) -> None:
        # QuestionAnswerScorer cannot score without the answer it is to look for.
        expectations = [ScoringExpectation(conditions=(AnswerMatches(correct_answer="Paris"),))] * 2
        evaluator = ScorerStabilityEvaluator(
            scorer=QuestionAnswerScorer(),
            presentations=[
                ScorerPresentation(
                    name="reworded",
                    scorer=QuestionAnswerScorer(),
                    response_converters=[SearchReplaceConverter(pattern="Paris", replace="the French capital")],
                )
            ],
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=[_conversation("It is Paris."), _conversation("It is Rome.")],
            expectations=expectations,
            num_repeats=1,
        )

        assert metrics.verdicts["reference"].tolist() == [[1.0, 0.0]]
        assert metrics.verdicts["reworded"].tolist() == [[0.0, 0.0]]

    async def test_each_expectation_is_passed_on_as_it_is(self) -> None:
        seen: list[ScoringExpectation | None] = []

        class _Recorder(_RuleScorer):
            async def _score_piece_with_expectation_async(self, message_piece, *, expectation):
                seen.append(expectation)
                return await super()._score_piece_with_expectation_async(message_piece, expectation=expectation)

        evaluator = ScorerStabilityEvaluator(
            scorer=_Recorder(rule=lambda text, _: True),
            presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda text, _: True))],
        )
        first, second = ScoringExpectation(objective="first"), ScoringExpectation(objective="second")

        await evaluator.evaluate_conversations_async(
            conversations=[_conversation("a"), _conversation("b")], expectations=[first, second], num_repeats=1
        )

        assert sorted(expectation.objective for expectation in seen) == ["first", "second"]

    @pytest.mark.parametrize("assistant_messages", [0, 2])
    async def test_a_conversation_needs_exactly_one_response(self, assistant_messages: int) -> None:
        evaluator = ScorerStabilityEvaluator(
            scorer=_RuleScorer(rule=lambda text, _: True),
            presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda text, _: True))],
        )
        conversation = [MessagePiece(role="user", original_value="request").to_message()] + [
            MessagePiece(role="assistant", original_value="response").to_message() for _ in range(assistant_messages)
        ]

        with pytest.raises(ValueError, match=f"exactly one assistant message, but found {assistant_messages}"):
            await evaluator.evaluate_conversations_async(conversations=[conversation], expectations=[OBJECTIVE])

    async def test_a_harm_dataset_is_rejected(self) -> None:
        dataset = HumanLabeledDataset(
            name="harm",
            version="1.0",
            metrics_type=MetricsType.HARM,
            entries=[HarmHumanLabeledEntry(conversation=_conversation("a"), human_scores=[0.5], harm_category="x")],
        )
        evaluator = ScorerStabilityEvaluator(
            scorer=_RuleScorer(rule=lambda text, _: True),
            presentations=[ScorerPresentation(name="same", scorer=_RuleScorer(rule=lambda text, _: True))],
        )

        with pytest.raises(ValueError, match="must be of type OBJECTIVE"):
            await evaluator.evaluate_dataset_async(labeled_dataset=dataset)


@pytest.mark.usefixtures("patch_central_database")
class TestEvaluatorConstruction:
    def test_presentations_are_required(self) -> None:
        with pytest.raises(ValueError, match="At least one presentation is required"):
            ScorerStabilityEvaluator(scorer=SubStringScorer(substring="x"), presentations=[])

    def test_a_multi_label_scorer_needs_a_label_selected_first(self) -> None:
        with pytest.raises(ValueError, match="Select a label with TrueFalseScoreSelector"):
            ScorerStabilityEvaluator(
                scorer=SubStringScorer(substring="x"),
                presentations=[
                    ScorerPresentation(name="other", scorer=MagicMock(spec=MessageMultiLabelTrueFalseScorer))
                ],
            )

    @pytest.mark.parametrize("names", [["a", "a"], ["reference"]])
    def test_names_must_be_unique_and_not_the_references(self, names: list[str]) -> None:
        with pytest.raises(ValueError, match="must be unique and must not be 'reference'"):
            ScorerStabilityEvaluator(
                scorer=SubStringScorer(substring="x"),
                presentations=[ScorerPresentation(name=name, scorer=SubStringScorer(substring="x")) for name in names],
            )

    def test_only_true_false_scorers_are_accepted(self) -> None:
        with pytest.raises(ValueError, match="Presentation 'other' must use a TrueFalseScorer"):
            ScorerStabilityEvaluator(
                scorer=SubStringScorer(substring="x"),
                presentations=[ScorerPresentation(name="other", scorer=object())],  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="Presentation 'reference' must use a TrueFalseScorer"):
            ScorerStabilityEvaluator(
                scorer=object(),  # type: ignore[arg-type]
                presentations=[ScorerPresentation(name="other", scorer=SubStringScorer(substring="x"))],
            )


# ---------------------------------------------------------------------------
# On PyRIT's own human-labeled refusal data, with a scorer that cannot be noisy
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_keyword_scorer_on_the_refusal_dataset() -> None:
    dataset = HumanLabeledDataset.from_csv(
        csv_path=SCORER_EVALS_PATH / "sample" / "mini_refusal.csv", metrics_type=MetricsType.OBJECTIVE
    )
    keyword = "I cannot"
    evaluator = ScorerStabilityEvaluator(
        scorer=SubStringScorer(substring=keyword),
        presentations=[
            # A keyword is indifferent to what follows the response.
            ScorerPresentation(
                name="trailing_text",
                scorer=SubStringScorer(substring=keyword),
                kind=PresentationKind.FORMATTING,
                response_converters=[SuffixAppendConverter(suffix="\n")],
            ),
            # It is not indifferent to the same refusal in other words.
            ScorerPresentation(
                name="contraction",
                scorer=SubStringScorer(substring=keyword),
                kind=PresentationKind.LABEL_WORDING,
                response_converters=[SearchReplaceConverter(pattern="I cannot", replace="I can't")],
            ),
        ],
    )

    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=dataset)

    assert isinstance(metrics, ScorerStabilityMetrics)
    assert metrics.num_items == len(dataset.entries)
    assert metrics.reference_repeat_flip_rate == 0.0
    assert 0.0 < metrics.reference_positive_rate < 1.0
    unchanged = metrics.get_effect(name="trailing_text")
    assert unchanged.flip_rate == 0.0
    assert unchanged.presentation_effect == 0.0
    assert unchanged.presentation_effect_p_value == 1.0
    assert unchanged.accuracy_shift == 0.0
    reworded = metrics.get_effect(name="contraction")
    assert reworded.positive_rate == 0.0
    assert reworded.flip_to_true_rate == 0.0
    assert reworded.flip_rate == reworded.presentation_effect == metrics.reference_positive_rate
    assert reworded.positive_rate_shift == -metrics.reference_positive_rate
    assert metrics.positive_rate_range == (0.0, metrics.reference_positive_rate)


# ---------------------------------------------------------------------------
# A model judge whose presentation is set through the scorer's own constructor
# ---------------------------------------------------------------------------


def _judge_that_reads_only_the_first_line() -> MagicMock:
    """Build a judge model that answers true when the first line of its prompt says 'cannot'."""

    async def answer(*args: Any, **kwargs: Any) -> list[Message]:
        request: Message = kwargs.get("message") or args[0]
        first_line = request.get_value().splitlines()[0]
        verdict = "true" if "cannot" in first_line else "false"
        reply = f'{{"score_value": "{verdict}", "description": "d", "rationale": "r"}}'
        return [Message(message_pieces=[MessagePiece(role="assistant", original_value=reply)])]

    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("FirstLineJudge")
    target._max_requests_per_minute = None
    target.send_prompt_async = AsyncMock(side_effect=answer)
    return target


@pytest.mark.usefixtures("patch_central_database")
async def test_field_order_reaches_a_model_judge_and_its_effect_is_measured() -> None:
    judge = _judge_that_reads_only_the_first_line()
    evaluator = ScorerStabilityEvaluator(
        scorer=SelfAskRefusalScorer(chat_target=judge),
        presentations=[
            ScorerPresentation(
                name="response_first",
                kind=PresentationKind.FIELD_ORDER,
                scorer=SelfAskRefusalScorer(
                    chat_target=judge,
                    prompt_format_string="response_to_evaluate_input: {response}\nconversation_objective: {objective}",
                ),
            )
        ],
    )
    refusals = [True, False, True, False, False]
    conversations = [
        _conversation("I cannot help with that." if refused else "Sure, here it is.") for refused in refusals
    ]

    metrics = await evaluator.evaluate_conversations_async(
        conversations=conversations,
        expectations=[ScoringExpectation(objective="Tell me a story.")] * 5,
        gold_labels=refusals,
    )

    # With the objective first the judge never sees the refusal; with the response first it does.
    effect = metrics.get_effect(name="response_first")
    assert metrics.reference_positive_rate == 0.0
    assert effect.positive_rate == 0.4
    assert effect.flip_to_true_rate == effect.presentation_effect == 0.4
    assert effect.repeat_flip_rate == 0.0
    assert metrics.reference_accuracy == 0.6
    assert effect.accuracy == 1.0
    assert metrics.positive_rate_range == (0.0, 0.4)
    assert effect.scorer_identifier_hash != metrics.reference_scorer_identifier_hash
