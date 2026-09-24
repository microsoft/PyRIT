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
    ScorerTargetResponsePayload,
    ScoreStatus,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)
from pyrit.score import (
    FloatScaleScorer,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorer,
    ScorerPromptValidator,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.exceptions import ExecutionContext
    from pyrit.memory import MemoryInterface
    from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc


class _FirstCondition(Condition):
    condition_type: Literal["test_response_first"] = "test_response_first"
    value: str = "first"


class _SecondCondition(Condition):
    condition_type: Literal["test_response_second"] = "test_response_second"
    value: str = "second"


class _MessageRecordingScorer(MessageTrueFalseScorer):
    CONDITION_TYPE = _FirstCondition

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

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_type="true_false",
                message_piece_id=message_piece.id,
                scored_expectation=expectation,
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _GenericRecordingScorer(TrueFalseScorer):
    CONDITION_TYPE = _SecondCondition

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
    CONDITION_TYPE = _SecondCondition

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


class _ConfiguredMessageScorer(_MessageRecordingScorer):
    CONDITION_TYPE = None


class _ConfiguredGenericScorer(_GenericRecordingScorer):
    CONDITION_TYPE = None


@pytest.mark.usefixtures("patch_central_database")
class TestConditionCapabilities:
    @pytest.mark.parametrize(
        "declaration",
        [
            {_FirstCondition, _SecondCondition},
            (_FirstCondition, _SecondCondition),
            _FirstCondition | _SecondCondition,
            _FirstCondition(),
            Condition,
            str,
        ],
    )
    def test_rejects_non_singular_declaration(self, declaration: object) -> None:
        with pytest.raises(TypeError, match="one specific Condition subclass or None"):
            type("InvalidScorer", (_MessageRecordingScorer,), {"CONDITION_TYPE": declaration})

    @pytest.mark.parametrize(
        "name", ["MATCHED_CONDITIONS", "REQUIRED_CONDITIONS", "matched_conditions", "required_conditions"]
    )
    def test_rejects_removed_set_declarations(self, name: str) -> None:
        with pytest.raises(TypeError, match="declare one CONDITION_TYPE"):
            type("InvalidScorer", (_MessageRecordingScorer,), {name: frozenset({_FirstCondition})})

    @pytest.mark.parametrize("name", ["condition_type", "get_condition_types"])
    def test_rejects_derived_capability_override(self, name: str) -> None:
        with pytest.raises(TypeError, match="cannot override derived condition capabilities"):
            type("InvalidScorer", (_MessageRecordingScorer,), {name: lambda self: {_FirstCondition, _SecondCondition}})

    def test_rejects_invalid_instance_condition_type(self) -> None:
        scorer = _MessageRecordingScorer()
        with (
            patch.object(scorer, "_get_condition_type", return_value={_FirstCondition, _SecondCondition}),
            pytest.raises(TypeError, match="one specific Condition subclass or None"),
        ):
            scorer.get_condition_types()

    def test_rejects_wrapper_owned_condition(self) -> None:
        with pytest.raises(TypeError, match="wraps scorers and cannot declare its own"):
            type("InvalidWrapper", (TrueFalseInverterScorer,), {"CONDITION_TYPE": _FirstCondition})

    def test_rejects_instance_specific_wrapper_condition(self) -> None:
        wrapper = TrueFalseInverterScorer(scorer=_MessageRecordingScorer())
        with (
            patch.object(wrapper, "_get_condition_type", return_value=_FirstCondition),
            pytest.raises(TypeError, match="wraps scorers and cannot declare its own"),
        ):
            wrapper.get_condition_types()

    def test_inherits_one_leaf_condition(self) -> None:
        class InheritedScorer(_MessageRecordingScorer):
            pass

        scorer = InheritedScorer()
        assert scorer.condition_type is _FirstCondition
        assert scorer.get_condition_types() == frozenset({_FirstCondition})

    def test_configured_leaf_needs_no_condition(self) -> None:
        scorer = _ConfiguredMessageScorer()
        assert scorer.condition_type is None
        assert scorer.get_condition_types() == frozenset()
        Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=None)

    def test_nested_wrapper_derives_coverage(self) -> None:
        wrapper = TrueFalseInverterScorer(
            scorer=TrueFalseCompositeScorer(
                scorers=[_MessageRecordingScorer(), _GenericRecordingScorer(), _ConfiguredMessageScorer()],
                aggregator=TrueFalseScoreAggregator.AND,
            )
        )
        assert wrapper.condition_type is None
        assert wrapper.get_condition_types() == frozenset({_FirstCondition, _SecondCondition})
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=_expectation())

    def test_explicit_type_takes_precedence_over_objective_validator(self) -> None:
        scorer = _MessageRecordingScorer()
        scorer._validator = ScorerPromptValidator(is_objective_required=True)
        assert scorer.condition_type is _FirstCondition
        assert scorer.get_condition_types() == frozenset({_FirstCondition})
        Scorer.validate_expectation_for_scorers(
            scorers=[scorer],
            expectation=ScoringExpectation(objective="question context", conditions=(_FirstCondition(),)),
        )

    @pytest.mark.parametrize(
        "expectation",
        [
            None,
            ScoringExpectation(),
            ScoringExpectation(objective="context"),
            ScoringExpectation(conditions=(_SecondCondition(),)),
        ],
    )
    def test_typed_leaf_requires_its_condition(self, expectation: ScoringExpectation | None) -> None:
        with pytest.raises(ValueError, match="requires one _FirstCondition"):
            _MessageRecordingScorer()._validate_expectation(expectation=expectation)

    def test_typed_leaf_rejects_duplicate_conditions(self) -> None:
        with pytest.raises(ValueError, match="received 2 _FirstCondition"):
            _MessageRecordingScorer()._validate_expectation(
                expectation=ScoringExpectation(conditions=(_FirstCondition(), _FirstCondition()))
            )

    def test_leaf_can_only_retrieve_its_declared_criterion(self) -> None:
        scorer = _MessageRecordingScorer()
        expectation = _expectation()
        assert (
            scorer._get_required_condition(expectation=expectation, condition_type=_FirstCondition)
            is expectation.conditions[0]
        )
        with pytest.raises(TypeError, match="only retrieve its declared condition type"):
            scorer._get_required_condition(expectation=expectation, condition_type=_SecondCondition)


