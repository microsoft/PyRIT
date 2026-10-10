# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.executor.attack import CrescendoAttack, PromptSendingAttack
from pyrit.models import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    MatchesObjective,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.prompt_target import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    TargetConfiguration,
    TargetRequirements,
    UnsupportedCapabilityBehavior,
)
from pyrit.scenario import (
    AttackTechnique,
    AttackTechniqueFactory,
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
)
from pyrit.scenario.core.technique_requirements import filter_seed_groups, prepare_seed_group
from tests.unit.mocks import MockPromptTarget


class TestSeedPreparation:
    def test_objective_only_is_checked_before_technique_seeds(self) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
        technique = AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format.")

        prepared = prepare_seed_group(
            seed_group=source,
            requirements=SeedGroupRequirements(objective_only=True),
            seed_technique=technique,
        )

        assert prepared.adaptation is None
        assert prepared.seed_group.objective.value == "objective"
        assert prepared.seed_group.prompts[0].value == "Use the supplied format."
        assert len(source.seeds) == 1

    def test_adaptation_preserves_source_conditions_metadata_and_harm_categories(self) -> None:
        source = AttackSeedGroup(
            seeds=[
                SeedObjective(
                    value="objective",
                    conditions=(MatchesObjective(),),
                    metadata={"source": {"index": 1}},
                    harm_categories=["objective-category"],
                ),
                SeedPrompt(value="context", harm_categories=["context-category"]),
            ]
        )
        original = source.model_dump()
        source_id = source.logical_id

        prepared = prepare_seed_group(
            seed_group=source,
            requirements=SeedGroupRequirements(objective_only=True, try_adapt=True),
        )

        assert prepared.adaptation == "objective_only"
        assert len(prepared.seed_group.seeds) == 1
        assert prepared.seed_group.objective.conditions == source.objective.conditions
        assert prepared.seed_group.objective.metadata == source.objective.metadata
        assert set(prepared.seed_group.harm_categories) == set(source.harm_categories)
        prepared.seed_group.objective.metadata["source"]["index"] = 2
        assert source.model_dump() == original
        assert source.logical_id == source_id

    def test_strict_objective_only_rejects_dataset_simulation(self) -> None:
        source = AttackSeedGroup(
            seeds=[
                SeedObjective(value="objective"),
                SeedSimulatedConversation(
                    num_turns=1, adversarial_chat_system_prompt=SeedPrompt(value="Use the supplied objective.")
                ),
            ]
        )

        with pytest.raises(IncompatibleTechniqueError, match="objective"):
            prepare_seed_group(seed_group=source, requirements=SeedGroupRequirements(objective_only=True))

    @pytest.mark.parametrize("failure", ["overlap", "roles", "duplicate_simulation"])
    def test_full_composition_rejects_invalid_groups(self, *, failure: str) -> None:
        simulation = SeedSimulatedConversation(
            num_turns=1, adversarial_chat_system_prompt=SeedPrompt(value="Use the supplied objective.")
        )
        if failure == "duplicate_simulation":
            source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), simulation])
            technique = AttackTechniqueSeedGroup(seeds=[simulation.model_copy(deep=True)])
        else:
            source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="user turn")])
            technique = AttackTechniqueSeedGroup(
                seeds=[
                    simulation
                    if failure == "overlap"
                    else SeedPrompt(value="system framing", role="system", is_general_technique=True)
                ]
            )
        original = source.model_dump()
        assert source.is_compatible_with_technique(technique=technique) is (failure != "overlap")

        with pytest.raises(IncompatibleTechniqueError, match="cannot be composed"):
            prepare_seed_group(seed_group=source, requirements=SeedGroupRequirements(), seed_technique=technique)

        assert source.model_dump() == original

    def test_permissive_requirements_keep_extra_prompts(self) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="user turn", sequence=99)])
        technique = AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format.")

        prepared = prepare_seed_group(seed_group=source, requirements=SeedGroupRequirements(), seed_technique=technique)

        assert [prompt.value for prompt in prepared.seed_group.prompts] == [
            "Use the supplied format.",
            "user turn",
        ]
        assert prepared.adaptation is None

    def test_unexpected_composition_error_propagates(self) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
        technique = AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format.")

        with patch.object(AttackSeedGroup, "with_technique", side_effect=RuntimeError("unexpected failure")):
            with pytest.raises(RuntimeError, match="unexpected failure"):
                filter_seed_groups(
                    seed_groups=[source],
                    requirements=SeedGroupRequirements(),
                    seed_technique=technique,
                    technique_name="example",
                    dataset_name="dataset",
                    policy=IncompatibleTechniquePolicy.SKIP,
                )

    @pytest.mark.parametrize("policy", list(IncompatibleTechniquePolicy))
    def test_seed_filter_applies_policy(
        self, *, policy: IncompatibleTechniquePolicy, caplog: pytest.LogCaptureFixture
    ) -> None:
        accepted = AttackSeedGroup(seeds=[SeedObjective(value="accepted")])
        rejected = AttackSeedGroup(seeds=[SeedObjective(value="rejected"), SeedPrompt(value="context")])
        arguments = {
            "seed_groups": [accepted, rejected],
            "requirements": SeedGroupRequirements(objective_only=True),
            "seed_technique": None,
            "technique_name": "example",
            "dataset_name": "dataset",
            "policy": policy,
        }

        if policy is IncompatibleTechniquePolicy.RAISE:
            with pytest.raises(IncompatibleTechniqueError, match=rejected.logical_id):
                filter_seed_groups(**arguments)
        else:
            retained = filter_seed_groups(**arguments)
            assert retained == [accepted]
            assert retained[0] is accepted
            summaries = [record for record in caplog.records if record.name.endswith("technique_requirements")]
            assert len(summaries) == 1
            assert "skipped 1" in summaries[0].getMessage()

    def test_adaptation_filter_returns_original_groups(self, caplog: pytest.LogCaptureFixture) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])

        retained = filter_seed_groups(
            seed_groups=[source],
            requirements=SeedGroupRequirements(objective_only=True, try_adapt=True),
            seed_technique=None,
            technique_name="example",
            dataset_name="dataset",
            policy=IncompatibleTechniquePolicy.SKIP,
        )

        assert retained[0] is source
        assert len(source.seeds) == 2
        assert "1 require objective-only adaptation" in caplog.text


