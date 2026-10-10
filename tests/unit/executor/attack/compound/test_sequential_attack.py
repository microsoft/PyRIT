# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``SequentialAttack``."""

import asyncio
import uuid
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.compound import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialAttackResult,
    SequentialChildAttack,
)
from pyrit.executor.attack.core.attack_executor import AttackExecutor, AttackExecutorResult
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    AttackResultSelection,
    AttackSeedGroup,
    ScoringExpectation,
    SeedObjective,
)
from pyrit.prompt_target import PromptTarget


def _make_strategy(*, outcomes: list[AttackOutcome], name: str = "attack") -> MagicMock:
    """Build a strategy mock annotated with the outcomes it should yield in order."""
    strategy = MagicMock(name=name)
    strategy._outcomes = outcomes
    strategy._name = name
    return strategy


def _make_seed_group(objective: str = "obj") -> AttackSeedGroup:
    return AttackSeedGroup(seeds=[SeedObjective(value=objective)])


def _make_context(
    *,
    objective: str = "obj",
    labels: dict[str, str] | None = None,
    expectation: ScoringExpectation | None = None,
) -> AttackContext[AttackParameters]:
    params_type = AttackParameters.excluding("next_message", "prepended_conversation")
    return AttackContext(params=params_type(objective=objective, memory_labels=labels or {}, expectation=expectation))


def _patch_run_child_attack(*, strategies_by_id: dict[int, MagicMock]):
    """
    Patch ``SequentialAttack._run_child_attack_async`` to return results driven by
    each strategy's ``_outcomes`` list (one outcome per invocation).

    Records every call onto a ``calls`` list so tests can assert on the
    ``child_attack`` that was dispatched and the ``memory_labels`` that were applied.
    """
    counters: dict[int, int] = dict.fromkeys(strategies_by_id, 0)
    calls: list[dict] = []

    async def _stub(self, *, child_attack, memory_labels, child_result_ids, attribution=None, expectation=None):
        sid = id(child_attack.strategy)
        idx = counters[sid]
        counters[sid] = idx + 1
        outcome = child_attack.strategy._outcomes[idx]
        calls.append(
            {
                "child_attack": child_attack,
                "memory_labels": dict(memory_labels),
                "attribution": attribution,
                "expectation": expectation,
            }
        )
        return AttackResult(
            conversation_id=f"conv-{child_attack.strategy._name}-{idx}",
            objective="obj",
            outcome=outcome,
        )

    patcher = patch.object(SequentialAttack, "_run_child_attack_async", _stub)
    return patcher, calls


@pytest.fixture
def target() -> MagicMock:
    return MagicMock(name="objective_target")


@pytest.fixture
def seed_group() -> AttackSeedGroup:
    return _make_seed_group()


@pytest.mark.usefixtures("patch_central_database")
class TestInit:
    def test_init_rejects_empty_child_attacks(self, target):
        with pytest.raises(ValueError, match="at least one"):
            SequentialAttack(objective_target=target, child_attacks=[])


@pytest.mark.usefixtures("patch_central_database")
class TestValidate:
    @pytest.mark.parametrize("bad_objective", ["", "   ", "\n\t"])
    def test_validate_rejects_empty_objective(self, target, seed_group, bad_objective):
        child_attack = SequentialChildAttack(
            strategy=_make_strategy(outcomes=[AttackOutcome.SUCCESS]),
            seed_group=seed_group,
        )
        compound = SequentialAttack(objective_target=target, child_attacks=[child_attack])
        with pytest.raises(ValueError, match="objective"):
            compound._validate_context(context=_make_context(objective=bad_objective))


