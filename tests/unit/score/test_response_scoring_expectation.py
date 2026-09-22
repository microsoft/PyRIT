# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target

from pyrit.exceptions import ComponentRole, execution_context, get_execution_context
from pyrit.models import (
    ComponentIdentifier,
    Condition,
    ContentEntryScorable,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    Scorable,
    ScorableUnion,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score import (
    FloatScaleScorer,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorer,
    ScorerPromptValidator,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.exceptions import ExecutionContext
    from pyrit.memory import MemoryInterface


class _FirstCondition(Condition):
    condition_type: Literal["test_response_first"] = "test_response_first"
    value: str = "first"


class _SecondCondition(Condition):
    condition_type: Literal["test_response_second"] = "test_response_second"
    value: str = "second"


class _MessageRecordingScorer(MessageTrueFalseScorer):
    MATCHED_CONDITIONS = frozenset({_FirstCondition})
    REQUIRED_CONDITIONS = MATCHED_CONDITIONS

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator())
        self.expectations: list[ScoringExpectation | None] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_prepared_message_async(
        self, *, message: Message, expectation: ScoringExpectation | None
    ) -> list[Score]:
        self.expectations.append(expectation)
        return await super()._score_prepared_message_async(message=message, expectation=expectation)

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_type="true_false",
                message_piece_id=message_piece.id,
                objective=objective,
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _GenericRecordingScorer(TrueFalseScorer):
    MATCHED_CONDITIONS = frozenset({_SecondCondition})
    REQUIRED_CONDITIONS = MATCHED_CONDITIONS

    def __init__(self) -> None:
        super().__init__()
        self.expectations: list[ScoringExpectation | None] = []
        self.contexts: list[ExecutionContext | None] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        self.expectations.append(expectation)
        await asyncio.sleep(0)
        self.contexts.append(get_execution_context())
        return [
            Score(
                score_value="false",
                score_type="true_false",
                scorable=cast("ScorableUnion", scorable),
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _MultipleContentScorer(FloatScaleScorer):
    MATCHED_CONDITIONS = frozenset({_SecondCondition})

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        await asyncio.sleep(0)
        return [
            Score(
                score_value=value,
                score_type="float_scale",
                scorable=cast("ScorableUnion", scorable),
                scorer_class_identifier=self.get_identifier(),
            )
            for value in ("0.25", "0.75")
        ]


def _expectation(objective: str | None = "scoring context") -> ScoringExpectation:
    return ScoringExpectation(objective=objective, conditions=(_FirstCondition(), _SecondCondition()))


def _stored_response(memory: MemoryInterface) -> Message:
    response = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4())).to_message()
    memory.add_message_to_memory(request=response)
    return response


async def _score_response_async(
    *,
    response: Message,
    scorers: list[Scorer],
    multiple: bool,
    **kwargs: Any,
) -> list[Score]:
    if multiple:
        return await MessageScorer.score_response_multiple_scorers_async(response=response, scorers=scorers, **kwargs)
    result = await MessageScorer.score_response_async(
        response=response,
        objective_scorer=scorers[0] if scorers else None,
        auxiliary_scorers=scorers[1:],
        **kwargs,
    )
    return result["objective_scores"] + result["auxiliary_scores"]


@pytest.mark.parametrize("method", ["score_response_async", "score_response_multiple_scorers_async"])
async def test_deprecated_response_shim_forwards_all_arguments_async(method: str) -> None:
    objective, auxiliary = MagicMock(spec=Scorer), MagicMock(spec=Scorer)
    kwargs = {
        "response": Message.from_prompt(prompt="response", role="assistant"),
        "expectation": _expectation(),
        "objective": None,
        "role_filter": "assistant",
        "skip_on_error_result": False,
    }
    kwargs.update(
        {"objective_scorer": objective, "auxiliary_scorers": [auxiliary]}
        if method == "score_response_async"
        else {"scorers": [objective, auxiliary]}
    )
    with (
        patch.object(MessageScorer, method, new_callable=AsyncMock) as forward,
        pytest.warns(DeprecationWarning, match=f"Scorer.{method}"),
    ):
        result = await getattr(Scorer, method)(**kwargs)

    forward.assert_awaited_once()
    assert forward.call_args.kwargs == kwargs
    assert forward.call_args.kwargs["expectation"] is kwargs["expectation"]
    assert result is forward.return_value


@pytest.mark.usefixtures("patch_central_database")
class TestResponseScoringExpectation:
    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize("objective_context", [None, "", "scoring context"])
    async def test_full_expectation_reaches_each_root_and_persists_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool, objective_context: str | None
    ) -> None:
        response = _stored_response(sqlite_instance)
        expectation = _expectation(objective_context)
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        with (
            patch.object(objective, "score_async", wraps=objective.score_async) as objective_spy,
            patch.object(auxiliary, "score_async", wraps=auxiliary.score_async) as auxiliary_spy,
        ):
            scores = await _score_response_async(
                response=response, scorers=[objective, auxiliary], multiple=multiple, expectation=expectation
            )

        assert len(scores) == 2
        for scorer, spy in ((objective, objective_spy), (auxiliary, auxiliary_spy)):
            assert scorer.expectations and all(item is expectation for item in scorer.expectations)
            spy.assert_awaited_once()
            assert spy.call_args.kwargs["expectation"] is expectation
            assert spy.call_args.kwargs["scorable"] == MessageScorable.from_message(response)
        assert all(score.scored_expectation is expectation for score in scores)
        stored = sqlite_instance.get_scores(score_ids=[score.id for score in scores])
        assert len(stored) == 2
        assert all(score.scored_expectation == expectation for score in stored)
        assert all(score.scorable == MessageScorable.from_message(response) for score in stored)

    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize(
        ("conditions", "error"),
        [
            ((_FirstCondition(),), "requires the condition"),
            ((_SecondCondition(),), "requires the condition"),
            ((_FirstCondition(), _FirstCondition(), _SecondCondition()), "at most one condition"),
        ],
    )
    async def test_invalid_group_fails_before_any_scoring_async(
        self, *, multiple: bool, conditions: tuple[Condition, ...], error: str
    ) -> None:
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        with pytest.raises(ValueError, match=error):
            await _score_response_async(
                response=MessagePiece(role="assistant", original_value="not stored").to_message(),
                scorers=[objective, auxiliary],
                multiple=multiple,
                expectation=ScoringExpectation(conditions=conditions),
            )
        assert objective.expectations == auxiliary.expectations == []

    @pytest.mark.parametrize("multiple", [False, True])
    async def test_no_scorers_reject_conditions_async(self, multiple: bool) -> None:
        response = MessagePiece(role="assistant", original_value="unused").to_message()
        with pytest.raises(ValueError, match="does not match the condition"):
            await _score_response_async(response=response, scorers=[], multiple=multiple, expectation=_expectation())

    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize("objective", [None, "", "context"])
    @pytest.mark.filterwarnings("error::DeprecationWarning")
    async def test_no_conditions_preserves_legacy_behavior_without_warning_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool, objective: str | None
    ) -> None:
        expectation = ScoringExpectation(objective=objective)
        scores = await _score_response_async(
            response=_stored_response(sqlite_instance),
            scorers=[_MessageRecordingScorer(), _GenericRecordingScorer()],
            multiple=multiple,
            expectation=expectation,
            objective=None,
        )
        assert len(scores) == 2
        assert all(score.scored_expectation is expectation for score in scores)

    @pytest.mark.parametrize("multiple", [False, True])
    async def test_omitted_expectation_keeps_default_objective_none_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool
    ) -> None:
        scores = await _score_response_async(
            response=_stored_response(sqlite_instance),
            scorers=[_MessageRecordingScorer()],
            multiple=multiple,
        )
        assert scores[0].scored_expectation == ScoringExpectation(objective=None)

    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize("objective", ["", "legacy context"])
    async def test_legacy_objective_warns_and_forwards_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool, objective: str
    ) -> None:
        with pytest.warns(DeprecationWarning, match="objective argument.*2.0.0"):
            scores = await _score_response_async(
                response=_stored_response(sqlite_instance),
                scorers=[_MessageRecordingScorer()],
                multiple=multiple,
                objective=objective,
            )
        assert scores[0].scored_expectation == ScoringExpectation(objective=objective)

    @pytest.mark.parametrize("multiple", [False, True])
    async def test_conflicting_inputs_are_rejected_async(self, multiple: bool) -> None:
        with pytest.raises(ValueError, match="either 'objective' or 'expectation'"):
            await _score_response_async(
                response=MessagePiece(role="assistant", original_value="unused").to_message(),
                scorers=[],
                multiple=multiple,
                expectation=_expectation(),
                objective="",
            )

    @pytest.mark.parametrize("multiple", [False, True])
    async def test_response_group_rejects_untyped_expectation_async(self, multiple: bool) -> None:
        with pytest.raises(TypeError, match=r"^expectation must be a ScoringExpectation or None\.$"):
            await _score_response_async(
                response=Message.from_prompt(prompt="unused", role="assistant"),
                scorers=[],
                multiple=multiple,
                expectation="not typed",
            )

    @pytest.mark.parametrize("scorer_type", [_MessageRecordingScorer, _GenericRecordingScorer])
    async def test_direct_root_ignores_other_condition_types_async(
        self, *, sqlite_instance: MemoryInterface, scorer_type: type[Scorer]
    ) -> None:
        scorer = scorer_type()
        expectation = _expectation()
        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(_stored_response(sqlite_instance)), expectation=expectation
        )
        assert len(scores) == 1
        assert scores[0].scored_expectation is expectation

    @pytest.mark.parametrize("duplicate", [False, True])
    async def test_direct_root_still_checks_its_own_criteria_async(self, duplicate: bool) -> None:
        scorer = _GenericRecordingScorer()
        conditions = (_SecondCondition(), _SecondCondition()) if duplicate else (_FirstCondition(),)
        with pytest.raises(ValueError, match="at most one condition" if duplicate else "requires the condition"):
            await scorer.score_async(
                scorable=ContentScorable(value="unused"), expectation=ScoringExpectation(conditions=conditions)
            )

    @pytest.mark.parametrize("scorer_type", [_MessageRecordingScorer, _GenericRecordingScorer])
    async def test_direct_root_rejects_untyped_expectation_async(self, scorer_type: type[Scorer]) -> None:
        with pytest.raises(TypeError, match=r"^expectation must be a ScoringExpectation or None\.$"):
            await scorer_type().score_async(
                scorable=ContentScorable(value="unused"), expectation=cast("Any", "not typed")
            )

    async def test_wrapped_root_preserves_full_expectation_and_root_persistence_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        leaf = _MessageRecordingScorer()
        root = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[leaf])
        expectation = _expectation()
        scores = await _score_response_async(
            response=_stored_response(sqlite_instance),
            scorers=[root, _GenericRecordingScorer()],
            multiple=False,
            expectation=expectation,
        )
        assert leaf.expectations and all(item is expectation for item in leaf.expectations)
        stored = sqlite_instance.get_scores(score_type="true_false")
        assert len(scores) == len(stored) == 2
        assert all(score.scored_expectation == expectation for score in stored)

    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize("skip_on_error", [None, True])
    @pytest.mark.filterwarnings("error::DeprecationWarning")
    async def test_response_error_policy_preserves_full_expectation_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool, skip_on_error: bool | None
    ) -> None:
        response = MessagePiece(
            role="assistant",
            original_value="transport failure",
            original_value_data_type="error",
            response_error="processing",
            conversation_id=str(uuid.uuid4()),
        ).to_message()
        sqlite_instance.add_message_to_memory(request=response)
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        expectation = _expectation()

        with pytest.warns(DeprecationWarning, match="skip_on_error_result") if skip_on_error else nullcontext():
            scores = await _score_response_async(
                response=response,
                scorers=[objective, auxiliary],
                multiple=multiple,
                expectation=expectation,
                skip_on_error_result=skip_on_error,
            )
        assert all(score.scored_expectation is expectation for score in scores)
        assert auxiliary.expectations == [expectation]
        if skip_on_error:
            assert len(scores) == 1
            assert not objective.expectations
        else:
            assert [score.status for score in scores] == [ScoreStatus.UNDETERMINED, ScoreStatus.COMPLETE]


