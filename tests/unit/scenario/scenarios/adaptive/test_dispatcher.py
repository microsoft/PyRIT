# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
)
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    SeedObjective,
    SeedPrompt,
)
from pyrit.scenario.scenarios.adaptive.dispatcher import (
    ADAPTIVE_ATTEMPT_LABEL,
    ADAPTIVE_TECHNIQUE_ID_LABEL,
    ADAPTIVE_TECHNIQUE_NAME_LABEL,
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
def seed_group() -> AttackSeedGroup:
    return AttackSeedGroup(seeds=[SeedObjective(value="obj")])


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
        assert attack._child_attacks[0].memory_labels[ADAPTIVE_TECHNIQUE_ID_LABEL] == "a"
        assert attack._child_attacks[1].memory_labels[ADAPTIVE_TECHNIQUE_ID_LABEL] == "b"
        assert attack._child_attacks[0].memory_labels[ADAPTIVE_TECHNIQUE_NAME_LABEL] == "a"
        assert attack._child_attacks[1].memory_labels[ADAPTIVE_TECHNIQUE_NAME_LABEL] == "b"
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

    async def test_merges_real_system_prompt_technique_onto_user_turn_at_sequence_zero(self, target):
        """Regression for the adaptive role-merge bug, exercised through the real dispatcher path.

        ``build_attack_async`` calls ``AttackSeedGroup.with_technique`` (dispatcher line ~216) to
        apply a bundle's ``seed_technique`` to the objective's seed group. A ``from_system_prompt``
        technique merged onto a group whose opening turn is a ``user`` prompt at sequence 0 -- as in
        the ``airt_hate`` ``escalating_discrimination`` group -- used to raise ``Inconsistent roles
        found for sequence 0: {system, user}``. Unlike the mocked merge test above, this drives the
        real merge with real seeds so a future dispatcher or compatibility-gate change can't let the
        collision slip back in.
        """
        seed_group = AttackSeedGroup(
            seeds=[
                SeedObjective(value="obj"),
                SeedPrompt(value="opening user turn", data_type="text", role="user", sequence=0),
            ]
        )
        seed_technique = AttackTechniqueSeedGroup.from_system_prompt("Follow these rules.")
        bundle = _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS], seed_technique=seed_technique)
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={"a": bundle},
            selector=_StubSelector(technique_order=["a"]),
        )

        attack = await dispatcher.build_attack_async(seed_group=seed_group)

        execution_group = attack._child_attacks[0].seed_group
        system_prompts = [p for p in execution_group.prompts if p.role == "system"]
        assert len(system_prompts) == 1
        # System framing is normalized to sequence 0; the base user turn shifts to 1.
        assert system_prompts[0].sequence == 0
        assert execution_group.prompts[0].role == "system"
        assert [(p.role, p.sequence) for p in execution_group.prompts] == [("system", 0), ("user", 1)]


@pytest.mark.usefixtures("patch_central_database")
class TestRegisteredTechniqueIdentityRoundTrip:
    """
    Pin the selector identity labels through real child-result persistence.
    """

    async def test_registered_identity_and_name_are_persisted(self, sqlite_instance):
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory.memory_models import AttackResultEntry
        from pyrit.models import AttackSeedGroup, SeedObjective
        from tests.unit.mocks import MockPromptTarget

        live_target = MockPromptTarget()
        attack = PromptSendingAttack(objective_target=live_target)
        technique_identifier = "factory-identity-hash"
        bundles = {technique_identifier: TechniqueBundle(attack=attack, name="prompt_sending")}
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=live_target,
            techniques=bundles,
            selector=_StubSelector(technique_order=[technique_identifier]),
            max_attempts_per_objective=1,
        )

        sg = AttackSeedGroup(seeds=[SeedObjective(value="say hello")])
        sequential = await dispatcher.build_attack_async(seed_group=sg)
        await sequential.execute_async(objective="say hello")

        with sqlite_instance.get_session() as session:
            rows = session.query(AttackResultEntry).all()

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
        assert all(row.labels[ADAPTIVE_TECHNIQUE_ID_LABEL] == technique_identifier for row in matching_rows)
        assert all(row.labels[ADAPTIVE_TECHNIQUE_NAME_LABEL] == "prompt_sending" for row in matching_rows)
