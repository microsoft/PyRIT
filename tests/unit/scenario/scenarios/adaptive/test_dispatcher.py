# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from pyrit.executor.attack import AttackStrategy, PromptSendingAttack
from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import (
    AttackTechnique,
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
)
from pyrit.scenario.scenarios.adaptive.dispatcher import (
    ADAPTIVE_ATTEMPT_LABEL,
    AdaptiveTechniqueDispatcher,
    TechniqueBundle,
)
from tests.unit.mocks import MockPromptTarget


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
    def test_adaptation_requires_behavioral_identifier(self) -> None:
        with pytest.raises(ValueError, match="require a technique_identifier"):
            TechniqueBundle(
                attack=MagicMock(spec=PromptSendingAttack),
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                ),
            )

    @pytest.mark.parametrize("policy", list(IncompatibleTechniquePolicy))
    def test_strict_dataset_requirements_follow_policy(
        self, *, policy: IncompatibleTechniquePolicy, target: PromptTarget, selector: _StubSelector
    ) -> None:
        group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context", sequence=99)])
        bundle = TechniqueBundle(
            attack=MagicMock(),
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True)),
        )
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={"strict": bundle},
            selector=selector,
            incompatible_technique_policy=policy,
        )

        if policy is IncompatibleTechniquePolicy.RAISE:
            with pytest.raises(IncompatibleTechniqueError, match="only an objective"):
                dispatcher.compatible_techniques(seed_group=group)
        else:
            assert dispatcher.compatible_techniques(seed_group=group) == []

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
    async def test_restores_saved_order_without_selecting_async(
        self, *, target: PromptTarget, seed_group: AttackSeedGroup
    ) -> None:
        bundles = {name: _make_bundle(name=name, outcomes=[AttackOutcome.SUCCESS]) for name in ["a", "b"]}
        selector = _StubSelector(technique_order=["a", "b"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
            max_attempts_per_objective=2,
        )
        with patch.object(selector, "select_async", new_callable=AsyncMock) as select_techniques:
            attack = await dispatcher.build_attack_async(
                seed_group=seed_group,
                selected_technique_eval_hashes=["b", "a"],
            )
            select_techniques.assert_not_awaited()

        assert [child.strategy for child in attack._child_attacks] == [bundles["b"].attack, bundles["a"].attack]
        assert dispatcher.selected_technique_eval_hashes == ["b", "a"]

    @pytest.mark.parametrize(
        ("selected_hashes", "match"),
        [
            ([], "between 1 and 2"),
            (["missing"], "unavailable or incompatible"),
            (["a", "b", "a"], "between 1 and 2"),
        ],
    )
    async def test_rejects_invalid_saved_choices_async(
        self,
        *,
        selected_hashes: list[str],
        match: str,
        target: PromptTarget,
        seed_group: AttackSeedGroup,
    ) -> None:
        selector = _StubSelector(technique_order=["a", "b"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={name: _make_bundle(name=name, outcomes=[AttackOutcome.SUCCESS]) for name in ["a", "b"]},
            selector=selector,
            max_attempts_per_objective=2,
        )
        with patch.object(selector, "select_async", new_callable=AsyncMock) as select_techniques:
            with pytest.raises(ValueError, match=match):
                await dispatcher.build_attack_async(
                    seed_group=seed_group,
                    selected_technique_eval_hashes=selected_hashes,
                )
            select_techniques.assert_not_awaited()

    async def test_saved_choice_cannot_be_replaced_by_compatible_alternative_async(
        self, *, target: PromptTarget
    ) -> None:
        selector = _StubSelector(technique_order=["plain"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={
                "strict": TechniqueBundle(
                    attack=MagicMock(spec=PromptSendingAttack),
                    requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True)),
                ),
                "plain": _make_bundle(name="plain", outcomes=[AttackOutcome.SUCCESS]),
            },
            selector=selector,
        )
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])
        with patch.object(selector, "select_async", new_callable=AsyncMock) as select_techniques:
            with pytest.raises(ValueError, match="unavailable or incompatible.*"):
                await dispatcher.build_attack_async(
                    seed_group=source,
                    selected_technique_eval_hashes=["strict"],
                )
            select_techniques.assert_not_awaited()

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
        incompatible_technique = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="system framing", role="system", is_general_technique=True)]
        )
        bundle = _make_bundle(name="a", outcomes=[AttackOutcome.SUCCESS], seed_technique=incompatible_technique)
        bundles = {"a": bundle}
        selector = _StubSelector(technique_order=["a"])
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques=bundles,
            selector=selector,
        )
        seed_group = AttackSeedGroup(seeds=[SeedObjective(value="obj"), SeedPrompt(value="user turn", role="user")])

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
        seed_technique = AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format.")
        outer_sg = AttackSeedGroup(seeds=[SeedObjective(value="obj")])
        original = outer_sg.model_dump()

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
        execution_group = attack._child_attacks[0].seed_group
        assert execution_group is not outer_sg
        assert execution_group.objective.value == "obj"
        assert execution_group.prompts[0].value == "Use the supplied format."
        assert outer_sg.model_dump() == original

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

    async def test_predicted_hash_matches_persisted_row_async(self, *, sqlite_instance: SQLiteMemory) -> None:
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory.memory_models import AttackResultEntry
        from pyrit.models import AttackSeedGroup, SeedObjective
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

        sg = AttackSeedGroup(seeds=[SeedObjective(value="say hello")])
        sequential = await dispatcher.build_attack_async(seed_group=sg)
        assert sequential.get_identifier().hash == AttackStrategy._build_identifier(sequential).hash
        await sequential.execute_async(objective="say hello")

        async with await sqlite_instance.get_session_async() as session:
            rows = (await session.scalars(select(AttackResultEntry))).all()

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

    @pytest.mark.parametrize("fail", [False, True])
    async def test_adapted_child_input_identity_and_metadata_round_trip_async(
        self, *, fail: bool, sqlite_instance: SQLiteMemory
    ) -> None:
        target = MockPromptTarget()
        technique = AttackTechnique(
            attack=PromptSendingAttack(objective_target=target),
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)),
        )
        expected_hash = AtomicAttackEvaluationIdentifier(
            AtomicAttackIdentifier.build(technique_identifier=technique.get_identifier())
        ).eval_hash
        source = AttackSeedGroup(
            seeds=[SeedObjective(value="say hello"), SeedPrompt(value="dataset context", harm_categories=["context"])]
        )
        original = source.model_dump()
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={
                expected_hash: TechniqueBundle(
                    attack=technique.attack,
                    requirements=technique.requirements,
                    technique_identifier=technique.get_identifier(),
                )
            },
            selector=_StubSelector(technique_order=[expected_hash]),
        )

        sequence = await dispatcher.build_attack_async(seed_group=source)

        child = sequence._child_attacks[0]
        assert len(child.seed_group.seeds) == 1
        assert child.seed_group.harm_categories == source.harm_categories
        assert child.atomic_attack_identifier.logical_seed_group_id == source.logical_id
        assert dispatcher.selected_adaptations == {expected_hash: "objective_only"}
        assert sequence.get_identifier().params["child_technique_eval_hashes"] == [expected_hash]
        if fail:
            with patch.object(target, "_send_prompt_to_target_async", side_effect=RuntimeError("synthetic failure")):
                with pytest.raises(RuntimeError, match="synthetic failure"):
                    await sequence.execute_async(objective=source.objective.value)
        else:
            await sequence.execute_async(objective=source.objective.value)
            assert target.prompt_sent == ["say hello"]
        stored = await sqlite_instance.get_attack_results_async(atomic_attack_eval_hashes=[expected_hash])
        assert len(stored) == 1
        result = stored[0]
        identifier = AtomicAttackIdentifier.from_component_identifier(result.atomic_attack_identifier)
        assert identifier.eval_hash == expected_hash
        assert identifier.logical_seed_group_id == source.logical_id
        assert result.metadata["seed_group_adaptation"] == {
            "source_seed_group_id": source.logical_id,
            "technique_eval_hash": expected_hash,
            "adaptation": "objective_only",
        }
        assert result.targeted_harm_categories == ["context"]
        assert (result.outcome is AttackOutcome.ERROR) is fail
        assert source.model_dump() == original
