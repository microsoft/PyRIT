# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
)
from pyrit.models import AttackOutcome, SeedAttackGroup, SeedObjective
from pyrit.scenario.scenarios.adaptive.dispatcher import (
    ADAPTIVE_ATTEMPT_LABEL,
    AdaptiveTechniqueDispatcher,
    TechniqueBundle,
)


def _make_bundle(*, name: str, outcomes: list[AttackOutcome], seed_technique=None) -> TechniqueBundle:
    """Build a TechniqueBundle whose attack stub yields the given outcomes in order."""
    attack = MagicMock(name=f"attack-{name}")
    attack._outcomes = outcomes
    attack._name = name
    return TechniqueBundle(attack=attack, name=name, seed_technique=seed_technique)


class _StubSelector:
    """A deterministic selector stub that returns techniques in the order given."""

    def __init__(self, *, technique_order: list[str]):
        self._order = technique_order

    async def select_async(
        self,
        *,
        technique_identifiers,
        objective: str,
        num_top_techniques: int = 1,
        scenario_result_id: str | None = None,
    ):
        return self._order[:num_top_techniques]


@pytest.fixture
def selector():
    return _StubSelector(technique_order=["a", "b", "c"])


@pytest.fixture
def target() -> MagicMock:
    return MagicMock(name="objective_target")


@pytest.fixture
def seed_group() -> SeedAttackGroup:
    return SeedAttackGroup(seeds=[SeedObjective(value="obj")])


class TestDispatcherInit:
    @pytest.mark.usefixtures("patch_central_database")
    def test_init_rejects_empty_techniques(self, target, selector):
        with pytest.raises(ValueError, match="techniques"):
            AdaptiveTechniqueDispatcher(
                objective_target=target,
                techniques={},
                selector=selector,
            )

    @pytest.mark.parametrize("bad_max", [0, -1])
    @pytest.mark.usefixtures("patch_central_database")
    def test_init_rejects_invalid_max_attempts(self, target, selector, bad_max):
        with pytest.raises(ValueError, match="max_attempts_per_objective"):
            AdaptiveTechniqueDispatcher(
                objective_target=target,
                techniques={"a": _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS])},
                selector=selector,
                max_attempts_per_objective=bad_max,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestCompatibleTechniques:
    def test_returns_all_when_no_seed_technique(self, target, selector, seed_group):
        bundles = {
            "a": _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS]),
            "b": _make_bundle(name="b", outcomes=[AttackOutcome.SUCCESS]),
        }
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
        )
        assert dispatcher.compatible_techniques(seed_group=seed_group) == ["a", "b"]