def _expectation(objective: str | None = "scoring context") -> ScoringExpectation:
    return ScoringExpectation(objective=objective, conditions=(_FirstCondition(), _SecondCondition()))


async def _stored_response_async(memory: MemoryInterface) -> Message:
    response = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4())).to_message()
    (await memory.add_message_to_memory_async(request=response))
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
    @pytest.mark.parametrize(
        ("conditions", "error"),
        [
            ((), "requires one"),
            ((_SecondCondition(),), "requires one"),
            ((_FirstCondition(), _FirstCondition(), _SecondCondition()), "exactly one condition"),
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
        with pytest.raises(ValueError, match="scorer.*supplied conditions"):
            await _score_response_async(response=response, scorers=[], multiple=multiple, expectation=_expectation())

    @pytest.mark.parametrize("multiple", [False, True])
    @pytest.mark.parametrize("objective", [None, "", "context"])
    @pytest.mark.filterwarnings("error::DeprecationWarning")
    async def test_no_conditions_accepts_constructor_configured_scorers_async(
        self, *, sqlite_instance: MemoryInterface, multiple: bool, objective: str | None
    ) -> None:
        expectation = ScoringExpectation(objective=objective)
        scores = await _score_response_async(
            response=(await _stored_response_async(sqlite_instance)),
            scorers=[_ConfiguredMessageScorer(), _ConfiguredGenericScorer()],
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
            response=(await _stored_response_async(sqlite_instance)),
            scorers=[_ConfiguredMessageScorer()],
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
                response=(await _stored_response_async(sqlite_instance)),
                scorers=[_ConfiguredMessageScorer()],
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
    async def test_direct_root_rejects_other_condition_types_async(
        self, *, sqlite_instance: MemoryInterface, scorer_type: type[Scorer]
    ) -> None:
        scorer = scorer_type()
        expectation = _expectation()
        with pytest.raises(ValueError, match="does not support"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(await _stored_response_async(sqlite_instance)),
                expectation=expectation,
            )
        assert not scorer.expectations

    @pytest.mark.parametrize("duplicate", [False, True])
    async def test_direct_root_still_checks_its_own_criteria_async(self, duplicate: bool) -> None:
        scorer = _GenericRecordingScorer()
        conditions = (_SecondCondition(), _SecondCondition()) if duplicate else (_FirstCondition(),)
        with pytest.raises(ValueError, match="exactly one condition" if duplicate else "requires one"):
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
        root = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND, scorers=[leaf, _GenericRecordingScorer()]
        )
        expectation = _expectation()
        scores = await _score_response_async(
            response=(await _stored_response_async(sqlite_instance)),
            scorers=[root],
            multiple=False,
            expectation=expectation,
        )
        assert leaf.expectations == [expectation.model_copy(update={"conditions": (_FirstCondition(),)})]
        stored = await sqlite_instance.get_scores_async(score_type="true_false")
        assert len(scores) == len(stored) == 1
        assert scores[0].scored_expectation == stored[0].scored_expectation == expectation

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
        (await sqlite_instance.add_message_to_memory_async(request=response))
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        auxiliary.CONDITION_TYPE = _FirstCondition
        expectation = ScoringExpectation(conditions=(_FirstCondition(),))

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
    async def test_complementary_roots_need_explicit_composition_async(self) -> None:
        first, second = _MessageRecordingScorer(), _GenericRecordingScorer()
        with pytest.raises(ValueError, match="does not support"):
            await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="unused"), scorers=[first, second], expectation=_expectation()
            )
        assert first.expectations == second.expectations == []

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
                expectation=ScoringExpectation(objective="scoring objective", conditions=(_SecondCondition(),)),
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

    @pytest.mark.parametrize("expectation", [None, ScoringExpectation(conditions=(_FirstCondition(),))])
    async def test_content_group_preserves_input_order_expectation_and_persistence_async(
        self, *, sqlite_instance: MemoryInterface, expectation: ScoringExpectation | None
    ) -> None:
        scorable = ContentScorable(value="combined response content")
        objective = _MessageRecordingScorer() if expectation else _ConfiguredMessageScorer()
        auxiliary = _GenericRecordingScorer() if expectation else _ConfiguredGenericScorer()
        if expectation:
            auxiliary.CONDITION_TYPE = _FirstCondition
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
        stored = await sqlite_instance.get_scores_async(score_type="true_false")
        assert len(stored) == 2
        for score in stored:
            assert score.scored_expectation == expectation
            assert isinstance(score.scorable, ContentEntryScorable)
            content = await sqlite_instance.get_scorable_content_async(content_ids=[score.scorable.content_id])
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
        with pytest.raises(ValueError, match="No scorer"):
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
        assert len(await sqlite_instance.get_scores_async(score_type="float_scale")) == 2

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
                expectation=ScoringExpectation(conditions=(_SecondCondition(),)),
            )

    async def test_group_preflights_conditions_before_running_any_root_async(self) -> None:
        objective, auxiliary = _MessageRecordingScorer(), _GenericRecordingScorer()
        with pytest.raises(ValueError, match="requires one"):
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


