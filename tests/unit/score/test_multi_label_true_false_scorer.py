# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_image_message_piece, store_message_async

from pyrit.exceptions import ScorerLLMResponseBlockedException
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    ComponentIdentifier,
    Contains,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    OutputMatches,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    FloatScaleThresholdScorer,
    MessageMultiLabelTrueFalseScorer,
    MultiLabelTrueFalseScorer,
    Scorer,
    ScorerEvaluator,
    ScorerPromptValidator,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScoreSelector,
)
from pyrit.score.observation.target_judge import JudgmentRequest, TargetJudge
from pyrit.score.response_handler import JsonSchemaResponseHandler
from pyrit.score.scorer_evaluation.scorer_evaluator import ObjectiveScorerEvaluator
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc

pytestmark = pytest.mark.usefixtures("patch_central_database")

LABELS = ("harmful_request", "response_refusal", "harmful_response")
VERDICTS = {"harmful_request": True, "response_refusal": True, "harmful_response": False}


class SyntheticMultiLabelScorer(MessageMultiLabelTrueFalseScorer):
    """Read independently labeled verdicts supplied as JSON instead of calling a model."""

    def __init__(
        self,
        *,
        labels: Sequence[str] = LABELS,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        super().__init__(
            labels=labels,
            validator=ScorerPromptValidator(supported_data_types=["text"]),
            score_aggregator=score_aggregator,
        )
        self.calls: list[ScoringExpectation | None] = []

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        self.calls.append(expectation)
        verdicts = json.loads(message_piece.converted_value)
        return [
            Score(
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_category=[label],
                score_rationale=f"Verdict for {label}",
                score_metadata={"label": label},
                message_piece_id=message_piece.id,
                scorer_class_identifier=self.get_identifier(),
                scored_expectation=expectation,
            )
            for label, value in verdicts.items()
        ]


class SyntheticJudgeScorer(SyntheticMultiLabelScorer):
    """Exercise the real judge, parsing, observation, and persistence contracts."""

    CONDITION_TYPE = OutputMatches

    def __init__(self, *, chat_target: PromptTarget) -> None:
        super().__init__()
        self._prompt_target = chat_target
        self._judge = TargetJudge(target=chat_target, requirements=type(self).TARGET_REQUIREMENTS)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"labels": list(self.labels)},
            score_aggregator=self._score_aggregator.__name__,
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        self.calls.append(expectation)
        unvalidated = await self._judge.judge_async(
            request=self._capture_judgment_evidence(
                JudgmentRequest(
                    expectation=expectation,
                    system_prompt=None,
                    value=message_piece.converted_value,
                    data_type="text",
                    scored_prompt_id=message_piece.id,
                    scorer_identifier=self.get_identifier(),
                )
            ),
            response_handler=JsonSchemaResponseHandler(),
        )
        return [
            unvalidated.to_score(score_value=str(value).lower(), score_type="true_false").model_copy(
                update={"score_category": [label]}
            )
            for label, value in json.loads(unvalidated.raw_score_value).items()
        ]


async def test_one_piece_preserves_three_independent_persisted_verdicts(sqlite_instance: MemoryInterface) -> None:
    scorer = SyntheticMultiLabelScorer()
    scores = await scorer.score_async(scorable=ContentScorable(value=json.dumps(VERDICTS)))

    assert len(scorer.calls) == 1
    assert [score.score_category for score in scores] == [[label] for label in LABELS]
    assert [score.get_value() for score in scores] == [True, True, False]
    assert len({score.id for score in scores}) == 3
    for score in scores:
        saved = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
        assert saved[0] == score
        assert saved[0].message_piece_id is None
        assert Score.model_validate_json(score.model_dump_json()) == score
        assert await sqlite_instance.get_scores_async(score_category=score.score_category[0]) == saved