@pytest.mark.usefixtures("patch_central_database")
class TestBuildAttackAsync:
    async def test_builds_sequential_attack(self, target, seed_group):
        bundles = {
            "a": _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS]),
            "b": _make_bundle(name="b", outcomes=[AttackOutcome.SUCCESS]),
        }
        selector = _StubSelector(technique_order=["a", "b"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
            max_attempts_per_objective=2,
        )

        attack = await dispatcher.build_attack_async(seed_group=seed_group)

        # plain SequentialAttack (no adaptive subclass)
        assert isinstance(attack, SequentialAttack)
        assert type(attack) is SequentialAttack
        assert len(attack._child_attacks) == 2
        # children in selection order
        assert attack._child_attacks[0].strategy is bundles["a"].attack
        assert attack._child_attacks[1].strategy is bundles["b"].attack
        # 1-based per-attempt label stamped on each child
        assert attack._child_attacks[0].memory_labels[ADAPTIVE_ATTEMPT_LABEL] == "1"
        assert attack._child_attacks[1].memory_labels[ADAPTIVE_ATTEMPT_LABEL] == "2"
        # default policy is FIRST_SUCCESS
        assert attack._completion_policy is SequenceCompletionPolicy.FIRST_SUCCESS

    async def test_raises_when_no_compatible_techniques(self, target):
        # bundle with an incompatible seed technique
        incompatible_technique = MagicMock(name="incompatible_seed_technique")
        bundle = _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS], seed_technique=incompatible_technique)
        bundles = {"a": bundle}
        selector = _StubSelector(technique_order=["a"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
        )
        seed_group = MagicMock(name="seed_group")
        seed_group.objective = MagicMock(value="obj")
        seed_group.is_compatible_with_technique.return_value = False

        with pytest.raises(ValueError, match="no compatible techniques"):
            await dispatcher.build_attack_async(seed_group=seed_group)

    async def test_raises_when_seed_group_has_no_objective(self, target, selector):
        bundles = {"a": _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS])}
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
        )
        sg = MagicMock(name="seed_group")
        sg.objective = None
        with pytest.raises(ValueError, match="objective is not initialized"):
            await dispatcher.build_attack_async(seed_group=sg)

    async def test_respects_max_attempts(self, target, seed_group):
        bundles = {
            "a": _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS]),
            "b": _make_bundle(name="b", outcomes=[AttackOutcome.SUCCESS]),
            "c": _make_bundle(name="c", outcomes=[AttackOutcome.SUCCESS]),
        }
        selector = _StubSelector(technique_order=["a", "b", "c"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
            max_attempts_per_objective=2,
        )

        attack = await dispatcher.build_attack_async(seed_group=seed_group)
        # selector receives num_top_techniques=2, returns 2 items
        assert len(attack._child_attacks) == 2

    async def test_merges_seed_technique_into_child_seed_group(self, target):
        """When a bundle declares a seed_technique it is merged into the seed group for that child."""
        seed_technique = MagicMock(name="seed_technique")

        outer_sg = MagicMock(name="seed_group")
        outer_sg.objective = MagicMock(value="obj")
        outer_sg.is_compatible_with_technique.return_value = True
        merged_sg = MagicMock(name="merged_seed_group")
        outer_sg.with_technique.return_value = merged_sg

        bundle = _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS], seed_technique=seed_technique)
        bundles = {"a": bundle}
        selector = _StubSelector(technique_order=["a"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
        )

        attack = await dispatcher.build_attack_async(seed_group=outer_sg)
        # The merged seed group is forwarded to the child attack.
        outer_sg.with_technique.assert_called_once_with(technique=seed_technique)
        assert attack._child_attacks[0].seed_group is merged_sg


@pytest.mark.usefixtures("patch_central_database")
class TestEvalHashRoundTrip:
    """
    Pin the load-bearing invariant that ``compute_inner_attack_eval_hash``
    (used by ``AdaptiveScenario._build_techniques_dict`` to key the
    ``techniques`` dict and by the selector to look up historical stats)
    equals the ``eval_hash`` the executor stamps on persisted child rows.

    If the prediction helper and the write path ever drift (e.g. a new
    field is added to the eval-hash rule on one side only), the selector
    silently reads zero history for every technique and epsilon-greedy
    degrades to random with no error. This test runs a real
    ``PromptSendingAttack`` through the dispatcher's ``SequentialAttack``
    end-to-end and asserts the round-trip holds.
    """

    async def test_predicted_hash_matches_persisted_row(self, sqlite_instance):
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory.memory_models import AttackResultEntry
        from pyrit.models import SeedAttackGroup, SeedObjective
        from pyrit.models.identifiers import compute_inner_attack_eval_hash
        from tests.unit.mocks import MockPromptTarget

        live_target = MockPromptTarget()
        attack = PromptSendingAttack(objective_target=live_target)
        predicted_hash = compute_inner_attack_eval_hash(attack=attack)

        bundles = {predicted_hash: TechniqueBundle(attack=attack, name="prompt_sending")}
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=live_target,
            techniques=bundles,
            selector=_StubSelector(technique_order=[predicted_hash]),
            max_attempts_per_objective=1,
        )

        sg = SeedAttackGroup(seeds=[SeedObjective(value="say hello")])
        sequential = await dispatcher.build_attack_async(seed_group=sg)
        await sequential.execute_async(objective="say hello")

        with sqlite_instance.get_session() as session:
            rows = session.query(AttackResultEntry).all()

        # Drill into the persisted envelope to find rows whose inner attack is PromptSendingAttack,
        # then assert the eval_hash on those rows matches what the selector predicted.
        matching_rows = [
            r
            for r in rows
            if r.atomic_attack_identifier
            and r.atomic_attack_identifier.get("children", {})
            .get("attack_technique", {})
            .get("children", {})
            .get("attack", {})
            .get("class_name")
            == "PromptSendingAttack"
        ]
        assert matching_rows, (
            f"Expected at least one persisted row whose inner attack is PromptSendingAttack; "
            f"found rows: {[(r.id, r.atomic_attack_identifier) for r in rows]}"
        )
        for row in matching_rows:
            stamped_hash = row.atomic_attack_identifier["eval_hash"]
            assert stamped_hash == predicted_hash, (
                f"Selector-side eval_hash ({predicted_hash}) drifted from executor-stamped "
                f"eval_hash ({stamped_hash}) on persisted row {row.id}. "
                f"compute_inner_attack_eval_hash and AtomicAttackIdentifier.build must agree."
            )