@pytest.mark.usefixtures("patch_central_database")
class TestStrictRouting:
    async def test_legacy_role_skip_cannot_bypass_condition_validation_async(self) -> None:
        scorer = _MessageRecordingScorer()
        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="does not support"):
            await scorer.score_async(
                message=Message.from_prompt(prompt="unused", role="assistant"),
                expectation=_expectation("context"),
                role_filter="user",
                infer_objective_from_request=True,
            )
        assert not scorer.expectations

    @pytest.mark.parametrize("entry", ["direct", "message", "batch", "nested", "nested-batch", "legacy"])
    async def test_entry_points_reject_extra_conditions_async(self, *, entry: str) -> None:
        scorer = _MessageRecordingScorer()
        expectation = _expectation()
        scorable = ContentScorable(value="unused")
        message = Message.from_prompt(prompt="unused", role="assistant")
        with pytest.raises(ValueError, match="does not support"):
            if entry == "direct":
                await scorer.score_async(scorable=scorable, expectation=expectation)
            elif entry == "message":
                await scorer.score_message_async(message=message, expectation=expectation)
            elif entry == "batch":
                await scorer.score_batch_async(scorables=[scorable], expectations=[expectation])
            elif entry == "nested":
                await scorer._score_nested_async(scorable=scorable, expectation=expectation)
            elif entry == "nested-batch":
                await scorer._score_batch_nested_async(scorables=[scorable], expectations=[expectation])
            else:
                with pytest.warns(DeprecationWarning):
                    await scorer.score_async(message=message, expectation=expectation)
        assert not scorer.expectations

    @pytest.mark.parametrize("configured", [False, True])
    def test_strict_leaf_rejects_unconsumed_input(self, *, configured: bool) -> None:
        scorer = _ConfiguredGenericScorer() if configured else _GenericRecordingScorer()
        with pytest.raises(ValueError, match="does not support"):
            scorer.prepare_expectation(expectation=_expectation())

    async def test_condition_can_reach_multiple_children_without_mutating_input_async(self) -> None:
        first, second = _GenericRecordingScorer(), _GenericRecordingScorer()
        root = TrueFalseCompositeScorer(
            scorers=[first, TrueFalseInverterScorer(scorer=second)], aggregator=TrueFalseScoreAggregator.OR
        )
        expectation = ScoringExpectation(objective="context", conditions=(_SecondCondition(),))
        before = expectation.model_dump()
        [score] = await root.score_async(scorable=ContentScorable(value="response"), expectation=expectation)
        assert first.expectations == second.expectations == [expectation]
        assert expectation.model_dump() == before
        assert score.scored_expectation == expectation
        assert score.get_value() is True

    @pytest.mark.parametrize("aggregator", [TrueFalseScoreAggregator.AND, TrueFalseScoreAggregator.OR])
    async def test_missing_child_condition_never_prunes_required_branch_async(
        self, *, aggregator: TrueFalseAggregatorFunc
    ) -> None:
        first, second = _MessageRecordingScorer(), _GenericRecordingScorer()
        root = TrueFalseCompositeScorer(scorers=[first, second], aggregator=aggregator)
        with pytest.raises(ValueError, match="requires one _SecondCondition"):
            await root.score_async(
                scorable=ContentScorable(value="unused"),
                expectation=ScoringExpectation(conditions=(_FirstCondition(),)),
            )
        assert not first.expectations and not second.expectations

    async def test_score_response_rejects_auxiliary_without_its_criterion_async(
        self, *, sqlite_instance: MemoryInterface
    ) -> None:
        with pytest.raises(ValueError, match="does not support|requires one|exactly one"):
            await MessageScorer.score_response_async(
                response=(await _stored_response_async(sqlite_instance)),
                objective_scorer=_MessageRecordingScorer(),
                auxiliary_scorers=[_GenericRecordingScorer()],
                expectation=ScoringExpectation(conditions=(_FirstCondition(),)),
            )

    async def test_score_response_requires_one_input_per_auxiliary_async(
        self, *, sqlite_instance: MemoryInterface
    ) -> None:
        with pytest.raises(ValueError, match="one input for each auxiliary scorer"):
            await MessageScorer.score_response_async(
                response=(await _stored_response_async(sqlite_instance)),
                auxiliary_scorers=[_ConfiguredGenericScorer()],
                auxiliary_expectations=[],
            )

    async def test_concurrent_composite_inputs_remain_isolated_async(self) -> None:
        child = _GenericRecordingScorer()
        root = TrueFalseCompositeScorer(
            scorers=[child, _MessageRecordingScorer()], aggregator=TrueFalseScoreAggregator.AND
        )
        expectations = [
            ScoringExpectation(
                objective=f"context {index}",
                conditions=(_SecondCondition(value=str(index)), _FirstCondition(value=str(index))),
            )
            for index in range(3)
        ]
        results = await asyncio.gather(
            *(root.score_async(scorable=ContentScorable(value="response"), expectation=item) for item in expectations)
        )
        assert [scores[0].scored_expectation for scores in results] == expectations
        assert child.expectations == [
            item.model_copy(update={"conditions": (item.conditions[0],)}) for item in expectations
        ]