@pytest.mark.parametrize(
    ("aggregator", "expected"),
    [(TrueFalseScoreAggregator.OR, [True, True, False]), (TrueFalseScoreAggregator.AND, [False, False, False])],
)
async def test_pieces_aggregate_only_within_each_label(
    aggregator: TrueFalseAggregatorFunc, expected: list[bool], sqlite_instance: MemoryInterface
) -> None:
    scorer = SyntheticMultiLabelScorer(score_aggregator=aggregator)
    first = MessagePiece(role="assistant", original_value=json.dumps(VERDICTS))
    second = MessagePiece(
        role="assistant",
        original_value=json.dumps(dict.fromkeys(reversed(LABELS), False)),
        conversation_id=first.conversation_id,
    )
    message = await store_message_async(Message(message_pieces=[first, second]))

    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

    assert len(scorer.calls) == 2
    assert [score.get_value() for score in scores] == expected
    assert [score.score_category for score in scores] == [[label] for label in LABELS]
    assert all(score.scorable == MessageScorable.from_message(message) for score in scores)
    assert all(score.score_metadata == {"label": score.score_category[0]} for score in scores)
    assert len(await sqlite_instance.get_scores_async(score_type="true_false")) == 3


@pytest.mark.parametrize(
    ("aggregator", "second", "expected"),
    [
        (TrueFalseScoreAggregator.OR, False, None),
        (TrueFalseScoreAggregator.OR, True, True),
        (TrueFalseScoreAggregator.AND, True, None),
        (TrueFalseScoreAggregator.AND, False, False),
    ],
)
async def test_undetermined_verdicts_follow_the_existing_three_valued_aggregation(
    aggregator: TrueFalseAggregatorFunc, second: bool, expected: bool | None
) -> None:
    scorer = SyntheticMultiLabelScorer(score_aggregator=aggregator)
    first = MessagePiece(role="assistant", original_value=json.dumps(dict.fromkeys(LABELS, None)))
    message = await store_message_async(
        Message(
            message_pieces=[
                first,
                MessagePiece(
                    role="assistant",
                    original_value=json.dumps(dict.fromkeys(LABELS, second)),
                    conversation_id=first.conversation_id,
                ),
            ]
        )
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

    assert all(score.is_undetermined == (expected is None) for score in scores)
    if expected is not None:
        assert [score.get_value() for score in scores] == [expected] * 3


@pytest.mark.parametrize("labels", [[], "label", [1], [""], [" "], [" spaced"], ["a", "a"], ["A", "a"]])
def test_invalid_label_declarations_are_rejected(labels: Any) -> None:
    with pytest.raises(ValueError, match="label"):
        SyntheticMultiLabelScorer(labels=labels)


def test_labels_are_snapshotted_and_part_of_the_behavioral_identity() -> None:
    labels = ["first", "second"]
    scorer = SyntheticMultiLabelScorer(labels=labels)
    labels.append("third")
    assert scorer.labels == ("first", "second")
    assert scorer.scorer_type == "true_false"
    assert isinstance(scorer, MultiLabelTrueFalseScorer)
    assert scorer.get_scorer_metrics() is None
    assert scorer.get_identifier() != SyntheticMultiLabelScorer(labels=["first"]).get_identifier()
    assert (
        scorer.get_identifier()
        != SyntheticMultiLabelScorer(
            labels=["first", "second"],
            score_aggregator=TrueFalseScoreAggregator.AND,
        ).get_identifier()
    )


@pytest.mark.parametrize(
    "invalid", ["missing", "unknown", "duplicate", "multiple_categories", "unlabeled", "float", "value", "id"]
)
async def test_invalid_piece_contracts_fail_before_any_scores_are_saved(
    invalid: str, sqlite_instance: MemoryInterface
) -> None:
    scorer = SyntheticMultiLabelScorer()
    scores = [
        Score(
            score_value="true",
            score_type="true_false",
            score_category=[label],
            scorer_class_identifier=scorer.get_identifier(),
        )
        for label in LABELS
    ]
    if invalid == "missing":
        scores.pop()
    elif invalid == "unknown":
        scores[-1].score_category = ["unknown"]
    elif invalid == "duplicate":
        scores[-1].score_category = [LABELS[0]]
    elif invalid == "multiple_categories":
        scores[0].score_category = list(LABELS)
    elif invalid == "unlabeled":
        scores[0].score_category = []
    elif invalid == "float":
        scores[0].score_type = "float_scale"
        scores[0].score_value = "0.5"
    elif invalid == "value":
        scores[0].score_value = "invalid"
    else:
        scores[0].id = scores[1].id

    with patch.object(scorer, "_score_piece_with_expectation_async", new_callable=AsyncMock) as score_piece:
        score_piece.return_value = scores
        with pytest.raises(RuntimeError, match="scorer"):
            await scorer.score_async(scorable=ContentScorable(value="evidence"))
    assert await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True) == []


