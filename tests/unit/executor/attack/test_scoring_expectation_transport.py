# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Per-execution expectations reach real outcome scorers without becoming attack prompts."""

import asyncio
from contextlib import nullcontext
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget, store_message_async

from pyrit.exceptions import (
    ComponentRole,
    ExecutionContext,
    get_exception_execution_context,
    get_execution_context,
)
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackExecutor,
    AttackParameters,
    AttackScoringConfig,
    ChunkedRequestAttack,
    CrescendoAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    SingleTurnAttackContext,
)
from pyrit.executor.attack.compound import SequentialAttack, SequentialChildAttack
from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttackParameters
from pyrit.executor.attack.multi_turn.multi_prompt_sending import MultiPromptSendingAttackParameters
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TAPAttackContext,
    TAPAttackScoringConfig,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    ComponentIdentifier,
    Condition,
    ContentEntryScorable,
    ContentScorable,
    Message,
    MessageScorable,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
    SeedObjective,
    SeedPrompt,
)
from pyrit.score import FloatScaleScorer, FloatScaleThresholdScorer, TrueFalseScorer


class _OutcomeCondition(Condition):
    condition_type: Literal["test_attack_transport_outcome"] = "test_attack_transport_outcome"
    value: str


class _AuxiliaryCondition(Condition):
    condition_type: Literal["test_attack_transport_auxiliary"] = "test_attack_transport_auxiliary"
    value: str


class _RecordingScorer(TrueFalseScorer):
    def __init__(
        self,
        *,
        condition_type: type[Condition] | None = _OutcomeCondition,
        value: bool = True,
        barrier: asyncio.Barrier | None = None,
    ) -> None:
        super().__init__()
        self._condition_type = condition_type
        self.value = value
        self.barrier = barrier
        self.calls: list[tuple[Scorable, ScoringExpectation | None]] = []
        self.contexts: list[ExecutionContext | None] = []

    def _get_condition_type(self) -> type[Condition] | None:
        return self._condition_type

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"condition": self.condition_type.__name__ if self.condition_type else None, "value": self.value}
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        self.calls.append((scorable, expectation))
        self.contexts.append(get_execution_context())
        if self.barrier is not None:
            await self.barrier.wait()
        return [
            Score(
                score_value=str(self.value).lower(),
                score_type="true_false",
                score_rationale="Recorded execution criterion",
                scorer_class_identifier=self.get_identifier(),
                scorable=cast("ScorableUnion", scorable),
            )
        ]


class _RecordingFloatScorer(FloatScaleScorer):
    CONDITION_TYPE = _OutcomeCondition

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[Scorable, ScoringExpectation | None]] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        self.calls.append((scorable, expectation))
        return [
            Score(
                score_value="0.9",
                score_type="float_scale",
                score_rationale="Recorded TAP criterion",
                scorer_class_identifier=self.get_identifier(),
                scorable=cast("ScorableUnion", scorable),
            )
        ]


class _ConfiguredFloatScorer(_RecordingFloatScorer):
    CONDITION_TYPE = None


def _expectation(objective: str | None = "scoring objective") -> ScoringExpectation:
    return ScoringExpectation(objective=objective, conditions=(_OutcomeCondition(value="expected evidence"),))


def _seed_group(objective: str = "attack objective") -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[
            SeedObjective(value=objective),
            SeedPrompt(value="first seed prompt", data_type="text", role="user", sequence=0),
            SeedPrompt(value="second seed prompt", data_type="text", role="user", sequence=1),
        ]
    )


class _SeedExpectationParameters(AttackParameters):
    @classmethod
    async def from_seed_group_async(cls, *, seed_group: AttackSeedGroup, **overrides: Any) -> AttackParameters:
        overrides.setdefault("expectation", _expectation(None))
        return await super().from_seed_group_async(seed_group=seed_group, **overrides)


