# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer stability: the estimates, the tests on them, and the evaluator.

The simulations give the instrument scorers whose answer is known in advance: one that is noisy
but indifferent to presentation, and ones with a planted effect of a known size.
"""

import itertools
import json
import uuid
from collections.abc import Callable
from dataclasses import fields
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from scipy.stats import hypergeom

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
    ObjectiveScorerEvaluator,
    QuestionAnswerScorer,
    ScorerPresentation,
    ScorerPromptValidator,
    ScorerStabilityEvaluator,
    SelfAskRefusalScorer,
    SubStringScorer,
    TrueFalseInverterScorer,
    compute_scorer_stability,
)

T, F = True, False


def _effect(reference: Any, presentation: Any, **kwargs: Any):
    metrics = compute_scorer_stability(verdicts={"reference": reference, "other": presentation}, **kwargs)
    return metrics, metrics.get_effect(name="other")


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


# ---------------------------------------------------------------------------
# Estimates
# ---------------------------------------------------------------------------


def test_flip_rate_splits_into_repeat_noise_and_presentation_effect() -> None:
    # One response: the reference answers true in 1 of 2 runs, the presentation in 2 of 2.
    # Pairs across presentations: 2 of 4 differ. Pairs within: the reference's two runs
    # differ, the presentation's do not, so the noise is (1 + 0) / 2.
    _, effect = _effect([[T], [F]], [[T], [T]])

    assert effect.flip_rate == 0.5
    assert effect.repeat_flip_rate == 0.5
    assert effect.presentation_effect == 0.0


def test_scorer_that_repeats_itself_has_an_effect_equal_to_its_flip_rate() -> None:
    _, effect = _effect([[T, T, F, F], [T, T, F, F]], [[T, F, F, F], [T, F, F, F]])

    assert effect.repeat_flip_rate == 0.0
    assert effect.flip_rate == 0.25
    assert effect.presentation_effect == 0.25
    assert effect.positive_rate == 0.25
    assert effect.positive_rate_shift == -0.25


def test_presentation_effect_agrees_with_counting_pairs_of_runs() -> None:
    rng = np.random.default_rng(5)
    reference = rng.random((3, 30)) < rng.random(30)
    presentation = rng.random((3, 30)) < rng.random(30)

    _, effect = _effect(reference, presentation)

    assert effect.presentation_effect == pytest.approx(_effect_by_counting_pairs(reference, presentation))
    assert effect.flip_rate == pytest.approx(
        np.mean([first != second for first in reference for second in presentation])
    )


def test_single_runs_leave_the_noise_floor_and_effect_empty() -> None:
    metrics, effect = _effect([[T, T, F, F]], [[T, F, F, T]])

    assert effect.flip_rate == 0.5
    assert effect.positive_rate_shift_p_value is not None
    assert effect.repeat_flip_rate is None
    assert effect.presentation_effect is None
    assert effect.presentation_effect_p_value is None
    assert metrics.reference_repeat_flip_rate is None


def test_equal_flips_each_way_leave_the_reported_rate_where_it_was() -> None:
    reference = [[T] * 10 + [F] * 10] * 2
    presentation = [[F] * 3 + [T] * 7 + [T] * 3 + [F] * 7] * 2

    _, effect = _effect(reference, presentation)

    assert effect.presentation_effect == pytest.approx(0.3)
    assert effect.positive_rate_shift == 0.0
    assert effect.presentation_effect_p_value < 0.01
    assert effect.positive_rate_shift_p_value == 1.0


# ---------------------------------------------------------------------------
# Permutation tests and their adjustment
# ---------------------------------------------------------------------------


def test_effect_test_matches_exact_enumeration() -> None:
    reference = np.array([[T, T, F], [T, F, F]])
    presentation = np.array([[F, T, T], [F, T, T]])
    _, effect = _effect(reference, presentation, num_permutations=40000, seed=1)

    # Under no effect, the true verdicts of a response fall in the reference's runs
    # hypergeometrically. Enumerate every outcome and count pairs of runs directly.
    pooled = reference.sum(axis=0) + presentation.sum(axis=0)
    outcomes = [
        [(count, hypergeom.pmf(count, 4, int(total), 2)) for count in range(3) if 0 <= total - count <= 2]
        for total in pooled
    ]
    observed = _effect_by_counting_pairs(reference, presentation)
    exact = 0.0
    for combination in itertools.product(*outcomes):
        drawn_reference = np.array([[index < count for count, _ in combination] for index in range(2)])
        drawn = np.array(
            [
                [index < total - count for (count, _), total in zip(combination, pooled, strict=True)]
                for index in range(2)
            ]
        )
        if _effect_by_counting_pairs(drawn_reference, drawn) >= observed - 1e-12:
            exact += float(np.prod([chance for _, chance in combination]))

    assert 0.02 < exact < 0.98
    assert effect.presentation_effect_p_value == pytest.approx(exact, abs=0.01)


def test_with_single_runs_the_shift_test_is_the_exact_sign_test() -> None:
    # Eight responses change verdict, all from false to true: 2 * 0.5 ** 8 = 0.0078.
    _, effect = _effect([[F] * 8 + [T] * 12], [[T] * 20], num_permutations=20000, seed=3)

    assert effect.positive_rate_shift_p_value == pytest.approx(2 * 0.5**8, abs=0.003)


def test_p_values_are_holm_adjusted_across_presentations() -> None:
    reference = [[T] * 20 + [F] * 20] * 2
    moved = [[F] * 40] * 2
    # No reassignment matches either moved presentation, so each unadjusted p-value is 1 / 100.
    alone = compute_scorer_stability(verdicts={"reference": reference, "a": moved}, num_permutations=99)
    metrics = compute_scorer_stability(
        verdicts={"reference": reference, "a": moved, "b": moved, "same": reference}, num_permutations=99
    )

    assert alone.get_effect(name="a").positive_rate_shift_p_value == pytest.approx(0.01)
    # Holm multiplies the smallest of three by 3, the next by 2 but never below the smallest, the last by 1.
    for name, expected in (("a", 0.03), ("b", 0.03), ("same", 1.0)):
        effect = metrics.get_effect(name=name)
        assert effect.positive_rate_shift_p_value == pytest.approx(expected)
        assert effect.presentation_effect_p_value == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Missing verdicts
# ---------------------------------------------------------------------------


def test_run_without_a_verdict_is_left_out_instead_of_counted_as_false() -> None:
    # Counted as false, the third response would look like a flip from true. The reference
    # misses its fourth response, which leaves the reference figures and the comparison.
    metrics, effect = _effect([[T, F, T, T], [T, F, T, None]], [[T, F, None, T], [T, F, np.nan, T]])

    assert effect.num_items == 2
    assert effect.num_undetermined_items == 1
    assert effect.flip_rate == 0.0
    assert effect.positive_rate == 0.5
    assert metrics.reference_num_undetermined_items == 1
    assert metrics.reference_positive_rate == pytest.approx(2 / 3)
    assert np.isnan(metrics.verdicts["other"][0, 2])


def test_failing_presentation_changes_no_other_comparison() -> None:
    reference = [[T] * 10 + [F] * 10] * 2
    moved = [[F] * 20] * 2
    # Agrees with the reference where it answers, but never answers on the first eight responses.
    failing = [[None] * 8 + [T] * 2 + [F] * 10] * 2
    alone = compute_scorer_stability(verdicts={"reference": reference, "moved": moved}, num_permutations=99)
    both = compute_scorer_stability(
        verdicts={"reference": reference, "failing": failing, "moved": moved}, num_permutations=99
    )

    assert both.reference_positive_rate == alone.reference_positive_rate == 0.5
    assert both.reference_repeat_flip_rate == alone.reference_repeat_flip_rate
    assert both.get_effect(name="failing").num_undetermined_items == 8
    with_failing, without = both.get_effect(name="moved"), alone.get_effect(name="moved")
    for name in ("num_items", "positive_rate", "positive_rate_shift", "flip_rate", "presentation_effect"):
        assert getattr(with_failing, name) == getattr(without, name)
    # The failing presentation's own unadjusted p-values are 1, so Holm doubles the moved one's.
    assert with_failing.positive_rate_shift_p_value == pytest.approx(2 * without.positive_rate_shift_p_value)
    assert with_failing.presentation_effect_p_value == pytest.approx(2 * without.presentation_effect_p_value)


def test_comparison_without_any_shared_verdict_is_an_error() -> None:
    with pytest.raises(ValueError, match="No response reached a verdict in every run of both the reference and 'b'"):
        compute_scorer_stability(verdicts={"reference": [[T, None]], "b": [[None, T]]})


# ---------------------------------------------------------------------------
# Input and output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "kwargs", "message"),
    [
        ({"other": [[T]]}, {}, "reference presentation 'reference' has no verdicts"),
        ({"reference": [[T]]}, {}, "At least one presentation besides the reference"),
        ({"reference": [[T, F]], "other": [[T]]}, {}, "'other' has 1 runs of 1 responses"),
        ({"reference": [[T], [F]], "other": [[T]]}, {}, "Every presentation needs the same runs"),
        ({"reference": [[T, F]], "other": [[T, F], [T]]}, {}, "rows of true/false verdicts of equal length"),
        ({"reference": [[T, F]], "other": [[T, 0.5]]}, {}, "not true, false or missing"),
        ({"reference": [[]], "other": [[]]}, {}, "at least one run of at least one verdict"),
        ({"reference": [[None]], "other": [[T]]}, {}, "No response reached a verdict in every run of the reference"),
        ({"reference": [[T]], "other": [[T]]}, {"num_permutations": 0}, "num_permutations must be at least 1"),
        ({"reference": [[T]], "other": [[T]]}, {"seed": -1}, "seed must not be negative"),
    ],
)
def test_malformed_input_is_rejected(verdicts: dict, kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        compute_scorer_stability(verdicts=verdicts, **kwargs)


def test_get_effect_rejects_a_name_without_an_effect() -> None:
    metrics, _ = _effect([[T, F]], [[T, T]])

    with pytest.raises(KeyError, match="No presentation named 'reference'"):
        metrics.get_effect(name="reference")


def test_json_writes_missing_values_as_null() -> None:
    metrics, _ = _effect([[T, F, None]], [[T, T, T]])

    document = json.loads(metrics.to_json())

    assert document["verdicts"] == {"reference": [[1.0, 0.0, None]], "other": [[1.0, 1.0, 1.0]]}
    assert document["effects"][0]["presentation_effect"] is None


def test_recorded_verdicts_reproduce_the_metrics() -> None:
    rng = np.random.default_rng(5)
    first = compute_scorer_stability(
        verdicts={
            "reference": rng.random((3, 40)) < 0.5,
            "a": rng.random((3, 40)) < 0.6,
            "b": rng.random((3, 40)) < 0.4,
        }
    )
    document = json.loads(first.to_json())

    again = compute_scorer_stability(verdicts=document["verdicts"])

    assert json.loads(again.to_json()) == document


# ---------------------------------------------------------------------------
# The instrument against scorers whose answer is known
# ---------------------------------------------------------------------------


def _noisy_but_indifferent(rng: np.random.Generator, num_items: int) -> tuple[np.ndarray, np.ndarray]:
    chance = rng.random(num_items)
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
    population: Callable[[np.random.Generator, int], tuple[np.ndarray, np.ndarray]], *, num_simulations: int = 300
) -> dict[str, float]:
    rng = np.random.default_rng(20261117)
    effects, flips, effect_rejected, shift_rejected = [], [], 0, 0
    for simulation in range(num_simulations):
        reference_chance, chance = population(rng, 100)
        _, effect = _effect(
            rng.random((2, 100)) < reference_chance,
            rng.random((2, 100)) < chance,
            num_permutations=300,
            seed=simulation,
        )
        effects.append(effect.presentation_effect)
        flips.append(effect.flip_rate)
        effect_rejected += effect.presentation_effect_p_value <= 0.05
        shift_rejected += effect.positive_rate_shift_p_value <= 0.05
    return {
        "effect": float(np.mean(effects)),
        "effect_standard_error": float(np.std(effects) / np.sqrt(num_simulations)),
        "flip_rate": float(np.mean(flips)),
        "effect_rejected": effect_rejected / num_simulations,
        "shift_rejected": shift_rejected / num_simulations,
    }


def test_noisy_scorer_that_ignores_presentation_shows_no_effect() -> None:
    result = _simulate(_noisy_but_indifferent)

    # A third of the run pairs differ, and none of it is the presentation.
    assert result["flip_rate"] == pytest.approx(1 / 3, abs=0.01)
    assert abs(result["effect"]) < 4 * result["effect_standard_error"]
    assert result["effect_rejected"] <= 0.08
    assert result["shift_rejected"] <= 0.08


def test_planted_effect_is_recovered() -> None:
    result = _simulate(_one_way_effect)

    assert result["effect"] == pytest.approx(0.3 * (0.8 - 0.3) ** 2, abs=4 * result["effect_standard_error"])
    # The raw flip rate is nearly three times the effect, because it carries the noise as well.
    assert result["flip_rate"] > 0.2
    assert result["effect_rejected"] >= 0.7
    assert result["shift_rejected"] >= 0.95


def test_two_way_effect_is_found_though_the_reported_rate_does_not_move() -> None:
    result = _simulate(_two_way_effect)

    assert result["effect"] == pytest.approx(0.1, abs=4 * result["effect_standard_error"])
    assert result["effect_rejected"] >= 0.95
    assert result["shift_rejected"] <= 0.08


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


class _RuleScorer(MessageTrueFalseScorer):
    """Answers by a rule of the text it is shown and how often it has seen it, so a test plants the effect."""

    def __init__(self, *, rule: Callable[[str, int], bool | None | list[bool]], label: str = "rule") -> None:
        self._rule = rule
        self._label = label
        self.shown: list[str] = []
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"label": self._label})

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        text = message_piece.converted_value
        verdict = self._rule(text, self.shown.count(text))
        self.shown.append(text)
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


def _always(verdict: bool) -> Callable[[str, int], bool]:
    return lambda text, seen: verdict


def _conversation(response: str) -> list[Message]:
    conversation_id = str(uuid.uuid4())
    return [
        MessagePiece(role="user", original_value="request", conversation_id=conversation_id).to_message(),
        MessagePiece(role="assistant", original_value=response, conversation_id=conversation_id).to_message(),
    ]


async def _stored_conversations(memory: MemoryInterface, responses: list[str]) -> list[list[Message]]:
    conversations = [_conversation(response) for response in responses]
    for conversation in conversations:
        for message in conversation:
            await memory.add_message_to_memory_async(request=message)
    return conversations


def _dataset(rows: list[tuple[str, list[bool]]]) -> HumanLabeledDataset:
    return HumanLabeledDataset(
        name="planted",
        version="1.0",
        metrics_type=MetricsType.OBJECTIVE,
        entries=[
            ObjectiveHumanLabeledEntry(conversation=_conversation(response), human_scores=labels, objective="objective")
            for response, labels in rows
        ],
    )


def _evaluator(*, reference: MessageTrueFalseScorer | None = None, **presentations: Any) -> ScorerStabilityEvaluator:
    return ScorerStabilityEvaluator(
        scorer=reference or _RuleScorer(rule=_always(True), label="reference"),
        presentations=[ScorerPresentation(name=name, scorer=scorer) for name, scorer in presentations.items()]
        or [ScorerPresentation(name="other", scorer=_RuleScorer(rule=_always(True), label="other"))],
    )


RESPONSES = ["yes one", "yes two", "no one", "no two"]
OBJECTIVE = ScoringExpectation(objective="o")


@pytest.mark.usefixtures("patch_central_database")
class TestEvaluator:
    async def test_planted_effect_is_measured(self) -> None:
        reference = _RuleScorer(rule=lambda text, _: text.startswith("yes"), label="reference")
        # The other presentation reverses the verdict on the responses that end in "two".
        swapped = _RuleScorer(rule=lambda text, _: text.startswith("yes") != text.endswith("two"), label="swapped")

        metrics = await _evaluator(reference=reference, swapped=swapped).evaluate_dataset_async(
            labeled_dataset=_dataset([(response, [response.startswith("yes")]) for response in RESPONSES])
        )

        effect = metrics.get_effect(name="swapped")
        assert metrics.num_repeats == 3
        assert effect.num_items == metrics.num_input_items == 4
        assert effect.flip_rate == effect.presentation_effect == 0.5
        assert effect.repeat_flip_rate == 0.0
        assert effect.positive_rate_shift == 0.0
        assert metrics.reference_eval_hash == reference.get_identifier().eval_hash
        assert effect.eval_hash == swapped.get_identifier().eval_hash
        assert (metrics.dataset_name, metrics.dataset_version) == ("planted", "1.0")
        assert metrics.verdicts["swapped"].tolist() == [[1.0, 0.0, 0.0, 1.0]] * 3

    async def test_labeled_metrics_match_objective_scorer_evaluator(self) -> None:
        def noisy(flip: str) -> Callable[[str, int], bool | None]:
            # The third run of one response flips, and "no two" reaches no verdict in its second run.
            return lambda text, seen: (
                None if (text, seen) == ("no two", 1) else text.startswith("yes") != ((text, seen) == (flip, 2))
            )

        rows = [(response, [response.startswith("yes"), response != "no one"]) for response in RESPONSES]
        metrics = await _evaluator(
            reference=_RuleScorer(rule=noisy("yes one"), label="reference"),
            moved=_RuleScorer(rule=noisy("no one"), label="moved"),
        ).evaluate_dataset_async(labeled_dataset=_dataset(rows))

        for stability_metrics, flip, label in (
            (metrics.reference_metrics, "yes one", "reference"),
            (metrics.get_effect(name="moved").metrics, "no one", "moved"),
        ):
            expected = await ObjectiveScorerEvaluator(
                _RuleScorer(rule=noisy(flip), label=label)
            ).evaluate_dataset_async(labeled_dataset=_dataset(rows), num_scorer_trials=3)
            assert stability_metrics.num_responses == 3
            for metric in fields(expected):
                if metric.name not in ("trial_scores", "average_score_time_seconds"):
                    assert getattr(stability_metrics, metric.name) == getattr(expected, metric.name), metric.name
            assert np.array_equal(stability_metrics.trial_scores, expected.trial_scores)

    async def test_conversations_report_no_labeled_metrics(self, sqlite_instance: MemoryInterface) -> None:
        conversations = await _stored_conversations(sqlite_instance, RESPONSES)

        metrics = await _evaluator().evaluate_conversations_async(
            conversations=conversations, expectations=[OBJECTIVE] * 4
        )

        assert metrics.reference_metrics is None
        assert metrics.get_effect(name="other").metrics is None
        assert metrics.dataset_name is None

    async def test_rounds_score_every_presentation_before_any_again(self, sqlite_instance: MemoryInterface) -> None:
        order: list[str] = []

        def recorder(name: str) -> Callable[[str, int], bool]:
            def rule(text: str, seen: int) -> bool:
                order.append(f"{name}{seen}")
                return True

            return rule

        evaluator = _evaluator(
            reference=_RuleScorer(rule=recorder("r"), label="r"),
            a=_RuleScorer(rule=recorder("a"), label="a"),
            b=_RuleScorer(rule=recorder("b"), label="b"),
        )

        await evaluator.evaluate_conversations_async(
            conversations=await _stored_conversations(sqlite_instance, ["only"]), expectations=[OBJECTIVE]
        )

        assert order == ["r0", "a0", "b0", "r1", "a1", "b1", "r2", "a2", "b2"]

    @pytest.mark.parametrize("no_verdict", [None, []], ids=["undetermined", "not_applicable"])
    async def test_response_without_a_verdict_is_recorded_as_missing(
        self, no_verdict: list | None, sqlite_instance: MemoryInterface
    ) -> None:
        silent = _RuleScorer(rule=lambda text, _: no_verdict if text == "yes two" else True, label="silent")

        metrics = await _evaluator(silent=silent).evaluate_conversations_async(
            conversations=await _stored_conversations(sqlite_instance, RESPONSES), expectations=[OBJECTIVE] * 4
        )

        effect = metrics.get_effect(name="silent")
        assert effect.num_items == 3
        assert effect.num_undetermined_items == 1
        assert np.isnan(metrics.verdicts["silent"][:, 1]).all()

    async def test_each_expectation_reaches_every_scorer(self, sqlite_instance: MemoryInterface) -> None:
        # QuestionAnswerScorer cannot score without the answer it is to look for, and each response has its own.
        evaluator = _evaluator(
            reference=QuestionAnswerScorer(),
            answer_only=QuestionAnswerScorer(correct_answer_matching_patterns=["{correct_answer}"]),
        )

        metrics = await evaluator.evaluate_conversations_async(
            conversations=await _stored_conversations(sqlite_instance, ["It is Paris.", "It is Rome."]),
            expectations=[
                ScoringExpectation(conditions=(AnswerMatches(correct_answer="Paris"),)),
                ScoringExpectation(conditions=(AnswerMatches(correct_answer="Rome"),)),
            ],
            num_repeats=1,
        )

        assert metrics.verdicts["reference"].tolist() == [[1.0, 1.0]]
        assert metrics.verdicts["answer_only"].tolist() == [[1.0, 1.0]]

    async def test_conversations_are_scored_in_place(self, sqlite_instance: MemoryInterface) -> None:
        conversations = await _stored_conversations(sqlite_instance, RESPONSES)

        await _evaluator().evaluate_conversations_async(conversations=conversations, expectations=[OBJECTIVE] * 4)

        assert len(await sqlite_instance.get_message_pieces_async()) == 8
        response_ids = {str(conversation[1].message_pieces[0].id) for conversation in conversations}
        scores = await sqlite_instance.get_prompt_scores_async(prompt_ids=list(response_ids))
        assert {str(score.message_piece_id) for score in scores} == response_ids

    async def test_conversations_must_already_be_in_memory(self, sqlite_instance: MemoryInterface) -> None:
        scorer = _RuleScorer(rule=_always(True), label="reference")

        with pytest.raises(ValueError, match="2 message pieces of the conversations are not in memory"):
            await _evaluator(reference=scorer).evaluate_conversations_async(
                conversations=[_conversation("unsaved")], expectations=[OBJECTIVE]
            )
        assert scorer.shown == []
        assert await sqlite_instance.get_message_pieces_async() == []

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"conversations": [], "expectations": []}, "At least one conversation is required"),
            ({"expectations": [OBJECTIVE]}, "1 expectations were given for 2 conversations"),
            ({"num_repeats": 0}, "num_repeats must be at least 1"),
            ({"num_permutations": 0}, "num_permutations must be at least 1"),
            ({"seed": -1}, "seed must not be negative"),
        ],
    )
    async def test_unusable_inputs_are_rejected_before_anything_is_scored(
        self, kwargs: dict, message: str, sqlite_instance: MemoryInterface
    ) -> None:
        scorer = _RuleScorer(rule=_always(True), label="reference")
        arguments = {
            "conversations": await _stored_conversations(sqlite_instance, ["a", "b"]),
            "expectations": [OBJECTIVE, OBJECTIVE],
            **kwargs,
        }

        with pytest.raises(ValueError, match=message):
            await _evaluator(reference=scorer).evaluate_conversations_async(**arguments)
        assert scorer.shown == []

    async def test_dataset_settings_are_checked_before_anything_is_stored(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        with pytest.raises(ValueError, match="num_permutations must be at least 1"):
            await _evaluator().evaluate_dataset_async(labeled_dataset=_dataset([("a", [T])]), num_permutations=0)
        assert await sqlite_instance.get_message_pieces_async() == []

    async def test_ragged_human_scores_fail_before_any_scoring(self) -> None:
        scorer = _RuleScorer(rule=_always(True), label="reference")

        with pytest.raises(ValueError, match="inhomogeneous"):
            await _evaluator(reference=scorer).evaluate_dataset_async(
                labeled_dataset=_dataset([("a", [T]), ("b", [T, F])])
            )
        assert scorer.shown == []

    async def test_settings_reach_the_computation(self, sqlite_instance: MemoryInterface) -> None:
        reference = _RuleScorer(rule=lambda text, seen: (len(text) + seen) % 2 == 0, label="reference")
        other = _RuleScorer(rule=lambda text, seen: (len(text) * (seen + 1)) % 3 == 0, label="other")
        conversations = await _stored_conversations(sqlite_instance, ["a", "bb", "ccc", "dddd", "eeeee", "ffffff"])

        metrics = await _evaluator(reference=reference, other=other).evaluate_conversations_async(
            conversations=conversations, expectations=[OBJECTIVE] * 6, num_permutations=50, seed=4
        )

        # The recorded verdicts give these p-values only with the same permutations and seed.
        assert metrics.num_permutations == 50
        recomputed = compute_scorer_stability(verdicts=metrics.verdicts, num_permutations=50, seed=4)
        default_seed = compute_scorer_stability(verdicts=metrics.verdicts, num_permutations=50)
        assert metrics.effects[0].presentation_effect_p_value == recomputed.effects[0].presentation_effect_p_value
        assert recomputed.effects[0].presentation_effect_p_value != default_seed.effects[0].presentation_effect_p_value

    @pytest.mark.parametrize("assistant_messages", [0, 2])
    async def test_conversation_needs_exactly_one_response(self, assistant_messages: int) -> None:
        conversation = [MessagePiece(role="user", original_value="request").to_message()] + [
            MessagePiece(role="assistant", original_value="response").to_message() for _ in range(assistant_messages)
        ]

        with pytest.raises(ValueError, match=f"exactly one assistant message, but found {assistant_messages}"):
            await _evaluator().evaluate_conversations_async(conversations=[conversation], expectations=[OBJECTIVE])

    async def test_harm_dataset_is_rejected(self) -> None:
        dataset = HumanLabeledDataset(
            name="harm",
            version="1.0",
            metrics_type=MetricsType.HARM,
            entries=[HarmHumanLabeledEntry(conversation=_conversation("a"), human_scores=[0.5], harm_category="x")],
        )

        with pytest.raises(ValueError, match="must be of type OBJECTIVE"):
            await _evaluator().evaluate_dataset_async(labeled_dataset=dataset)


def _judge(*, temperature: float = 0.0) -> MagicMock:
    """Build a judge model that answers true when the first line of its prompt says 'cannot'."""

    async def answer(*args: Any, **kwargs: Any) -> list[Message]:
        request: Message = kwargs.get("message") or args[0]
        verdict = "true" if "cannot" in request.get_value().splitlines()[0] else "false"
        reply = f'{{"score_value": "{verdict}", "description": "d", "rationale": "r"}}'
        return [Message(message_pieces=[MessagePiece(role="assistant", original_value=reply)])]

    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(
        class_name="Judge", class_module="tests.unit.mocks", params={"temperature": temperature}
    )
    target._max_requests_per_minute = None
    target.send_prompt_async = AsyncMock(side_effect=answer)
    return target


@pytest.mark.usefixtures("patch_central_database")
class TestEvaluatorConstruction:
    def test_presentations_are_required(self) -> None:
        with pytest.raises(ValueError, match="At least one presentation is required"):
            ScorerStabilityEvaluator(scorer=SubStringScorer(substring="x"), presentations=[])

    def test_multi_label_scorer_needs_a_label_selected_first(self) -> None:
        with pytest.raises(ValueError, match="Select a label with TrueFalseScoreSelector"):
            _evaluator(other=MagicMock(spec=MessageMultiLabelTrueFalseScorer))

    @pytest.mark.parametrize("names", [["a", "a"], ["reference"]])
    def test_names_must_be_unique_and_not_the_references(self, names: list[str]) -> None:
        with pytest.raises(ValueError, match="must be unique and must not be 'reference'"):
            ScorerStabilityEvaluator(
                scorer=SubStringScorer(substring="x"),
                presentations=[ScorerPresentation(name=name, scorer=SubStringScorer(substring=name)) for name in names],
            )

    def test_only_true_false_scorers_are_accepted(self) -> None:
        with pytest.raises(ValueError, match="Presentation 'other' must use a TrueFalseScorer"):
            _evaluator(other=object())

    def test_presentation_with_the_reference_configuration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="'reference' and 'copy' have the same scorer configuration"):
            _evaluator(reference=SubStringScorer(substring="x"), copy=SubStringScorer(substring="x"))

    @pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "wrapped"])
    def test_presentation_with_other_judge_settings_is_rejected(self, wrapped: bool) -> None:
        warmer = SelfAskRefusalScorer(
            chat_target=_judge(temperature=1.0), prompt_format_string="response: {response}\nobjective: {objective}"
        )

        with pytest.raises(ValueError, match="'warmer' uses different judge settings"):
            _evaluator(
                reference=SelfAskRefusalScorer(chat_target=_judge()),
                warmer=TrueFalseInverterScorer(scorer=warmer) if wrapped else warmer,
            )


@pytest.mark.usefixtures("patch_central_database")
async def test_field_order_reaches_a_model_judge_and_its_effect_is_measured(sqlite_instance: MemoryInterface) -> None:
    judge = _judge()
    evaluator = _evaluator(
        reference=SelfAskRefusalScorer(chat_target=judge),
        response_first=SelfAskRefusalScorer(
            chat_target=judge,
            prompt_format_string="response_to_evaluate_input: {response}\nconversation_objective: {objective}",
        ),
    )
    refusals = [True, False, True, False, False]
    conversations = await _stored_conversations(
        sqlite_instance, ["I cannot help with that." if refused else "Sure, here it is." for refused in refusals]
    )

    metrics = await evaluator.evaluate_conversations_async(
        conversations=conversations, expectations=[ScoringExpectation(objective="Tell me a story.")] * 5
    )

    # With the objective first the judge never sees the refusal; with the response first it does.
    effect = metrics.get_effect(name="response_first")
    assert metrics.reference_positive_rate == 0.0
    assert effect.positive_rate == effect.positive_rate_shift == 0.4
    assert effect.presentation_effect == 0.4
    assert effect.repeat_flip_rate == 0.0