@pytest.mark.usefixtures("patch_central_database")
class TestTechniqueTargetRequirements:
    def test_declared_adaptation_cannot_weaken_native_attack_requirement(self) -> None:
        target = MockPromptTarget(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(supports_multi_turn=False, supports_editable_history=True),
                policy=CapabilityHandlingPolicy(
                    behaviors={CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT}
                ),
            )
        )
        requirements = TechniqueRequirements(
            objective_target=TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN}))
        )

        requirements.objective_target.validate(target=target)
        assert requirements.check_target(target=target, attack_class=CrescendoAttack)
        with pytest.raises(IncompatibleTechniqueError, match="natively support 'supports_multi_turn'"):
            requirements.validate_target(target=target, attack_class=CrescendoAttack)

    def test_factory_checks_target_before_construction(self) -> None:
        target = MockPromptTarget(
            custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_system_prompt=False))
        )
        factory = AttackTechniqueFactory(
            name="native_system",
            attack_class=PromptSendingAttack,
            requirements=TechniqueRequirements(
                objective_target=TargetRequirements(native_required=frozenset({CapabilityName.SYSTEM_PROMPT}))
            ),
        )

        with patch.object(PromptSendingAttack, "__init__") as constructor:
            with pytest.raises(IncompatibleTechniqueError, match="system_prompt"):
                factory.create(objective_target=target, attack_scoring_config=None)
            constructor.assert_not_called()

    def test_factory_and_copy_helpers_keep_requirements(self) -> None:
        requirements = TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True))
        factory = AttackTechniqueFactory(name="example", attack_class=PromptSendingAttack, requirements=requirements)

        assert (
            factory.create(objective_target=MockPromptTarget(), attack_scoring_config=None).requirements is requirements
        )
        assert factory.with_attack_kwargs(attack_kwargs={}).requirements is requirements
        adversarial_factory = AttackTechniqueFactory(
            name="adversarial", attack_class=CrescendoAttack, requirements=requirements
        )
        assert (
            adversarial_factory.with_adversarial_system_prompt_prefix("Use the supplied format.").requirements
            is requirements
        )

    def test_simulated_factory_defaults_to_strict_objective_only(self) -> None:
        factory = AttackTechniqueFactory.with_simulated_conversation(
            name="simulation",
            attack_class=PromptSendingAttack,
            num_turns=1,
            adversarial_chat_system_prompt=SeedPrompt(value="Use the supplied objective."),
        )

        assert factory.requirements.seed_group == SeedGroupRequirements(objective_only=True)

    def test_only_execution_changing_requirements_change_identity(self) -> None:
        factory = AttackTechniqueFactory(name="example", attack_class=PromptSendingAttack)
        acceptance = TechniqueRequirements(
            objective_target=TargetRequirements(native_required=frozenset({CapabilityName.SYSTEM_PROMPT})),
            seed_group=SeedGroupRequirements(objective_only=True),
        )
        adaptation = TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True))
        accepted_factory = AttackTechniqueFactory(
            name="example", attack_class=PromptSendingAttack, requirements=acceptance
        )
        adapted_factory = AttackTechniqueFactory(
            name="example", attack_class=PromptSendingAttack, requirements=adaptation
        )
        attack = PromptSendingAttack(objective_target=MockPromptTarget())
        default = AttackTechnique(attack=attack)
        accepted = AttackTechnique(attack=attack, requirements=acceptance)
        adapted = AttackTechnique(attack=attack, requirements=adaptation)

        assert factory.get_identifier().hash == accepted_factory.get_identifier().hash
        assert factory.get_identifier().hash != adapted_factory.get_identifier().hash
        assert default.get_identifier().hash == accepted.get_identifier().hash
        assert default.get_identifier().hash != adapted.get_identifier().hash
        default_eval = AtomicAttackEvaluationIdentifier(
            AtomicAttackIdentifier.build(technique_identifier=default.get_identifier())
        ).eval_hash
        adapted_eval = AtomicAttackEvaluationIdentifier(
            AtomicAttackIdentifier.build(technique_identifier=adapted.get_identifier())
        ).eval_hash
        assert default_eval != adapted_eval