@pytest.mark.usefixtures("patch_central_database")
class TestGenericScoringGroup:
    @pytest.mark.parametrize("shared_scorer", [False, True])
    @pytest.mark.parametrize("explicit_roles", [False, True])
    async def test_roles_preserve_parent_context_and_shared_scorer_calls_async(
        self, *, shared_scorer: bool, explicit_roles: bool
    ) -> None:
        first = _GenericRecordingScorer()
        second = first if shared_scorer else _GenericRecordingScorer()
        roles = [ComponentRole.OBJECTIVE_SCORER, ComponentRole.AUXILIARY_SCORER]
        with execution_context(
            component_role=ComponentRole.UNKNOWN,
            attack_strategy_name="test attack",
            attack_identifier=first.get_identifier(),
            objective_target_conversation_id="conversation",
            objective="attack objective",
        ):
            parent = get_execution_context()
            results = await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="response"),
                scorers=[first, second],
                scorer_roles=roles if explicit_roles else None,
                expectation=ScoringExpectation(objective="scoring objective"),
            )
            assert get_execution_context() is parent
        assert [len(scores) for scores in results] == [1, 1]
        contexts = first.contexts if shared_scorer else first.contexts + second.contexts
        assert len(contexts) == 2
        for context, role, scorer in zip(contexts, roles, [first, second], strict=True):
            if not explicit_roles:
                assert context is parent
                continue
            assert context is not None
            assert context.component_role is role
            assert context.component_identifier == scorer.get_identifier()
            assert context.attack_strategy_name == "test attack"
            assert context.attack_identifier == first.get_identifier()
            assert context.objective_target_conversation_id == "conversation"
            assert context.objective == "attack objective"

    @pytest.mark.parametrize(("scorer_count", "role_count"), [(1, 0), (1, 2), (0, 1)])
    async def test_role_count_mismatch_fails_before_scoring_async(self, *, scorer_count: int, role_count: int) -> None:
        scorer = _GenericRecordingScorer()
        with pytest.raises(ValueError, match="one entry per scorer"):
            await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="response"),
                scorers=[scorer] * scorer_count,
                scorer_roles=[ComponentRole.AUXILIARY_SCORER] * role_count,
            )
        assert scorer.expectations == []

    @pytest.mark.parametrize("expectation", [None, _expectation()])
    async def test_content_group_preserves_input_order_expectation_and_persistence_async(
        self, *, sqlite_instance: MemoryInterface, expectation: ScoringExpectation | None
    ) -> None:
        scorable = ContentScorable(value="combined response content")
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        roots = (objective, auxiliary)
        with (
            patch.object(objective, "score_async", wraps=objective.score_async) as objective_spy,
            patch.object(auxiliary, "score_async", wraps=auxiliary.score_async) as auxiliary_spy,
        ):
            score_lists = await Scorer.score_with_scorers_async(
                scorable=scorable, scorers=roots, expectation=expectation
            )

        assert [[score.get_value() for score in scores] for scores in score_lists] == [[True], [False]]
        for spy in (objective_spy, auxiliary_spy):
            spy.assert_awaited_once()
            assert spy.call_args.kwargs["scorable"] is scorable
            assert spy.call_args.kwargs["expectation"] is expectation
        stored = sqlite_instance.get_scores(score_type="true_false")
        assert len(stored) == 2
        for score in stored:
            assert score.scored_expectation == expectation
            assert isinstance(score.scorable, ContentEntryScorable)
            content = sqlite_instance.get_scorable_content(content_ids=[score.scorable.content_id])
            assert content[score.scorable.content_id].value == scorable.value

    async def test_empty_scorer_group_rejects_conditions_only_async(self) -> None:
        scorable = ContentScorable(value="combined response")
        assert await Scorer.score_with_scorers_async(scorable=scorable, scorers=[]) == []
        assert (
            await Scorer.score_with_scorers_async(
                scorable=scorable, scorers=(), expectation=ScoringExpectation(objective="context")
            )
            == []
        )
        with pytest.raises(ValueError, match="does not match the condition"):
            await Scorer.score_with_scorers_async(scorable=scorable, scorers=[], expectation=_expectation())

    async def test_empty_and_multiple_score_lists_keep_their_root_positions_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        multiple, empty = _MultipleContentScorer(), _GenericRecordingScorer()
        expectation = ScoringExpectation(conditions=(_SecondCondition(),))

        with patch.object(empty, "_score_scorable_async", return_value=[]):
            results = await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="combined response"),
                scorers=[multiple, empty],
                expectation=expectation,
            )
        assert [len(scores) for scores in results] == [2, 0]
        assert len(sqlite_instance.get_scores(score_type="float_scale")) == 2

    async def test_group_propagates_root_exception_context_async(self) -> None:
        scorer = _GenericRecordingScorer()
        with (
            patch.object(scorer, "_score_scorable_async", side_effect=ValueError("test scoring failure")),
            pytest.raises(RuntimeError, match="Error in scorer _GenericRecordingScorer: test scoring failure"),
        ):
            await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="combined response"),
                scorers=[scorer],
                scorer_roles=[ComponentRole.AUXILIARY_SCORER],
            )

    async def test_group_preflights_conditions_before_running_any_root_async(self) -> None:
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        with pytest.raises(ValueError, match="requires the condition"):
            await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="combined response"),
                scorers=[objective, auxiliary],
                expectation=ScoringExpectation(conditions=(_FirstCondition(),)),
            )
        assert objective.expectations == auxiliary.expectations == []

    async def test_content_group_rejects_untyped_expectation_async(self) -> None:
        with pytest.raises(TypeError, match=r"^expectation must be a ScoringExpectation or None\.$"):
            await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="unused"), scorers=[], expectation=cast("Any", "not typed")
            )