async def test_each_piece_must_return_the_complete_contract() -> None:
    scorer = SyntheticMultiLabelScorer()
    first = MessagePiece(role="assistant", original_value=json.dumps({LABELS[0]: True}))
    message = await store_message_async(
        Message(
            message_pieces=[
                first,
                MessagePiece(
                    role="assistant",
                    original_value=json.dumps(dict.fromkeys(LABELS[1:], False)),
                    conversation_id=first.conversation_id,
                ),
            ]
        )
    )
    with pytest.raises(RuntimeError, match="each declared label"):
        await scorer.score_async(scorable=MessageScorable.from_message(message))


@pytest.mark.parametrize("select_label", [False, True])
async def test_selector_returns_one_public_result_and_retains_all_children(
    select_label: bool, sqlite_instance: MemoryInterface
) -> None:
    source = SyntheticMultiLabelScorer()
    scorer = TrueFalseScoreSelector(scorer=source, label="harmful_response") if select_label else source
    scores = await scorer.score_async(scorable=ContentScorable(value=json.dumps(VERDICTS)))
    assert len(source.calls) == 1
    assert [score.get_value() for score in scores] == ([False] if select_label else [True, True, False])
    saved = await sqlite_instance.get_scores_async(score_type="true_false")
    all_saved = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
    assert {score.id for score in saved} == {score.id for score in scores}
    assert len(all_saved) == (4 if select_label else 3)
    if select_label:
        child = [score for score in all_saved if score.id not in {item.id for item in saved}]
        assert {score.score_category[0] for score in child} == set(LABELS)
        assert all(score.scorer_class_identifier == source.get_identifier() for score in child)
        assert saved[0].scorer_class_identifier == scorer.get_identifier()
        assert saved[0].id not in {score.id for score in child}
        assert await sqlite_instance.get_scores_async(score_category=LABELS[0]) == []
        retained = await sqlite_instance.get_scores_async(score_category=LABELS[0], include_intermediate=True)
        assert len(retained) == 1
        assert retained[0].get_value() is True


@pytest.mark.parametrize("label", ["harmful_request", "harmful_response"])
async def test_selector_composes_with_boolean_wrappers(label: str) -> None:
    source = SyntheticMultiLabelScorer()
    selected = TrueFalseScoreSelector(scorer=source, label=label)
    inverted = TrueFalseInverterScorer(scorer=selected)
    scores = await inverted.score_async(scorable=ContentScorable(value=json.dumps(VERDICTS)))
    assert scores[0].get_value() is not VERDICTS[label]
    assert len(source.calls) == 1
    composite = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[selected])
    assert (await composite.score_async(scorable=ContentScorable(value=json.dumps(VERDICTS))))[
        0
    ].get_value() == VERDICTS[label]


@pytest.mark.parametrize("consumer", ["attack", "refusal", "inverter", "composite", "threshold", "evaluation"])
def test_raw_multi_label_scores_require_explicit_selection(consumer: str) -> None:
    source = SyntheticMultiLabelScorer()
    with pytest.raises(ValueError):
        if consumer == "attack":
            AttackScoringConfig(objective_scorer=source)
        elif consumer == "refusal":
            AttackScoringConfig(refusal_scorer=source)
        elif consumer == "inverter":
            TrueFalseInverterScorer(scorer=source)
        elif consumer == "composite":
            TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[source])
        elif consumer == "threshold":
            FloatScaleThresholdScorer(scorer=source, threshold=0.5)
        else:
            ScorerEvaluator.from_scorer(source)


def test_selector_identity_and_invalid_selection() -> None:
    source = SyntheticMultiLabelScorer()
    first = TrueFalseScoreSelector(scorer=source, label=LABELS[0])
    last = TrueFalseScoreSelector(scorer=source, label=LABELS[-1])
    assert first.get_identifier() != last.get_identifier()
    assert isinstance(ScorerEvaluator.from_scorer(last), ObjectiveScorerEvaluator)
    AttackScoringConfig(objective_scorer=last)
    with pytest.raises(ValueError, match="Unknown label"):
        TrueFalseScoreSelector(scorer=source, label="unknown")
    with pytest.raises(ValueError, match="MultiLabelTrueFalseScorer"):
        TrueFalseScoreSelector(scorer=SubStringScorer(substring="test"), label=LABELS[0])


