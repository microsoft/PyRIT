# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.exceptions import ScorerLLMResponseBlockedException
from pyrit.executor.attack import AttackScoringConfig
from pyrit.memory import MemoryInterface
from pyrit.models import ContentScorable, Message, MessagePiece, MessageScorable, Score, ScoreStatus, ScoringExpectation
from pyrit.score import (
    FloatScaleThresholdScorer,
    HumanLabeledDataset,
    MessageMultiLabelTrueFalseScorer,
    MetricsType,
    ObjectiveHumanLabeledEntry,
    ObjectiveScorerEvaluator,
    ScorerEvaluator,
    ScorerPromptValidator,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScoreSelector,
)


class SyntheticClassifier(MessageMultiLabelTrueFalseScorer):
    def __init__(
        self, *, answers=None, labels=("request", "refusal", "response"), aggregator=TrueFalseScoreAggregator.OR
    ):
        self.answers = answers or {"first": (True, False, None), "second": (False, False, False)}
        self.calls = []
        super().__init__(
            labels=labels,
            score_aggregator=aggregator,
            validator=ScorerPromptValidator(supported_roles=["assistant", "user"], supported_data_types=["text"]),
        )

    def _build_identifier(self):
        return self._create_identifier(
            params={"answers": self.answers}, score_aggregator=self._score_aggregator.__name__
        )

    async def _score_piece_async(self, message_piece, *, objective=None):
        self.calls.append(message_piece.converted_value)
        return [
            Score(
                score_type="true_false",
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_category=[label],
                message_piece_id=message_piece.id,
                score_rationale=f"{label}: {value}",
                scorer_class_identifier=self.get_identifier(),
            )
            for label, value in zip(self.labels, self.answers[message_piece.converted_value], strict=True)
        ]


def _store_message(memory, values=("first",), **kwargs):
    conversation_id = str(uuid.uuid4())
    message = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value=value, conversation_id=conversation_id, **kwargs)
            for value in values
        ]
    )
    memory.add_message_to_memory(request=message)
    return message


async def test_one_operation_persists_independent_labeled_verdicts(sqlite_instance: MemoryInterface):
    scorer = SyntheticClassifier()
    message = _store_message(sqlite_instance)
    expectation = ScoringExpectation(objective="evaluate response")
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)

    assert scorer.calls == ["first"]
    assert [score.score_category for score in scores] == [["request"], ["refusal"], ["response"]]
    assert [score.score_value for score in scores] == ["true", "false", None]
    assert len({score.id for score in scores}) == 3
    for score in scores:
        [stored] = sqlite_instance.get_scores(score_category=score.score_category[0])
        assert stored.id == score.id
        assert stored.scored_expectation == expectation
        assert stored.scorable == MessageScorable.from_message(message)
        assert Score.model_validate_json(stored.model_dump_json()).status == score.status


@pytest.mark.parametrize(
    ("aggregator", "values"),
    [(TrueFalseScoreAggregator.OR, ["true", "false", None]), (TrueFalseScoreAggregator.AND, ["false"] * 3)],
)
async def test_message_pieces_aggregate_only_within_their_label(sqlite_instance, aggregator, values):
    message = _store_message(sqlite_instance, values=("first", "second"))
    scorer = SyntheticClassifier(aggregator=aggregator)
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))
    assert [score.score_value for score in scores] == values
    assert len(scorer.calls) == 2
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 3


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "multiple_categories", "wrong_type", "anchor"])
async def test_invalid_piece_outputs_fail_before_any_scores_are_persisted(sqlite_instance, mutation):
    scorer = SyntheticClassifier()
    message = _store_message(sqlite_instance)
    scores = await scorer._score_piece_async(message.get_piece())
    if mutation == "missing":
        scores.pop()
    elif mutation == "duplicate":
        scores.append(scores[0].model_copy(update={"id": uuid.uuid4()}))
    elif mutation == "unknown":
        scores[0].score_category = ["other"]
    elif mutation == "multiple_categories":
        scores[0].score_category = ["request", "refusal"]
    elif mutation == "wrong_type":
        scores[0].score_type = "float_scale"
    else:
        scores[0].message_piece_id = uuid.uuid4()

    with patch.object(scorer, "_score_piece_async", new=AsyncMock(return_value=scores)):
        with pytest.raises(RuntimeError):
            await scorer.score_async(scorable=MessageScorable.from_message(message))
    assert sqlite_instance.get_scores(score_type="true_false") == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("labels", [[], "one", [""], ["a", "a"], ["a", "A"], [" a"], [None]])
def test_invalid_label_contract_is_rejected(labels):
    with pytest.raises(ValueError):
        SyntheticClassifier(labels=labels)