class _FirstJudgmentScorer(SelfAskTrueFalseScorer):
    MATCHED_CONDITIONS = frozenset({_FirstCondition})


class _SecondJudgmentScorer(SelfAskTrueFalseScorer):
    MATCHED_CONDITIONS = frozenset({_SecondCondition})


def _judgment_scorers() -> list[Scorer]:
    scorers: list[Scorer] = []
    for scorer_type in (_FirstJudgmentScorer, _SecondJudgmentScorer):
        target = get_mock_target(scorer_type.__name__)
        target.send_prompt_async = AsyncMock(
            side_effect=lambda **kwargs: [
                MessagePiece(
                    role="assistant",
                    original_value='{"score_value":"true","description":"matched","rationale":"reason"}',
                ).to_message()
            ]
        )
        scorers.append(scorer_type(chat_target=target))
    return scorers


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("content_group", [False, True], ids=["response", "content"])
async def test_group_roots_commit_observations_independently_async(
    *, sqlite_instance: MemoryInterface, content_group: bool
) -> None:
    scorers = _judgment_scorers()
    expectation = _expectation()

    with patch.object(sqlite_instance, "add_scores_to_memory", wraps=sqlite_instance.add_scores_to_memory) as persist:
        if content_group:
            score_lists = await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="combined response"), scorers=scorers, expectation=expectation
            )
            scores = [score for root_scores in score_lists for score in root_scores]
        else:
            scores = await _score_response_async(
                response=_stored_response(sqlite_instance), scorers=scorers, multiple=False, expectation=expectation
            )

    assert len(scores) == persist.call_count == 2
    assert len({observation_id for score in scores for observation_id in score.observation_ids}) == 2
    for call in persist.call_args_list:
        assert len(call.kwargs["scores"]) == len(call.kwargs["observations"]) == 1
        score = call.kwargs["scores"][0]
        observation = call.kwargs["observations"][0]
        assert score.observation_ids == [observation.id]
        assert isinstance(score.scorable, ContentEntryScorable if content_group else MessageScorable)
    for score in scores:
        assert score.scored_expectation is expectation
        stored = sqlite_instance.get_scores(score_ids=[score.id])[0]
        assert stored.scored_expectation == expectation
        assert stored.observation_ids == score.observation_ids
        [observation] = sqlite_instance.get_observations(observation_ids=stored.observation_ids)
        assert observation.scorable == stored.scorable