async def test_batch_scoring_keeps_expectations_and_categories_independent() -> None:
    source = SyntheticMultiLabelScorer()
    expectations = [ScoringExpectation(objective=f"objective {index}") for index in range(2)]
    scores = await source.score_batch_async(
        scorables=[ContentScorable(value=json.dumps(VERDICTS)) for _ in range(2)],
        expectations=expectations,
        batch_size=2,
    )
    assert len(scores) == 6
    assert source.calls == expectations
    assert [score.scored_expectation for score in scores] == [item for item in expectations for _ in LABELS]


async def test_selector_batching_discovers_the_child_target_rate_limit() -> None:
    source = SyntheticJudgeScorer(chat_target=MockPromptTarget(rpm=60))
    selector = TrueFalseScoreSelector(scorer=source, label=LABELS[-1])
    assert selector.get_chat_target() is source.get_chat_target()
    with pytest.raises(ValueError, match="Batch size"):
        await selector.score_batch_async(scorables=[ContentScorable(value="test")], batch_size=2)
    assert source.calls == []


@pytest.mark.parametrize("select_label", [False, True])
async def test_target_judge_retains_one_observation_and_full_expectation(
    select_label: bool, sqlite_instance: MemoryInterface
) -> None:
    target = MockPromptTarget()
    source = SyntheticJudgeScorer(chat_target=target)
    scorer: Scorer = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    expectation = ScoringExpectation(objective="context", conditions=(OutputMatches(matcher=Contains(value="test")),))
    message = await store_message_async(MessagePiece(role="assistant", original_value="test evidence").to_message())
    response = MessagePiece(
        role="assistant",
        original_value=json.dumps({"score_value": json.dumps(VERDICTS), "rationale": "classifier"}),
    ).to_message()

    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.return_value = [response]
        scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)
        assert send.call_count == 1
    assert source.calls == [expectation]
    all_scores = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
    assert len(all_scores) == (4 if select_label else 3)
    assert all(score.scored_expectation == expectation for score in all_scores)
    observation_ids = {identifier for score in all_scores for identifier in score.observation_ids}
    assert len(observation_ids) == 1
    observations = await sqlite_instance.get_observations_async(observation_ids=list(observation_ids))
    assert len(observations) == 1
    assert all(score.message_piece_id == message.message_pieces[0].id for score in all_scores)
    assert [score.get_value() for score in scores] == ([False] if select_label else [True, True, False])


@pytest.mark.parametrize("response_error", ["blocked", "processing"])
@pytest.mark.parametrize("select_label", [False, True])
async def test_unreadable_or_blocked_message_has_undetermined_labeled_verdicts(
    response_error: str, select_label: bool
) -> None:
    source = SyntheticMultiLabelScorer()
    scorer = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    message = await store_message_async(
        MessagePiece(
            role="assistant",
            original_value="",
            response_error=response_error,
        ).to_message()
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))
    assert source.calls == []
    assert len(scores) == (1 if select_label else 3)
    assert all(score.is_undetermined for score in scores)
    assert len({score.id for score in scores}) == len(scores)
    assert [score.score_category for score in scores] == (
        [[LABELS[-1]]] if select_label else [[label] for label in LABELS]
    )


@pytest.mark.parametrize("select_label", [False, True])
async def test_suppressed_judge_block_expands_and_propagates_policy(select_label: bool) -> None:
    source = SyntheticMultiLabelScorer()
    original = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    scorer = original.with_scorer_block_policy(raise_if_scorer_blocks=False)
    assert scorer is not original
    assert source.raise_if_scorer_blocks is True
    scoped_source = scorer._scorer if select_label else scorer
    with patch.object(scoped_source, "_score_piece_with_expectation_async", new_callable=AsyncMock) as score_piece:
        score_piece.side_effect = ScorerLLMResponseBlockedException()
        scores = await scorer.score_async(scorable=ContentScorable(value="evidence"))
    assert len(scores) == (1 if select_label else 3)
    assert all(score.is_undetermined for score in scores)
    assert scorer.with_scorer_block_policy(raise_if_scorer_blocks=False) is scorer


async def test_unsupported_message_role_does_not_create_verdicts() -> None:
    source = SyntheticMultiLabelScorer()
    message = await store_message_async(MessagePiece(role="simulated_assistant", original_value="ignored").to_message())
    assert await source.score_async(scorable=MessageScorable.from_message(message)) == []
    assert source.calls == []