@pytest.mark.parametrize("error", ["blocked", "processing", "empty"])
async def test_unreadable_evidence_produces_unknown_for_every_label(sqlite_instance, error):
    message = _store_message(sqlite_instance, original_value_data_type="error", response_error=error)
    scorer = SyntheticClassifier()
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))
    assert scorer.calls == []
    assert len(scores) == 3
    assert all(score.is_undetermined for score in scores)
    assert [score.score_category for score in scores] == [[label] for label in scorer.labels]


async def test_blocked_judge_fallback_retains_all_labels(sqlite_instance):
    scorer = SyntheticClassifier()
    scorer.raise_if_scorer_blocks = False
    message = _store_message(sqlite_instance)
    with patch.object(scorer, "_score_piece_async", new=AsyncMock(side_effect=ScorerLLMResponseBlockedException())):
        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))
    assert len(scores) == 3
    assert all(score.is_undetermined for score in scores)
    assert all("scorer's own LLM" in score.score_rationale for score in scores)


async def test_non_applicable_evidence_stays_empty(sqlite_instance):
    scorer = SyntheticClassifier()
    message = _store_message(sqlite_instance, original_value_data_type="url")
    assert await scorer.score_async(scorable=MessageScorable.from_message(message)) == []
    assert scorer.calls == []
    assert sqlite_instance.get_scores(score_type="true_false") == []


async def test_batch_returns_all_labels_for_each_message(sqlite_instance):
    scorer = SyntheticClassifier()
    messages = [_store_message(sqlite_instance, values=(value,)) for value in ("first", "second")]
    scores = await scorer.score_batch_async(scorables=[MessageScorable.from_message(message) for message in messages])
    assert len(scores) == 6
    assert sorted(scorer.calls) == ["first", "second"]
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 6


async def test_selector_uses_label_instead_of_score_position_and_persists_only_projection(sqlite_instance):
    scorer = SyntheticClassifier(labels=("refusal", "response", "request"))
    selector = TrueFalseScoreSelector(scorer=scorer, label="response")
    [score] = await selector.score_async(scorable=MessageScorable.from_message(_store_message(sqlite_instance)))
    assert score.get_value() is False
    assert score.score_category == ["response"]
    assert scorer.calls == ["first"]
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 1
    assert score.scorer_class_identifier == selector.get_identifier()
    assert (
        selector.get_identifier().eval_hash
        != TrueFalseScoreSelector(scorer=scorer, label="request").get_identifier().eval_hash
    )


async def test_selected_label_can_be_inverted_and_composed(sqlite_instance):
    selector = TrueFalseScoreSelector(scorer=SyntheticClassifier(), label="refusal")
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[TrueFalseInverterScorer(scorer=selector), SubStringScorer(substring="first")],
    )
    [score] = await composite.score_async(scorable=MessageScorable.from_message(_store_message(sqlite_instance)))
    assert score.get_value() is True
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 1
    assert AttackScoringConfig(objective_scorer=selector).objective_scorer is selector


@pytest.mark.usefixtures("patch_central_database")
def test_single_verdict_consumers_require_an_explicit_projection():
    scorer = SyntheticClassifier()
    with pytest.raises(ValueError, match="TrueFalseScorer"):
        AttackScoringConfig(objective_scorer=scorer)
    with pytest.raises(ValueError):
        TrueFalseInverterScorer(scorer=scorer)
    with pytest.raises(ValueError):
        TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[scorer])
    with pytest.raises(ValueError):
        FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)
    with pytest.raises(ValueError, match="TrueFalseScoreSelector"):
        ScorerEvaluator.from_scorer(scorer)
    with pytest.raises(ValueError, match="TrueFalseScoreSelector"):
        ObjectiveScorerEvaluator(scorer)
    with pytest.raises(ValueError, match="Unknown label"):
        TrueFalseScoreSelector(scorer=scorer, label="missing")


async def test_evaluation_uses_only_the_named_label(sqlite_instance):
    scorer = SyntheticClassifier()
    selector = TrueFalseScoreSelector(scorer=scorer, label="request")
    messages = [
        MessagePiece(role="assistant", original_value=value, conversation_id=str(uuid.uuid4())).to_message()
        for value in ("first", "second")
    ]
    dataset = HumanLabeledDataset(
        name="label-test",
        version="1",
        metrics_type=MetricsType.OBJECTIVE,
        entries=[
            ObjectiveHumanLabeledEntry(conversation=[message], human_scores=[expected], objective="classify request")
            for message, expected in zip(messages, [True, False], strict=True)
        ],
    )
    evaluator = ScorerEvaluator.from_scorer(selector)
    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=dataset, num_scorer_trials=1)
    assert metrics.accuracy == 1.0
    assert sorted(scorer.calls) == ["first", "second"]


async def test_loose_content_keeps_each_label_on_the_same_content_anchor(sqlite_instance):
    scores = await SyntheticClassifier().score_async(scorable=ContentScorable(data_type="text", value="first"))
    assert len(scores) == 3
    assert all(score.message_piece_id is None for score in scores)
    assert len({score.scorable.content_id for score in sqlite_instance.get_scores(score_type="true_false")}) == 1