@pytest.mark.usefixtures("patch_central_database")
async def test_group_failure_keeps_other_root_commit_and_no_orphan_observations_async(
    sqlite_instance: MemoryInterface,
) -> None:
    objective, auxiliary = _judgment_scorers()
    response = _stored_response(sqlite_instance)
    expectation = _expectation()
    committed = asyncio.Event()
    failed_observation_ids: list[uuid.UUID] = []
    original_persist_async = auxiliary._validate_and_persist_scores_async

    async def persist_auxiliary_async(**kwargs: Any) -> list[Score]:
        result = await original_persist_async(**kwargs)
        committed.set()
        return result

    async def fail_objective_async(**kwargs: Any) -> list[Score]:
        await committed.wait()
        failed_observation_ids.extend(observation.id for observation in kwargs["observations"])
        raise ValueError("test persistence failure")

    with (
        patch.object(auxiliary, "_validate_and_persist_scores_async", side_effect=persist_auxiliary_async),
        patch.object(objective, "_validate_and_persist_scores_async", side_effect=fail_objective_async),
        pytest.raises(ValueError, match="test persistence failure"),
    ):
        await asyncio.wait_for(
            _score_response_async(
                response=response, scorers=[objective, auxiliary], multiple=False, expectation=expectation
            ),
            timeout=10,
        )

    stored = sqlite_instance.get_scores(score_type="true_false")
    assert len(stored) == 1
    assert stored[0].scored_expectation == expectation
    assert len(sqlite_instance.get_observations(observation_ids=stored[0].observation_ids)) == 1
    assert failed_observation_ids
    assert sqlite_instance.get_observations(observation_ids=failed_observation_ids) == []
