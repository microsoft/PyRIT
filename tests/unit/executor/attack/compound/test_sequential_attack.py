# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``SequentialAttack``."""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.compound import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialAttackResult,
    SequentialChildAttack,
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


def _patch_run_child_attack(*, strategies_by_id: dict[int, MagicMock]):
    """
    Patch ``SequentialAttack._run_child_attack_async`` to return results driven by
    each strategy's ``_outcomes`` list (one outcome per invocation).

    Records every call onto a ``calls`` list so tests can assert on the
    ``child_attack`` that was dispatched and the ``memory_labels`` that were applied.
    """
    counters: dict[int, int] = dict.fromkeys(strategies_by_id, 0)
    calls: list[dict] = []

    async def _stub(self, *, child_attack, memory_labels, attribution=None):
        sid = id(child_attack.strategy)
        idx = counters[sid]
        counters[sid] = idx + 1
        outcome = child_attack.strategy._outcomes[idx]
        calls.append(
            {
                "child_attack": child_attack,
                "memory_labels": dict(memory_labels),
                "attribution": attribution,
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
def seed_group() -> SeedAttackGroup:
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
                AttackOutcome.FAILURE,
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
                AttackOutcome.FAILURE,
            ),
            # STRICT_ALL: SUCCESS only if every executed child_attack succeeded, ERROR if any errored,
            # else FAILURE. Short-circuits on the first non-SUCCESS.
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
                AttackOutcome.FAILURE,
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
        ``AttackResult`` rows can be attributed to the parent."""
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

        assert executor_call_kwargs["attribution"] is attribution


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

        async def _stub(self, *, child_attack, memory_labels, attribution=None):
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

        async def _stub(self, *, child_attack, memory_labels, attribution=None):
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

        async def _stub(self, *, child_attack, memory_labels, attribution=None):
            return AttackResult(
                conversation_id="c",
                objective="obj",
                outcome=child_attack.strategy._outcomes[0],
                executed_turns=3,
            )

        with patch.object(SequentialAttack, "_run_child_attack_async", _stub):
            result = await compound._perform_async(context=_make_context())

        assert result.executed_turns == 6