@pytest.mark.parametrize("select_label", [False, True])
async def test_non_applicable_piece_does_not_invent_missing_labels(
    select_label: bool, sqlite_instance: MemoryInterface
) -> None:
    source = SyntheticMultiLabelScorer()
    scorer = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    with patch.object(source, "_score_piece_with_expectation_async", new_callable=AsyncMock) as score_piece:
        score_piece.return_value = []
        assert await scorer.score_async(scorable=ContentScorable(value="evidence")) == []
    assert await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True) == []


@pytest.mark.parametrize("select_label", [False, True])
async def test_unsupported_modality_does_not_create_verdicts(select_label: bool) -> None:
    source = SyntheticMultiLabelScorer()
    scorer = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    message = await store_message_async(get_image_message_piece().to_message())
    assert await scorer.score_async(scorable=MessageScorable.from_message(message)) == []
    assert source.calls == []


@pytest.mark.parametrize("select_label", [False, True])
async def test_real_judge_block_retains_its_observation_for_every_label(
    select_label: bool, sqlite_instance: MemoryInterface
) -> None:
    target = MockPromptTarget()
    source = SyntheticJudgeScorer(chat_target=target)
    root = TrueFalseScoreSelector(scorer=source, label=LABELS[-1]) if select_label else source
    scorer = root.with_scorer_block_policy(raise_if_scorer_blocks=False)
    expectation = ScoringExpectation(objective="context", conditions=(OutputMatches(matcher=Contains(value="test")),))
    message = await store_message_async(MessagePiece(role="assistant", original_value="test evidence").to_message())
    response = MessagePiece(
        role="assistant", original_value="blocked", original_value_data_type="error", response_error="blocked"
    ).to_message()

    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.return_value = [response]
        scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)
        assert send.call_count == 1
    assert all(score.is_undetermined for score in scores)
    all_scores = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
    assert len(all_scores) == (4 if select_label else 3)
    assert len({score.id for score in all_scores}) == len(all_scores)
    assert all(score.scored_expectation == expectation for score in all_scores)
    observation_ids = {identifier for score in all_scores for identifier in score.observation_ids}
    assert len(observation_ids) == 1
    observations = await sqlite_instance.get_observations_async(observation_ids=list(observation_ids))
    assert len(observations) == 1
    assert observations[0].metadata == {"reason": "scorer_response_blocked"}


async def test_default_judge_block_policy_still_raises() -> None:
    source = SyntheticMultiLabelScorer()
    with patch.object(source, "_score_piece_with_expectation_async", new_callable=AsyncMock) as score_piece:
        score_piece.side_effect = ScorerLLMResponseBlockedException()
        with pytest.raises(ScorerLLMResponseBlockedException):
            await TrueFalseScoreSelector(scorer=source, label=LABELS[-1]).score_async(
                scorable=ContentScorable(value="evidence")
            )


@pytest.mark.parametrize("label", ["harmful_request", "harmful_response"])
async def test_evaluation_uses_the_selected_label(label: str) -> None:
    source = SyntheticMultiLabelScorer()
    selector = TrueFalseScoreSelector(scorer=source, label=label)
    evaluator = ScorerEvaluator.from_scorer(selector)
    message = await store_message_async(
        MessagePiece(role="assistant", original_value=json.dumps(VERDICTS)).to_message()
    )
    groups = await evaluator._score_responses_grouped_async(
        responses=[message],
        objectives=["context"],
        max_concurrency=1,
    )
    score = evaluator._select_evaluation_score(scores=groups[0], harm_category=None)
    assert score is not None
    assert score.get_value() is VERDICTS[label]
    assert score.score_category == [label]
    assert len(source.calls) == 1


async def test_attack_outcome_uses_the_selected_label_instead_of_the_first_verdict() -> None:
    target = MockPromptTarget()
    source = SyntheticMultiLabelScorer()
    source._validator = ScorerPromptValidator(supported_data_types=["text"], is_objective_required=True)
    selector = TrueFalseScoreSelector(scorer=source, label=LABELS[-1])
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=selector),
    )
    response = MessagePiece(role="assistant", original_value=json.dumps(VERDICTS)).to_message()
    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.return_value = [response]
        result = await attack.execute_async(objective="test objective")
    assert result.outcome == AttackOutcome.FAILURE
    assert len(source.calls) == 1
