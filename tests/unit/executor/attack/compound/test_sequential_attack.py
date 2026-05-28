# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``SequentialAttack``."""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.compound import (
    SequenceMode,
    SequentialAttack,
    SequentialAttackResult,
    SequentialAttackStep,
)
from pyrit.executor.attack.core.attack_executor import AttackExecutor, AttackExecutorResult
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_strategy import AttackContext
from pyrit.models import AttackOutcome, AttackResult, SeedAttackGroup, SeedObjective


def _make_strategy(*, outcomes: list[AttackOutcome], name: str = "attack") -> MagicMock:
    """Build a strategy mock annotated with the outcomes it should yield in order."""
    strategy = MagicMock(name=name)
    strategy._outcomes = outcomes
    strategy._name = name
    return strategy


def _make_seed_group(objective: str = "obj") -> SeedAttackGroup:
    return SeedAttackGroup(seeds=[SeedObjective(value=objective)])


def _make_context(
    *,
    objective: str = "obj",
    labels: Optional[dict[str, str]] = None,
) -> AttackContext[AttackParameters]:
    params_type = AttackParameters.excluding("next_message", "prepended_conversation")
    return AttackContext(params=params_type(objective=objective, memory_labels=labels or {}))


def _patch_run_step(*, strategies_by_id: dict[int, MagicMock]):
    """
    Patch ``SequentialAttack._run_step_async`` to return results driven by
    each strategy's ``_outcomes`` list (one outcome per invocation).

    Records every call onto a ``calls`` list so tests can assert on the
    ``step`` that was dispatched and the ``memory_labels`` that were applied.
    """
    counters: dict[int, int] = dict.fromkeys(strategies_by_id, 0)
    calls: list[dict] = []

    async def _stub(self, *, step, memory_labels):
        sid = id(step.strategy)
        idx = counters[sid]
        counters[sid] = idx + 1
        outcome = step.strategy._outcomes[idx]
        calls.append({"step": step, "memory_labels": dict(memory_labels)})
        return AttackResult(
            conversation_id=f"conv-{step.strategy._name}-{idx}",
            objective="obj",
            outcome=outcome,
        )

    patcher = patch.object(SequentialAttack, "_run_step_async", _stub)
    return patcher, calls


@pytest.fixture
def target() -> MagicMock:
    return MagicMock(name="objective_target")


@pytest.fixture
def seed_group() -> SeedAttackGroup:
    return _make_seed_group()


@pytest.mark.usefixtures("patch_central_database")
class TestInit:
    def test_init_rejects_empty_steps(self, target):
        with pytest.raises(ValueError, match="at least one"):
            SequentialAttack(objective_target=target, steps=[])


@pytest.mark.usefixtures("patch_central_database")
class TestValidate:
    @pytest.mark.parametrize("bad_objective", ["", "   ", "\n\t"])
    def test_validate_rejects_empty_objective(self, target, seed_group, bad_objective):
        step = SequentialAttackStep(
            strategy=_make_strategy(outcomes=[AttackOutcome.SUCCESS]),
            seed_group=seed_group,
        )
        compound = SequentialAttack(objective_target=target, steps=[step])
        with pytest.raises(ValueError, match="objective"):
            compound._validate_context(context=_make_context(objective=bad_objective))