@pytest.mark.usefixtures("patch_central_database")
class TestFirstSuccess:
    async def test_stops_on_first_success(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 1

    async def test_runs_all_on_failures(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        c = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="c")
        child_attacks = [SequentialChildAttack(strategy=s, seed_group=seed_group) for s in (a, b, c)]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_SUCCESS,
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b, id(c): c})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.FAILURE
        assert len(calls) == 3

    async def test_undetermined_outcome_does_not_stop(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.UNDETERMINED], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2

    async def test_error_outcome_does_not_stop(self, target, seed_group):
        """FIRST_SUCCESS is resilient: a transient ERROR should not abort the sequence."""
        a = _make_strategy(outcomes=[AttackOutcome.ERROR], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestFirstDecisive:
    async def test_stops_on_error(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.ERROR], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_DECISIVE,
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.ERROR
        assert len(calls) == 1

    async def test_does_not_stop_on_failure(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_DECISIVE,
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2

    async def test_does_not_stop_on_undetermined(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.UNDETERMINED], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_DECISIVE,
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestExhaustive:
    async def test_runs_every_child_attack(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(
            objective_target=target, child_attacks=child_attacks, completion_policy=SequenceCompletionPolicy.EXHAUSTIVE
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert len(calls) == 2
        # Any-success aggregation: envelope SUCCESS because A succeeded.
        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestOutcomeDerivation:
    async def test_unscored_child_keeps_compound_undetermined_async(self) -> None:
        target = MockPromptTarget()
        child = SequentialChildAttack(
            strategy=PromptSendingAttack(objective_target=target),
            seed_group=_make_seed_group(),
        )
        compound = SequentialAttack(objective_target=target, child_attacks=[child])

        result = await compound.execute_async(objective="compound objective")

        assert result.child_attack_results[0].outcome is AttackOutcome.UNDETERMINED
        assert result.outcome is AttackOutcome.UNDETERMINED

    @pytest.mark.parametrize(
        ("policy", "outcomes", "expected", "executed"),
        [
            (
                SequenceCompletionPolicy.FIRST_SUCCESS,
                [AttackOutcome.UNDETERMINED, AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.SUCCESS,
                2,
            ),
            (
                SequenceCompletionPolicy.FIRST_SUCCESS,
                [AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED],
                AttackOutcome.UNDETERMINED,
                2,
            ),
            (
                SequenceCompletionPolicy.FIRST_DECISIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.FAILURE],
                AttackOutcome.UNDETERMINED,
                2,
            ),
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.UNDETERMINED, AttackOutcome.SUCCESS],
                AttackOutcome.UNDETERMINED,
                1,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.SUCCESS,
                3,
            ),
            (
                SequenceCompletionPolicy.LAST_RESULT,
                [AttackOutcome.UNDETERMINED, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
                2,
            ),
        ],
    )
    async def test_undetermined_reporting_preserves_stopping_rules(
        self, target, seed_group, policy, outcomes, expected, executed
    ):
        strategies = [_make_strategy(outcomes=[outcome], name=f"s{i}") for i, outcome in enumerate(outcomes)]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=strategy, seed_group=seed_group) for strategy in strategies],
            completion_policy=policy,
        )
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(strategy): strategy for strategy in strategies})
        with patcher:
            result = await compound._perform_async(context=_make_context())
        assert result.outcome is expected
        assert len(calls) == executed

    @pytest.mark.parametrize("expectation", [None, ScoringExpectation(objective="child criterion")])
    async def test_child_receives_only_explicit_expectation(self, target, seed_group, expectation):
        strategy = _make_strategy(outcomes=[AttackOutcome.SUCCESS])
        child = SequentialChildAttack(strategy=strategy, seed_group=seed_group)
        compound = SequentialAttack(objective_target=target, child_attacks=[child])
        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as execute:
            execute.return_value = AttackExecutorResult(
                completed_results=[
                    AttackResult(conversation_id="child", objective="child objective", outcome=AttackOutcome.SUCCESS)
                ],
                incomplete_objectives=[],
            )
            await compound._perform_async(context=_make_context(objective="parent objective", expectation=expectation))
        kwargs = execute.call_args.kwargs
        if expectation is None:
            assert "expectation" not in kwargs
        else:
            assert kwargs["expectation"] is expectation

    @pytest.mark.parametrize(
        ("completion_policy", "outcomes", "expected"),
        [
            # EXHAUSTIVE: any-success aggregation over every child_attack.
            (SequenceCompletionPolicy.EXHAUSTIVE, [AttackOutcome.SUCCESS], AttackOutcome.SUCCESS),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.ERROR, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.UNDETERMINED],
                AttackOutcome.UNDETERMINED,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.ERROR],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceCompletionPolicy.EXHAUSTIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.FAILURE],
                AttackOutcome.UNDETERMINED,
            ),
            # STRICT_ALL stops at the first non-SUCCESS and retains that outcome.
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED],
                AttackOutcome.UNDETERMINED,
            ),
            (
                SequenceCompletionPolicy.STRICT_ALL,
                [AttackOutcome.ERROR, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            # LAST_RESULT: pass through the last executed child_attack's outcome verbatim.
            (
                SequenceCompletionPolicy.LAST_RESULT,
                [AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceCompletionPolicy.LAST_RESULT,
                [AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (SequenceCompletionPolicy.LAST_RESULT, [AttackOutcome.UNDETERMINED], AttackOutcome.UNDETERMINED),
            (
                SequenceCompletionPolicy.LAST_RESULT,
                [AttackOutcome.ERROR, AttackOutcome.UNDETERMINED],
                AttackOutcome.UNDETERMINED,
            ),
        ],
    )
    async def test_outcome_aggregation(self, target, seed_group, completion_policy, outcomes, expected):
        strategies = [_make_strategy(outcomes=[o], name=f"s{i}") for i, o in enumerate(outcomes)]
        child_attacks = [SequentialChildAttack(strategy=s, seed_group=seed_group) for s in strategies]
        compound = SequentialAttack(
            objective_target=target, child_attacks=child_attacks, completion_policy=completion_policy
        )
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(s): s for s in strategies})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is expected

    async def test_default_policy_is_first_success(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestLabels:
    async def test_context_labels_passed_through(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            await compound._perform_async(context=_make_context(labels={"foo": "bar"}))

        assert calls[0]["memory_labels"]["foo"] == "bar"

    async def test_completed_result_carries_context_labels(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        compound = SequentialAttack(
            objective_target=target, child_attacks=[SequentialChildAttack(strategy=a, seed_group=seed_group)]
        )
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            result = await compound._perform_async(context=_make_context(labels={"op": "seq"}))

        assert result.labels == {"op": "seq"}

    async def test_child_attack_labels_override_context_labels(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [
            SequentialChildAttack(
                strategy=a,
                seed_group=seed_group,
                memory_labels={"foo": "override", "extra": "x"},
            ),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            await compound._perform_async(context=_make_context(labels={"foo": "ctx"}))

        assert calls[0]["memory_labels"]["foo"] == "override"
        assert calls[0]["memory_labels"]["extra"] == "x"


@pytest.mark.usefixtures("patch_central_database")
class TestExecutorForwarding:
    async def test_executor_receives_child_attack_inputs(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        adversarial = MagicMock(name="adversarial_chat")
        scorer = MagicMock(name="objective_scorer")
        child_attack = SequentialChildAttack(
            strategy=a,
            seed_group=seed_group,
            adversarial_chat=adversarial,
            objective_scorer=scorer,
            memory_labels={"k": "v"},
        )
        compound = SequentialAttack(objective_target=target, child_attacks=[child_attack])

        executor_call_kwargs: dict = {}

        async def _fake_execute(**kwargs):
            executor_call_kwargs.update(kwargs)
            return AttackExecutorResult(
                completed_results=[AttackResult(conversation_id="c", objective="obj", outcome=AttackOutcome.SUCCESS)],
                incomplete_objectives=[],
            )

        with patch.object(
            AttackExecutor, "execute_attack_from_seed_groups_async", AsyncMock(side_effect=_fake_execute)
        ):
            await compound._perform_async(context=_make_context(labels={"ctx": "1"}))

        assert executor_call_kwargs["attack"] is a
        assert executor_call_kwargs["seed_groups"] == [seed_group]
        assert executor_call_kwargs["adversarial_chat"] is adversarial
        assert executor_call_kwargs["objective_scorer"] is scorer
        # Context labels + child_attack labels merged for the executor call.
        assert executor_call_kwargs["memory_labels"] == {"ctx": "1", "k": "v"}
        # No attribution on the context -> executor receives None.
        assert executor_call_kwargs["attribution"] is None

    async def test_executor_receives_context_attribution(self, target, seed_group):
        """When the compound's context carries attribution (e.g. nested under
        a Scenario), it must be forwarded to the executor so the inner
        ``AttackResult`` rows can be attributed to the parent, with the
        child's 1-based position added."""
        from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution

        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)

        attribution = AttackResultAttribution(parent_id="scenario-1", parent_collection="scenario_results")
        context = _make_context()
        context._attribution = attribution

        executor_call_kwargs: dict = {}

        async def _fake_execute(**kwargs):
            executor_call_kwargs.update(kwargs)
            return AttackExecutorResult(
                completed_results=[AttackResult(conversation_id="c", objective="obj", outcome=AttackOutcome.SUCCESS)],
                incomplete_objectives=[],
            )

        with patch.object(
            AttackExecutor, "execute_attack_from_seed_groups_async", AsyncMock(side_effect=_fake_execute)
        ):
            await compound._perform_async(context=context)

        assert executor_call_kwargs["attribution"] == AttackResultAttribution(
            parent_id="scenario-1", parent_collection="scenario_results", attempt_index=1
        )


@pytest.mark.usefixtures("patch_central_database")
class TestResultRoles:
    def test_sequential_attack_is_orchestration_and_children_are_target_facing(self) -> None:
        assert SequentialAttack.RESULT_ROLE is AttackResultRole.ORCHESTRATION
        assert PromptSendingAttack.RESULT_ROLE is AttackResultRole.TARGET_FACING

    async def test_each_child_receives_its_position_and_the_parent_attribution(self, target, seed_group):
        from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution

        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        c = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="c")
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=s, seed_group=seed_group) for s in (a, b, c)],
        )
        parent = AttackResultAttribution(
            parent_id="scenario-1", parent_collection="adaptive_x", parent_eval_hash="eval", seed_group_id="seed"
        )
        context = _make_context()
        context._attribution = parent

        patcher, calls = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b, id(c): c})
        with patcher:
            await compound._perform_async(context=context)

        assert [call["attribution"].attempt_index for call in calls] == [1, 2, 3]
        for call in calls:
            assert call["attribution"].parent_id == parent.parent_id
            assert call["attribution"].parent_collection == parent.parent_collection
            assert call["attribution"].parent_eval_hash == parent.parent_eval_hash
            assert call["attribution"].seed_group_id == parent.seed_group_id
        # The parent's own attribution is unchanged, so its row carries no position.
        assert context._attribution is parent
        assert parent.attempt_index is None