class _ConditionJudgmentScorer(SelfAskTrueFalseScorer):
    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        assert expectation is not None
        assert self.CONDITION_TYPE is not None
        condition = self._get_required_condition(expectation=expectation, condition_type=self.CONDITION_TYPE)
        return await self._score_piece_async(message_piece, objective=str(condition.model_dump()))


class _FirstJudgmentScorer(_ConditionJudgmentScorer):
    CONDITION_TYPE = _FirstCondition


class _SecondJudgmentScorer(_ConditionJudgmentScorer):
    CONDITION_TYPE = _SecondCondition


def _judgment_scorers(*, mixed: bool = False) -> list[TrueFalseScorer]:
    scorers: list[TrueFalseScorer] = []
    for scorer_type in (_FirstJudgmentScorer, _SecondJudgmentScorer if mixed else _FirstJudgmentScorer):
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
    expectation = ScoringExpectation(conditions=(_FirstCondition(),))

    with patch.object(
        sqlite_instance, "add_scores_to_memory_async", wraps=sqlite_instance.add_scores_to_memory_async
    ) as persist:
        if content_group:
            score_lists = await Scorer.score_with_scorers_async(
                scorable=ContentScorable(value="combined response"), scorers=scorers, expectation=expectation
            )
            scores = [score for root_scores in score_lists for score in root_scores]
        else:
            scores = await _score_response_async(
                response=(await _stored_response_async(sqlite_instance)),
                scorers=scorers,
                multiple=False,
                expectation=expectation,
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
        stored = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
        assert stored.scored_expectation == expectation
        assert stored.observation_ids == score.observation_ids
        [observation] = await sqlite_instance.get_observations_async(observation_ids=stored.observation_ids)
        assert observation.scorable == stored.scorable


@pytest.mark.usefixtures("patch_central_database")
async def test_group_failure_keeps_other_root_commit_and_no_orphan_observations_async(
    sqlite_instance: MemoryInterface,
) -> None:
    objective, auxiliary = _judgment_scorers()
    response = await _stored_response_async(sqlite_instance)
    expectation = ScoringExpectation(conditions=(_FirstCondition(),))
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

    stored = await sqlite_instance.get_scores_async(score_type="true_false")
    assert len(stored) == 1
    assert stored[0].scored_expectation == expectation
    assert len(await sqlite_instance.get_observations_async(observation_ids=stored[0].observation_ids)) == 1
    assert failed_observation_ids
    assert (await sqlite_instance.get_observations_async(observation_ids=failed_observation_ids)) == []


@pytest.mark.usefixtures("patch_central_database")
async def test_composite_judgment_observations_keep_each_child_fingerprint_async(
    *, sqlite_instance: MemoryInterface
) -> None:
    first, second = _judgment_scorers(mixed=True)
    root = TrueFalseCompositeScorer(
        scorers=[first, second],
        aggregator=TrueFalseScoreAggregator.AND,
    )
    expectation = _expectation("shared context")
    [score] = await root.score_async(scorable=ContentScorable(value="response"), expectation=expectation)
    observations = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert len(observations) == 2
    assert score.scored_expectation == expectation
    fingerprints = {
        scoring_expectation_fingerprint(expectation.model_copy(update={"conditions": (condition,)}))
        for condition in expectation.conditions
    }
    actual_fingerprints = set()
    for observation in observations:
        assert isinstance(observation.payload, ScorerTargetResponsePayload)
        actual_fingerprints.add(observation.payload.expectation_fingerprint)
    assert actual_fingerprints == fingerprints
    [stored] = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert stored.scored_expectation == expectation
    assert set(stored.observation_ids) == {observation.id for observation in observations}