@pytest.mark.usefixtures("patch_central_database")
class TestFirstSuccess:
    async def test_stops_on_first_success(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 1

    async def test_runs_all_on_failures(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        c = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="c")
        steps = [SequentialAttackStep(strategy=s, seed_group=seed_group) for s in (a, b, c)]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=SequenceMode.FIRST_SUCCESS)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b, id(c): c})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.FAILURE
        assert len(calls) == 3

    async def test_undetermined_outcome_does_not_stop(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.UNDETERMINED], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2

    async def test_error_outcome_does_not_stop(self, target, seed_group):
        """FIRST_SUCCESS is resilient: a transient ERROR should not abort the sequence."""
        a = _make_strategy(outcomes=[AttackOutcome.ERROR], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestFirstDecisive:
    async def test_stops_on_error(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.ERROR], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=SequenceMode.FIRST_DECISIVE)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.ERROR
        assert len(calls) == 1

    async def test_does_not_stop_on_failure(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=SequenceMode.FIRST_DECISIVE)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2

    async def test_does_not_stop_on_undetermined(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.UNDETERMINED], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=SequenceMode.FIRST_DECISIVE)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS
        assert len(calls) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestExhaustive:
    async def test_runs_every_step(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=SequenceMode.EXHAUSTIVE)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert len(calls) == 2
        # Any-success aggregation: envelope SUCCESS because A succeeded.
        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestOutcomeDerivation:
    @pytest.mark.parametrize(
        ("mode", "outcomes", "expected"),
        [
            # EXHAUSTIVE: any-success aggregation over every step.
            (SequenceMode.EXHAUSTIVE, [AttackOutcome.SUCCESS], AttackOutcome.SUCCESS),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.ERROR, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.UNDETERMINED],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.FAILURE, AttackOutcome.ERROR],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.EXHAUSTIVE,
                [AttackOutcome.UNDETERMINED, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            # STRICT_ALL: SUCCESS only if every executed step succeeded, ERROR if any errored,
            # else FAILURE. Short-circuits on the first non-SUCCESS.
            (
                SequenceMode.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (
                SequenceMode.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            (
                SequenceMode.STRICT_ALL,
                [AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.STRICT_ALL,
                [AttackOutcome.ERROR, AttackOutcome.ERROR],
                AttackOutcome.ERROR,
            ),
            # LAST_RESULT: pass through the last executed step's outcome verbatim.
            (
                SequenceMode.LAST_RESULT,
                [AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
                AttackOutcome.FAILURE,
            ),
            (
                SequenceMode.LAST_RESULT,
                [AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
                AttackOutcome.SUCCESS,
            ),
            (SequenceMode.LAST_RESULT, [AttackOutcome.UNDETERMINED], AttackOutcome.UNDETERMINED),
            (
                SequenceMode.LAST_RESULT,
                [AttackOutcome.ERROR, AttackOutcome.UNDETERMINED],
                AttackOutcome.UNDETERMINED,
            ),
        ],
    )
    async def test_outcome_aggregation(self, target, seed_group, mode, outcomes, expected):
        strategies = [_make_strategy(outcomes=[o], name=f"s{i}") for i, o in enumerate(outcomes)]
        steps = [SequentialAttackStep(strategy=s, seed_group=seed_group) for s in strategies]
        compound = SequentialAttack(objective_target=target, steps=steps, mode=mode)
        patcher, _ = _patch_run_step(strategies_by_id={id(s): s for s in strategies})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is expected

    async def test_default_mode_is_first_success(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="b")
        steps = [
            SequentialAttackStep(strategy=a, seed_group=seed_group),
            SequentialAttackStep(strategy=b, seed_group=seed_group),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, _ = _patch_run_step(strategies_by_id={id(a): a, id(b): b})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestLabels:
    async def test_context_labels_passed_through(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        steps = [SequentialAttackStep(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a})

        with patcher:
            await compound._perform_async(context=_make_context(labels={"foo": "bar"}))

        assert calls[0]["memory_labels"]["foo"] == "bar"

    async def test_step_labels_override_context_labels(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        steps = [
            SequentialAttackStep(
                strategy=a,
                seed_group=seed_group,
                memory_labels={"foo": "override", "extra": "x"},
            ),
        ]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, calls = _patch_run_step(strategies_by_id={id(a): a})

        with patcher:
            await compound._perform_async(context=_make_context(labels={"foo": "ctx"}))

        assert calls[0]["memory_labels"]["foo"] == "override"
        assert calls[0]["memory_labels"]["extra"] == "x"


@pytest.mark.usefixtures("patch_central_database")
class TestExecutorForwarding:
    async def test_executor_receives_step_inputs(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        adversarial = MagicMock(name="adversarial_chat")
        scorer = MagicMock(name="objective_scorer")
        step = SequentialAttackStep(
            strategy=a,
            seed_group=seed_group,
            adversarial_chat=adversarial,
            objective_scorer=scorer,
            memory_labels={"k": "v"},
        )
        compound = SequentialAttack(objective_target=target, steps=[step])

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
        # Context labels + step labels merged for the executor call.
        assert executor_call_kwargs["memory_labels"] == {"ctx": "1", "k": "v"}


@pytest.mark.usefixtures("patch_central_database")
class TestResultShape:
    async def test_returns_sequential_attack_result(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        steps = [SequentialAttackStep(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, steps=steps)
        patcher, _ = _patch_run_step(strategies_by_id={id(a): a})

        with patcher:
            result = await compound._perform_async(context=_make_context())

        assert isinstance(result, SequentialAttackResult)

    async def test_attempt_result_ids_in_order(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="a")
        b = _make_strategy(outcomes=[AttackOutcome.FAILURE], name="b")
        c = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="c")
        steps = [SequentialAttackStep(strategy=s, seed_group=seed_group) for s in (a, b, c)]
        compound = SequentialAttack(objective_target=target, steps=steps)

        captured_ids: list[str] = []

        async def _stub(self, *, step, memory_labels):
            inner = AttackResult(
                conversation_id=f"c-{step.strategy._name}",
                objective="obj",
                outcome=step.strategy._outcomes[0],
            )
            captured_ids.append(inner.attack_result_id)
            return inner

        with patch.object(SequentialAttack, "_run_step_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.attempt_result_ids == captured_ids

    async def test_fresh_result_id_not_equal_to_any_inner(self, target, seed_group):
        a = _make_strategy(outcomes=[AttackOutcome.SUCCESS], name="a")
        steps = [SequentialAttackStep(strategy=a, seed_group=seed_group)]
        compound = SequentialAttack(objective_target=target, steps=steps)

        inner_ids: list[str] = []

        async def _stub(self, *, step, memory_labels):
            inner = AttackResult(conversation_id="c", objective="obj", outcome=AttackOutcome.SUCCESS)
            inner_ids.append(inner.attack_result_id)
            return inner

        with patch.object(SequentialAttack, "_run_step_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.attack_result_id != inner_ids[0]
        assert result.outcome is AttackOutcome.SUCCESS