@pytest.mark.usefixtures("patch_central_database")
class TestResultShape:
    async def test_returns_sequential_attack_result(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert isinstance(result, SequentialAttackResult)

    async def test_child_attack_result_ids_in_order(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        c = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="c")
        child_attacks = [SequentialChildAttack(strategy=s, seed_group=seed_group) for s in (a, b, c)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)

        captured_ids: list[str] = []

        async def _stub(self, *, child_attack, memory_labels, child_result_ids, attribution=None, expectation=None):
            inner = AttackResult(
                conversation_id=f"c-{child_attack.strategy._name}",
                objective="obj",
                outcome=child_attack.strategy._outcomes[0],
            )
            captured_ids.append(inner.attack_result_id)
            return inner

        with patch.object(SequentialAttack, "_run_child_attack_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.child_attack_result_ids == captured_ids

    async def test_fresh_result_id_not_equal_to_any_inner(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)

        inner_ids: list[str] = []

        async def _stub(self, *, child_attack, memory_labels, child_result_ids, attribution=None, expectation=None):
            inner = AttackResult(conversation_id="c", objective="obj", outcome=AttackOutcome.SUCCESS)
            inner_ids.append(inner.attack_result_id)
            return inner

        with patch.object(SequentialAttack, "_run_child_attack_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.attack_result_id != inner_ids[0]
        assert result.outcome is AttackOutcome.SUCCESS

    async def test_envelope_has_no_conversation_or_response(self, target, seed_group):
        """The envelope owns no conversation/last_response/last_score —
        those live on the inner per-child-attack rows surfaced via
        ``child_attack_results``."""
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.conversation_id == ""
        assert result.last_response is None
        assert result.last_score is None
        # The envelope objective comes from the context, not the inner.
        assert result.objective == "obj"

    async def test_child_attack_results_populated_in_dispatch_order(self, target, seed_group):
        """``child_attack_results`` holds the live inner ``AttackResult`` instances."""
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [
            SequentialChildAttack(strategy=a, seed_group=seed_group),
            SequentialChildAttack(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert len(result.child_attack_results) == 2
        assert [r.outcome for r in result.child_attack_results] == [
            AttackOutcome.FAILURE,
            AttackOutcome.SUCCESS,
        ]
        # ``child_attack_result_ids`` reads from child_attack_results when populated.
        assert result.child_attack_result_ids == [r.attack_result_id for r in result.child_attack_results]

    async def test_completion_policy_saved_on_result_and_metadata(self, target, seed_group):
        """The active ``SequenceCompletionPolicy`` is exposed both as a typed
        field and as a string in metadata for DB round-trip."""
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        child_attacks = [SequentialChildAttack(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.STRICT_ALL,
        )
        patcher, _ = _patch_run_child_attack(strategies_by_id={id(a): a})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.completion_policy is SequenceCompletionPolicy.STRICT_ALL
        assert result.metadata[SequentialAttack.COMPLETION_POLICY_KEY] == "strict_all"
        assert result.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [
            r.attack_result_id for r in result.child_attack_results
        ]

    def test_child_attack_result_ids_falls_back_to_metadata(self):
        """After a DB round-trip ``child_attack_results`` is empty; the
        ``child_attack_result_ids`` property must fall back to metadata."""
        result = SequentialAttackResult(
            conversation_id="",
            objective="obj",
            outcome=AttackOutcome.SUCCESS,
            metadata={SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY: ["a", "b", "c"]},
        )
        assert result.child_attack_results == []
        assert result.child_attack_result_ids == ["a", "b", "c"]

    async def test_executed_turns_sums_child_turns(self, target, seed_group):
        """``executed_turns`` on the envelope is the sum across child attacks."""
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        child_attacks = [SequentialChildAttack(strategy=s, seed_group=seed_group) for s in (a, b)]
        compound = SequentialAttack(objective_target=target, child_attacks=child_attacks)

        async def _stub(self, *, child_attack, memory_labels, child_result_ids, attribution=None, expectation=None):
            return AttackResult(
                conversation_id="c",
                objective="obj",
                outcome=child_attack.strategy._outcomes[0],
                executed_turns=3,
            )

        with patch.object(SequentialAttack, "_run_child_attack_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.executed_turns == 6


class _OkChildStrategy(AttackStrategy[AttackContext[AttackParameters], AttackResult]):
    """Minimal real strategy that completes successfully (no live target)."""

    def __init__(self, *, objective_target: PromptTarget) -> None:
        super().__init__(
            objective_target=objective_target,
            context_type=AttackContext,
            params_type=AttackParameters,
        )

    def _validate_context(self, *, context: AttackContext[AttackParameters]) -> None:
        pass

    async def _setup_async(self, *, context: AttackContext[AttackParameters]) -> None:
        pass

    async def _teardown_async(self, *, context: AttackContext[AttackParameters]) -> None:
        pass

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> AttackResult:
        return AttackResult(
            conversation_id=str(uuid.uuid4()),
            objective=context.objective,
            outcome=AttackOutcome.SUCCESS,
            labels=context.memory_labels,
        )


class _BoomChildStrategy(_OkChildStrategy):
    """Minimal real strategy that raises from ``_perform_async`` (no live target)."""

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> AttackResult:
        raise RuntimeError("child boom")


@pytest.mark.usefixtures("patch_central_database")
class TestChildFailurePreservesLinks:
    """The parent error result must retain child-result links (issue #3039)."""

    async def test_parent_error_result_links_completed_and_failed_children(self, seed_group, sqlite_instance):
        target = MockPromptTarget()
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_OkChildStrategy(objective_target=target), seed_group=seed_group),
                SequentialChildAttack(strategy=_BoomChildStrategy(objective_target=target), seed_group=seed_group),
            ],
            completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
        )

        # Exception propagation is preserved: the child error still raises.
        with pytest.raises(RuntimeError, match="child boom"):
            await compound.execute_async(objective="obj")

        memory = sqlite_instance
        error_results = await memory.get_attack_results_async(objective="obj", outcome="error")
        assert len(error_results) == 2
        # The parent envelope is persisted after the failed child.
        parent = max(error_results, key=lambda r: r.timestamp)
        child_error = min(error_results, key=lambda r: r.timestamp)
        success_results = await memory.get_attack_results_async(objective="obj", outcome="success")
        assert len(success_results) == 1

        # Dispatch order: completed children first, then the failed child's
        # persisted error result. No invented ids.
        assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [
            success_results[0].attack_result_id,
            child_error.attack_result_id,
        ]
        # The child error result itself carries no child links.
        assert child_error.metadata.get(SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY) is None

    async def test_nested_compound_links_each_level(self, seed_group, sqlite_instance):
        target = MockPromptTarget()
        inner = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_BoomChildStrategy(objective_target=target), seed_group=seed_group),
            ],
        )
        outer = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=inner, seed_group=seed_group),
            ],
        )

        with pytest.raises(RuntimeError, match="child boom"):
            await outer.execute_async(objective="obj")

        memory = sqlite_instance
        error_results = await memory.get_attack_results_async(objective="obj", outcome="error")
        assert len(error_results) == 3
        by_time = sorted(error_results, key=lambda r: r.timestamp)
        leaf_error, inner_error, outer_error = by_time

        # Each envelope links its direct children in dispatch order.
        assert inner_error.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [leaf_error.attack_result_id]
        assert outer_error.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [inner_error.attack_result_id]
        assert leaf_error.metadata.get(SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY) is None

    async def test_scenario_attributed_execution_links_children(self, seed_group, sqlite_instance):
        target = MockPromptTarget()
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_BoomChildStrategy(objective_target=target), seed_group=seed_group),
            ],
            completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
        )
        context = _make_context()
        context._attribution = AttackResultAttribution(
            parent_id="8a8d17b9-b671-4a3d-8170-e65ea9b44053",
            parent_collection="test-scenario",
        )

        with pytest.raises(RuntimeError, match="child boom"):
            await compound.execute_with_context_async(context=context)

        memory = sqlite_instance
        error_results = await memory.get_attack_results_async(objective="obj", outcome="error")
        assert len(error_results) == 2
        parent = max(error_results, key=lambda r: r.timestamp)
        child_error = min(error_results, key=lambda r: r.timestamp)

        assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [child_error.attack_result_id]
        # The Scenario attribution still reaches the persisted parent error result.
        assert parent.attribution_parent_id == "8a8d17b9-b671-4a3d-8170-e65ea9b44053"

    async def test_concurrent_shared_exception_keeps_child_links_isolated_async(
        self, seed_group: AttackSeedGroup, sqlite_instance: MemoryInterface
    ) -> None:
        target = MockPromptTarget()
        child = _BoomChildStrategy(objective_target=target)
        sequences = [
            SequentialAttack(
                objective_target=target,
                child_attacks=[SequentialChildAttack(strategy=child, seed_group=seed_group)],
            )
            for _ in range(2)
        ]
        failure = RuntimeError("shared child failure")
        persist = sqlite_instance.add_attack_results_to_memory_async
        both_children_persisted = asyncio.Event()
        child_writes = 0

        async def persist_children_together_async(*, attack_results: Sequence[AttackResult]) -> None:
            nonlocal child_writes
            await persist(attack_results=attack_results)
            if attack_results[0].attribution_data["result_role"] == AttackResultRole.TARGET_FACING.value:
                child_writes += 1
                if child_writes == 2:
                    both_children_persisted.set()
                await both_children_persisted.wait()

        with (
            patch.object(child, "_perform_async", new_callable=AsyncMock, side_effect=failure),
            patch.object(
                sqlite_instance, "add_attack_results_to_memory_async", side_effect=persist_children_together_async
            ),
        ):
            failures = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        sequence.execute_async(objective="obj", memory_labels={"execution": str(index)})
                        for index, sequence in enumerate(sequences)
                    ],
                    return_exceptions=True,
                ),
                timeout=10,
            )

        assert all(isinstance(error, RuntimeError) for error in failures)
        for index in range(2):
            rows = await sqlite_instance.get_attack_results_async(labels={"execution": str(index)})
            [parent] = [row for row in rows if row.attribution_data["result_role"] == "orchestration"]
            [stored_child] = [row for row in rows if row.attribution_data["result_role"] == "target_facing"]
            assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [stored_child.attack_result_id]
        assert not hasattr(failure, "_pyrit_error_result_id")
        assert not hasattr(failure, "_pyrit_error_result_metadata")

    @pytest.mark.parametrize("child_fails", [False, True])
    @pytest.mark.parametrize("committed", [False, True])
    async def test_uncertain_child_write_links_only_confirmed_rows_async(
        self,
        seed_group: AttackSeedGroup,
        sqlite_instance: MemoryInterface,
        child_fails: bool,
        committed: bool,
    ) -> None:
        target = MockPromptTarget()
        uncertain_child = (_BoomChildStrategy if child_fails else _OkChildStrategy)(objective_target=target)
        skipped_child = _OkChildStrategy(objective_target=target)
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_OkChildStrategy(objective_target=target), seed_group=seed_group),
                SequentialChildAttack(
                    strategy=uncertain_child, seed_group=seed_group, memory_labels={"child": "uncertain"}
                ),
                SequentialChildAttack(strategy=skipped_child, seed_group=seed_group),
            ],
            completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
        )
        persist = sqlite_instance.add_attack_results_to_memory_async
        persistence_error = RuntimeError("commit acknowledgement lost")
        uncertain_ids: list[str] = []

        async def uncertain_write_async(*, attack_results: Sequence[AttackResult]) -> None:
            if attack_results[0].labels.get("child") == "uncertain":
                uncertain_ids.append(attack_results[0].attack_result_id)
                if committed:
                    await persist(attack_results=attack_results)
                raise persistence_error
            await persist(attack_results=attack_results)

        with (
            patch.object(sqlite_instance, "add_attack_results_to_memory_async", side_effect=uncertain_write_async),
            patch.object(skipped_child, "_perform_async", new_callable=AsyncMock) as skipped,
            pytest.raises(RuntimeError) as raised,
        ):
            await compound.execute_async(objective="obj")

        skipped.assert_not_awaited()
        assert len(uncertain_ids) == 1
        if child_fails:
            assert isinstance(raised.value.__cause__, ExceptionGroup)
            assert raised.value.__cause__.exceptions[1] is persistence_error
        else:
            assert raised.value.__cause__ is persistence_error
        rows = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        [parent] = [row for row in rows if row.attribution_data["result_role"] == "orchestration"]
        children = sorted(
            (row for row in rows if row.attribution_data["result_role"] == "target_facing"),
            key=lambda row: row.timestamp,
        )
        assert len(children) == 1 + int(committed)
        assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [
            child.attack_result_id for child in children
        ]
        assert (uncertain_ids[0] in parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY]) is committed

    async def test_nested_uncertain_error_write_preserves_direct_child_links_async(
        self, seed_group: AttackSeedGroup, sqlite_instance: MemoryInterface
    ) -> None:
        target = MockPromptTarget()
        inner = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_BoomChildStrategy(objective_target=target), seed_group=seed_group)
            ],
        )
        outer = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=inner, seed_group=seed_group, memory_labels={"envelope": "inner"})
            ],
        )
        persist = sqlite_instance.add_attack_results_to_memory_async
        inner_writes = 0

        async def persist_then_fail_inner_async(*, attack_results: Sequence[AttackResult]) -> None:
            nonlocal inner_writes
            await persist(attack_results=attack_results)
            row = attack_results[0]
            if row.attribution_data["result_role"] == "orchestration" and row.labels.get("envelope") == "inner":
                inner_writes += 1
                raise RuntimeError("inner commit acknowledgement lost")

        with (
            patch.object(
                sqlite_instance, "add_attack_results_to_memory_async", side_effect=persist_then_fail_inner_async
            ),
            pytest.raises(RuntimeError),
        ):
            await outer.execute_async(objective="obj")

        assert inner_writes == 1
        rows = await sqlite_instance.get_attack_results_async()
        [leaf] = [row for row in rows if row.attribution_data["result_role"] == "target_facing"]
        [inner_row] = [
            row
            for row in rows
            if row.attribution_data["result_role"] == "orchestration" and row.labels.get("envelope") == "inner"
        ]
        [outer_row] = [
            row
            for row in rows
            if row.attribution_data["result_role"] == "orchestration" and "envelope" not in row.labels
        ]
        assert inner_row.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [leaf.attack_result_id]
        assert outer_row.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [inner_row.attack_result_id]

    async def test_parameter_build_failure_keeps_only_completed_child_links_async(
        self, seed_group: AttackSeedGroup, sqlite_instance: MemoryInterface
    ) -> None:
        target = MockPromptTarget()
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_OkChildStrategy(objective_target=target), seed_group=seed_group)
                for _ in range(3)
            ],
            completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
        )
        build = AttackParameters.from_seed_group_async
        calls = 0

        async def build_then_fail_async(**kwargs: Any) -> AttackParameters:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("child preparation failed")
            return await build(**kwargs)

        with (
            patch.object(AttackParameters, "from_seed_group_async", side_effect=build_then_fail_async),
            pytest.raises(RuntimeError, match="child preparation failed"),
        ):
            await compound.execute_async(objective="obj")

        assert calls == 2
        rows = await sqlite_instance.get_attack_results_async()
        [parent] = [row for row in rows if row.outcome is AttackOutcome.ERROR]
        [child] = [row for row in rows if row.outcome is AttackOutcome.SUCCESS]
        assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == [child.attack_result_id]

    async def test_reused_context_keeps_failure_links_separate_async(
        self, seed_group: AttackSeedGroup, sqlite_instance: MemoryInterface
    ) -> None:
        target = MockPromptTarget()
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_BoomChildStrategy(objective_target=target), seed_group=seed_group)
            ],
        )
        context = _make_context()
        links: list[list[str]] = []
        for _ in range(2):
            with pytest.raises(RuntimeError, match="child boom"):
                await compound.execute_with_context_async(context=context)
            [parent] = await sqlite_instance.get_attack_results_async(attack_result_ids=[context.attack_result_id])
            links.append(parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY])
        assert len(links[0]) == len(links[1]) == 1
        assert set(links[0]).isdisjoint(links[1])

    @pytest.mark.parametrize("child_fails", [False, True])
    async def test_uncertain_write_lookup_failure_logs_without_masking_original_error_async(
        self,
        seed_group: AttackSeedGroup,
        sqlite_instance: MemoryInterface,
        caplog: pytest.LogCaptureFixture,
        child_fails: bool,
    ) -> None:
        target = MockPromptTarget()
        child = (_BoomChildStrategy if child_fails else _OkChildStrategy)(objective_target=target)
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[SequentialChildAttack(strategy=child, seed_group=seed_group)],
        )
        persist = sqlite_instance.add_attack_results_to_memory_async
        persistence_error = RuntimeError("commit acknowledgement lost")
        writes = 0

        async def persist_then_fail_child_async(*, attack_results: Sequence[AttackResult]) -> None:
            nonlocal writes
            writes += 1
            await persist(attack_results=attack_results)
            if attack_results[0].attribution_data["result_role"] == "target_facing":
                raise persistence_error

        with (
            patch.object(
                sqlite_instance, "add_attack_results_to_memory_async", side_effect=persist_then_fail_child_async
            ),
            patch.object(
                sqlite_instance,
                "get_attack_results_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("read failed"),
            ) as confirm,
            pytest.raises(RuntimeError) as raised,
        ):
            await compound.execute_async(objective="obj")

        assert writes == 2
        confirm.assert_awaited_once()
        assert "Unable to confirm persisted attack result" in caplog.text
        assert "read failed" in caplog.text
        if child_fails:
            assert isinstance(raised.value.__cause__, ExceptionGroup)
            assert raised.value.__cause__.exceptions[1] is persistence_error
        else:
            assert raised.value.__cause__ is persistence_error
        rows = await sqlite_instance.get_attack_results_async()
        [parent] = [row for row in rows if row.attribution_data["result_role"] == "orchestration"]
        assert len(rows) == 2
        assert parent.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY] == []