class TestEffectiveExpectation:
    @pytest.mark.parametrize("scoring_objective", [None, "", "different objective"])
    def test_context_resolves_only_missing_scoring_objective(self, *, scoring_objective: str | None) -> None:
        supplied = _expectation(scoring_objective)
        original = supplied.model_dump()
        params = AttackParameters(objective="attack objective", expectation=supplied)

        context = SingleTurnAttackContext(params=params)

        assert context.expectation.objective == ("attack objective" if scoring_objective is None else scoring_objective)
        assert context.expectation.conditions == supplied.conditions
        assert context.params.expectation is supplied
        assert supplied.model_dump() == original
        assert context.objective == "attack objective"
        if scoring_objective is None:
            assert context.expectation is not supplied
        else:
            assert context.expectation is supplied

    def test_context_defaults_to_attack_objective(self) -> None:
        context = SingleTurnAttackContext(params=AttackParameters(objective="attack objective"))

        assert context.expectation == ScoringExpectation(objective="attack objective")
        assert context.params.expectation is None

    @pytest.mark.parametrize(
        "params_type",
        [AttackParameters, MultiPromptSendingAttackParameters, ChunkedRequestAttackParameters],
        ids=["base", "inherited", "excluding"],
    )
    async def test_seed_preparation_retains_override_async(self, params_type: type[AttackParameters]) -> None:
        supplied = _expectation(None)

        params = await params_type.from_seed_group_async(seed_group=_seed_group(), expectation=supplied)
        context = SingleTurnAttackContext(params=params)

        assert params.expectation is supplied
        assert context.expectation == supplied.model_copy(update={"objective": "attack objective"})
        assert supplied.objective is None


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.filterwarnings("error::DeprecationWarning")
class TestExecutionExpectationTransport:
    async def test_prompt_sending_persists_effective_expectation_async(self, sqlite_instance: SQLiteMemory) -> None:
        supplied = _expectation(None)
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_RecordingScorer()),
        )

        result = await attack.execute_async(objective="attack objective", expectation=supplied)

        effective = supplied.model_copy(update={"objective": "attack objective"})
        assert result.last_response is not None
        [stored] = await sqlite_instance.get_scores_async(score_type="true_false")
        assert stored.scored_expectation == effective
        assert stored.scorable == MessageScorable(message_piece_ids=(result.last_response.id,))
        [stored_result] = await sqlite_instance.get_attack_results_async(objective="attack objective")
        assert stored_result.automated_score is not None
        assert stored_result.automated_score.id == stored.id
        assert stored_result.automated_score.scored_expectation == effective

    async def test_prompt_sending_keeps_scoring_objective_out_of_prompt_async(self) -> None:
        target = MockPromptTarget()
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_RecordingScorer()),
        )

        result = await attack.execute_async(objective="attack objective", expectation=_expectation())

        assert target.prompt_sent == ["attack objective"]
        assert result.objective == "attack objective"

    async def test_execute_with_context_uses_effective_not_raw_expectation_async(self) -> None:
        supplied = _expectation(None)
        context = SingleTurnAttackContext(params=AttackParameters(objective="context objective", expectation=supplied))
        scorer = _RecordingScorer()
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        )

        result = await attack.execute_with_context_async(context=context)

        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == context.expectation
        assert scorer.calls[0][1] is context.expectation

    async def test_auxiliary_cannot_supply_missing_objective_coverage_async(self) -> None:
        supplied = ScoringExpectation(
            objective="scoring objective",
            conditions=(_OutcomeCondition(value="main criterion"), _AuxiliaryCondition(value="secondary criterion")),
        )
        objective = _RecordingScorer()
        auxiliary = _RecordingScorer(condition_type=_AuxiliaryCondition)
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=objective, auxiliary_scorers=[auxiliary]),
        )

        with patch.object(attack._objective_target, "send_prompt_async", new_callable=AsyncMock) as send:
            with pytest.raises(ValueError, match="does not support.*_AuxiliaryCondition"):
                await attack.execute_async(objective="attack objective", expectation=supplied)
        assert objective.calls == auxiliary.calls == []
        send.assert_not_awaited()

    @pytest.mark.parametrize("from_seeds", [False, True], ids=["objectives", "seed_groups"])
    async def test_concurrent_rows_keep_effective_expectations_isolated_async(self, from_seeds: bool) -> None:
        objectives = ["broadcast row", "replaced row", "cleared row", "empty row"]
        supplied = _expectation(None)
        replaced = ScoringExpectation(objective="replacement", conditions=(_OutcomeCondition(value="different"),))
        empty = _expectation("")
        overrides = [{}, {"expectation": replaced}, {"expectation": None}, {"expectation": empty}]
        scorer = _RecordingScorer(barrier=asyncio.Barrier(3))
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(), attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
        )
        executor = AttackExecutor(max_concurrency=4)
        kwargs = {
            "attack": attack,
            "expectation": supplied,
            "field_overrides": overrides,
            "return_partial_on_failure": True,
        }
        if from_seeds:
            execution = executor.execute_attack_from_seed_groups_async(
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=value)]) for value in objectives], **kwargs
            )
        else:
            execution = executor.execute_attack_async(objectives=objectives, **kwargs)

        batch = await asyncio.wait_for(execution, timeout=10)

        expected = [
            supplied.model_copy(update={"objective": objectives[0]}),
            replaced,
            empty,
        ]
        results = batch.completed_results
        assert len(results) == 3
        [(failed_objective, error)] = batch.incomplete_objectives
        assert failed_objective == objectives[2]
        assert "requires one _OutcomeCondition" in str(error)
        for result, expectation in zip(results, expected, strict=True):
            assert result.automated_score is not None
            assert result.automated_score.scored_expectation == expectation
        assert supplied.objective is None

    async def test_multi_prompt_seed_group_override_reaches_final_score_async(self) -> None:
        target = MockPromptTarget()
        scorer = _RecordingScorer()
        supplied = _expectation(None)
        attack = MultiPromptSendingAttack(
            objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
        )

        batch = await AttackExecutor().execute_attack_from_seed_groups_async(
            attack=attack, seed_groups=[_seed_group()], field_overrides=[{"expectation": supplied}]
        )

        [result] = batch.get_results()
        assert target.prompt_sent == ["first seed prompt", "second seed prompt"]
        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == supplied.model_copy(
            update={"objective": "attack objective"}
        )
        assert len(scorer.calls) == 1

    async def test_generic_scorer_receives_errored_response_and_expectation_async(self) -> None:
        target = MockPromptTarget()
        scorer = _RecordingScorer()
        supplied = _expectation()
        attack = PromptSendingAttack(
            objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
        )
        response = Message.from_prompt(prompt="transport failed", role="assistant")
        response.get_piece().response_error = "processing"
        response.get_piece().converted_value_data_type = "error"
        with patch.object(target, "send_prompt_async", new_callable=AsyncMock, return_value=[response]):
            result = await attack.execute_async(objective="attack objective", expectation=supplied)

        assert scorer.calls == [(MessageScorable.from_message(response), supplied)]
        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == supplied

    @pytest.mark.parametrize("with_scorer", [False, True], ids=["no_scorer", "unmatched_scorer"])
    async def test_unconsumed_conditions_fail_before_target_send_async(self, with_scorer: bool) -> None:
        target = MockPromptTarget()
        config = AttackScoringConfig(
            objective_scorer=_RecordingScorer(condition_type=_AuxiliaryCondition) if with_scorer else None
        )
        attack = PromptSendingAttack(objective_target=target, attack_scoring_config=config)

        with pytest.raises(ValueError, match="condition"):
            await attack.execute_async(objective="attack objective", expectation=_expectation())

        assert target.prompt_sent == []

    async def test_invalid_execution_expectation_fails_before_target_send_async(self) -> None:
        target = MockPromptTarget()
        attack = PromptSendingAttack(objective_target=target)

        with pytest.raises(TypeError, match=r"^expectation must be a ScoringExpectation or None\.$"):
            await attack.execute_async(objective="attack objective", expectation=cast("Any", "not typed"))

        assert target.prompt_sent == []

    async def test_missing_scoring_config_rejects_conditions_before_setup_async(self) -> None:
        target = MockPromptTarget()
        attack = PromptSendingAttack(objective_target=target)
        with (
            patch.object(attack, "get_attack_scoring_config", return_value=None),
            patch.object(attack, "_setup_async", new_callable=AsyncMock) as setup,
            pytest.raises(ValueError, match="objective scorer is required"),
        ):
            await attack.execute_async(objective="attack objective", expectation=_expectation())
        setup.assert_not_awaited()
        assert target.prompt_sent == []

    @pytest.mark.parametrize("supplied", [None, _expectation(), _expectation(None)])
    async def test_nested_compound_uses_child_seed_defaults_async(self, supplied: ScoringExpectation | None) -> None:
        target = MockPromptTarget()
        scorer = _RecordingScorer()
        leaf = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            params_type=_SeedExpectationParameters,
        )
        inner = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=leaf, seed_group=_seed_group("child objective"))],
        )
        outer = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=inner, seed_group=_seed_group("inner objective"))],
        )

        await outer.execute_async(objective="parent objective", expectation=supplied)

        effective = (
            supplied if supplied is not None and supplied.objective is not None else _expectation("child objective")
        )
        assert scorer.calls[0][1] == effective

    async def test_compound_child_rejects_unsupported_criteria_before_send_async(self) -> None:
        target = MockPromptTarget()
        leaf = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=None),
        )
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=leaf, seed_group=_seed_group())],
        )
        with pytest.raises(RuntimeError, match="condition") as error:
            await compound.execute_async(objective="parent objective", expectation=_expectation())
        assert isinstance(error.value.__cause__, ValueError)
        assert target.prompt_sent == []

    async def test_red_teaming_executes_auxiliary_with_explicit_expectation_async(self) -> None:
        target = MockPromptTarget()
        scorer = _RecordingScorer()
        auxiliary = _RecordingScorer()
        supplied = _expectation()
        attack = RedTeamingAttack(
            objective_target=target,
            attack_adversarial_config=AttackAdversarialConfig(target=MockPromptTarget()),
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, auxiliary_scorers=[auxiliary]),
            max_turns=1,
        )

        result = await attack.execute_async(
            objective="attack objective",
            next_message=Message.from_prompt(prompt="seed prompt", role="user"),
            expectation=supplied,
        )

        assert target.prompt_sent == ["seed prompt"]
        assert result.objective == "attack objective"
        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == supplied
        assert scorer.calls[0][1] is supplied
        assert auxiliary.calls[0][1] is supplied
        for root, role in ((scorer, ComponentRole.OBJECTIVE_SCORER), (auxiliary, ComponentRole.AUXILIARY_SCORER)):
            [context] = root.contexts
            assert context is not None
            assert context.component_role == role
            assert context.component_identifier == root.get_identifier()
            assert context.objective == "attack objective"

    async def test_crescendo_keeps_refusal_criterion_separate_async(self) -> None:
        target = MockPromptTarget()
        objective = _RecordingScorer()
        auxiliary = _RecordingScorer()
        refusal = _RecordingScorer(value=False, condition_type=None)
        supplied = _expectation()
        attack = CrescendoAttack(
            objective_target=target,
            attack_adversarial_config=AttackAdversarialConfig(target=MockPromptTarget()),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=objective, auxiliary_scorers=[auxiliary], refusal_scorer=refusal
            ),
            max_turns=1,
        )

        await attack.execute_async(
            objective="attack objective",
            next_message=Message.from_prompt(prompt="seed prompt", role="user"),
            expectation=supplied,
        )

        assert target.prompt_sent == ["seed prompt"]
        assert objective.calls[0][1] is supplied
        assert auxiliary.calls[0][1] is supplied
        assert refusal.calls[0][1] == ScoringExpectation(objective="seed prompt")
        assert refusal.calls[0][0] == objective.calls[0][0]

    async def test_chunked_scores_combined_content_with_auxiliary_async(self, sqlite_instance: SQLiteMemory) -> None:
        target = MockPromptTarget()
        objective = _RecordingScorer()
        auxiliary = _RecordingScorer()
        supplied = _expectation()
        attack = ChunkedRequestAttack(
            objective_target=target,
            chunk_size=2,
            total_length=4,
            attack_scoring_config=AttackScoringConfig(objective_scorer=objective, auxiliary_scorers=[auxiliary]),
        )

        await attack.execute_async(objective="attack objective", expectation=supplied)

        assert all("attack objective" in prompt and "scoring objective" not in prompt for prompt in target.prompt_sent)
        assert objective.calls == [(ContentScorable(value="default\ndefault"), supplied)]
        assert auxiliary.calls == objective.calls
        scores = await sqlite_instance.get_scores_async(score_type="true_false")
        assert len(scores) == 2
        assert all(isinstance(score.scorable, ContentEntryScorable) for score in scores)
        assert all(score.scored_expectation == supplied and score.message_piece_id is None for score in scores)
        for root, role in ((objective, ComponentRole.OBJECTIVE_SCORER), (auxiliary, ComponentRole.AUXILIARY_SCORER)):
            [context] = root.contexts
            assert context is not None
            assert context.component_role == role
            assert context.component_identifier == root.get_identifier()

    @pytest.mark.parametrize("objective_kind", ["absent", "empty", "negative"])
    async def test_chunked_auxiliary_cannot_supply_objective_result_async(self, objective_kind: str) -> None:
        objective = None if objective_kind == "absent" else _RecordingScorer(value=False)
        auxiliary = _RecordingScorer(value=True)
        attack = ChunkedRequestAttack(
            objective_target=MockPromptTarget(),
            chunk_size=2,
            total_length=4,
            attack_scoring_config=AttackScoringConfig(objective_scorer=objective, auxiliary_scorers=[auxiliary]),
        )
        if objective_kind == "absent":
            with pytest.raises(ValueError, match="objective scorer is required"):
                await attack.execute_async(objective="attack objective", expectation=_expectation())
            assert not auxiliary.calls
            return
        with (
            patch.object(objective, "_score_scorable_async", new_callable=AsyncMock, return_value=[])
            if objective_kind == "empty"
            else nullcontext()
        ):
            result = await attack.execute_async(objective="attack objective", expectation=_expectation())
        assert result.outcome == AttackOutcome.FAILURE
        if objective_kind != "negative":
            assert result.automated_score is None
        assert len(auxiliary.calls) == 1
        [context] = auxiliary.contexts
        assert context is not None
        assert context.component_role == ComponentRole.AUXILIARY_SCORER
        assert context.component_identifier == auxiliary.get_identifier()

    async def test_chunked_shared_scorer_keeps_distinct_roles_async(self) -> None:
        scorer = _RecordingScorer()
        attack = ChunkedRequestAttack(
            objective_target=MockPromptTarget(),
            chunk_size=2,
            total_length=4,
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, auxiliary_scorers=[scorer]),
        )
        await attack.execute_async(objective="attack objective", expectation=_expectation())
        assert len(scorer.calls) == 2
        assert {context.component_role for context in scorer.contexts if context is not None} == {
            ComponentRole.OBJECTIVE_SCORER,
            ComponentRole.AUXILIARY_SCORER,
        }

    @pytest.mark.parametrize("attack_type", [PromptSendingAttack, ChunkedRequestAttack])
    @pytest.mark.parametrize(
        ("with_auxiliary", "failing_role"),
        [
            (False, ComponentRole.OBJECTIVE_SCORER),
            (True, ComponentRole.OBJECTIVE_SCORER),
            (True, ComponentRole.AUXILIARY_SCORER),
        ],
    )
    async def test_attack_error_names_failing_scorer_async(
        self,
        *,
        attack_type: type[PromptSendingAttack] | type[ChunkedRequestAttack],
        with_auxiliary: bool,
        failing_role: ComponentRole,
    ) -> None:
        objective, auxiliary = _RecordingScorer(), _RecordingScorer(value=False)
        config = AttackScoringConfig(
            objective_scorer=objective, auxiliary_scorers=[auxiliary] if with_auxiliary else []
        )
        target = MockPromptTarget()
        attack = (
            ChunkedRequestAttack(objective_target=target, attack_scoring_config=config, chunk_size=2, total_length=4)
            if attack_type is ChunkedRequestAttack
            else PromptSendingAttack(objective_target=target, attack_scoring_config=config)
        )
        failing = objective if failing_role is ComponentRole.OBJECTIVE_SCORER else auxiliary
        original = ValueError("scoring failed")
        with (
            patch.object(failing, "_score_scorable_async", side_effect=original),
            pytest.raises(RuntimeError, match=f"Strategy execution failed for {failing_role.value}") as raised,
        ):
            await attack.execute_async(objective="attack objective", expectation=_expectation())

        context = get_exception_execution_context(raised.value)
        assert context is not None
        assert context.component_role is failing_role
        assert context.component_identifier == failing.get_identifier()
        assert context.objective == "attack objective"
        assert f"{failing_role.value} identifier:" in str(raised.value)
        assert raised.value.__cause__.__cause__ is original
        assert get_execution_context() is None

    @pytest.mark.parametrize("duplicate", [False, True], ids=["initial_node", "duplicated_node"])
    @pytest.mark.parametrize("explicit_expectation", [False, True], ids=["default_objective", "scoring_objective"])
    async def test_tap_node_preserves_execution_expectation_async(
        self, *, duplicate: bool, explicit_expectation: bool
    ) -> None:
        target = MockPromptTarget()
        leaf = _RecordingFloatScorer() if explicit_expectation else _ConfiguredFloatScorer()
        auxiliary = _RecordingScorer(condition_type=_OutcomeCondition if explicit_expectation else None)
        supplied = _expectation() if explicit_expectation else None
        attack = TreeOfAttacksWithPruningAttack(
            objective_target=target,
            attack_adversarial_config=AttackAdversarialConfig(target=MockPromptTarget()),
            attack_scoring_config=TAPAttackScoringConfig(
                objective_scorer=FloatScaleThresholdScorer(scorer=leaf, threshold=0.7),
                auxiliary_scorers=[auxiliary],
            ),
            on_topic_checking_enabled=False,
        )
        context = TAPAttackContext(params=AttackParameters(objective="attack objective", expectation=supplied))
        expected = context.expectation
        with patch.object(attack, "_create_on_topic_scorer", wraps=attack._create_on_topic_scorer) as on_topic:
            node = attack._create_attack_node(
                context=context, initial_prompt=Message.from_prompt(prompt="seed prompt", role="user")
            )
        assert on_topic.call_args.args == ("attack objective",)
        await node.send_prompt_async(objective=context.objective)
        if duplicate:
            child = await node.duplicate_async()
            response = Message.from_prompt(prompt="branch response", role="assistant")
            response.get_piece().conversation_id = child.objective_target_conversation_id
            await child._score_response_async(
                response=(await store_message_async(response)), objective=context.objective
            )
            node = child

        assert node.objective_score is not None
        assert node.objective_score.scored_expectation == expected
        assert len(leaf.calls) == (2 if duplicate else 1)
        assert all(expectation is expected for _, expectation in leaf.calls)
        assert all(expectation is expected for _, expectation in auxiliary.calls)
        assert target.prompt_sent == ["seed prompt"]
