# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from pyrit.models import (
    AttackSeedGroup,
    ChatMessageRole,
    MatchesObjective,
    SeedGroupRequirements,
    SeedMetadataRequirement,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)


def test_empty_requirements_accept_additional_prompts() -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])

    assert SeedGroupRequirements().check(seed_group=group) == []


@pytest.mark.parametrize(
    ("requirements", "message"),
    [
        (SeedGroupRequirements(required_user_prompt=True), "stored user prompt"),
        (SeedGroupRequirements(required_adversarial_prompt=True), "Stored adversarial prompts"),
    ],
)
def test_required_inputs_are_checked(*, requirements: SeedGroupRequirements, message: str) -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
    assert message in requirements.check(seed_group=group)[0]


def test_named_metadata_requirement_reuses_owning_parser() -> None:
    from unit.assert_mocks import make_assert_result

    from pyrit.models import AssertGroupMetadata

    requirements = SeedGroupRequirements(
        metadata=(
            SeedMetadataRequirement(
                name="assert_generation",
                key=AssertGroupMetadata.METADATA_KEY,
                description="ASSERT policy metadata",
                validator=AssertGroupMetadata.from_metadata,
            ),
        )
    )
    group = make_assert_result().seed_groups[0]
    assert requirements.check(seed_group=group) == []
    group.objective.metadata = {AssertGroupMetadata.METADATA_KEY: {}}
    assert requirements.check(seed_group=group) == ["Requires valid ASSERT policy metadata (assert_generation)."]
    assert requirements.to_identity()["metadata"] == [
        {"name": "assert_generation", "key": "assert_generation", "version": 1}
    ]


def test_default_requirement_identity_remains_unchanged() -> None:
    assert SeedGroupRequirements().to_identity() == {
        "objective_only": False,
        "try_adapt": False,
        "user_prompts_only": False,
    }


def test_named_requirement_distinguishes_a_missing_key_from_a_valid_null() -> None:
    parser = Mock(return_value=None)
    requirements = SeedGroupRequirements(
        metadata=(
            SeedMetadataRequirement(
                name="nullable_reference", key="answer", description="reference answer", validator=parser
            ),
        )
    )
    nullable = AttackSeedGroup(seeds=[SeedObjective(value="objective", metadata={"answer": None})])
    missing = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
    assert requirements.check(seed_group=nullable) == []
    assert requirements.check(seed_group=missing) == ["Requires valid reference answer (nullable_reference)."]
    parser.assert_called_once()


def test_objective_only_accepts_objective_conditions_and_metadata() -> None:
    group = AttackSeedGroup(
        seeds=[SeedObjective(value="objective", conditions=(MatchesObjective(),), metadata={"source": "example"})]
    )

    assert SeedGroupRequirements(objective_only=True).check(seed_group=group) == []


@pytest.mark.parametrize("sequence", [0, 99])
def test_objective_only_rejects_additional_prompts(sequence: int) -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context", sequence=sequence)])
    original = group.model_dump()

    assert SeedGroupRequirements(objective_only=True).check(seed_group=group)
    assert group.model_dump() == original


def test_adaptation_permission_does_not_change_check_result() -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])

    assert SeedGroupRequirements(objective_only=True, try_adapt=True).check(seed_group=group)
    assert len(group.seeds) == 2


def test_adaptation_requires_objective_only_constraint() -> None:
    with pytest.raises(ValueError, match="objective_only"):
        SeedGroupRequirements(try_adapt=True)


def test_requirements_are_frozen() -> None:
    requirements = SeedGroupRequirements()

    with pytest.raises(FrozenInstanceError):
        requirements.objective_only = True  # type: ignore[misc]


@pytest.mark.parametrize("with_prompts", [False, True])
def test_user_prompts_only_accepts_objective_and_static_user_prompts(with_prompts: bool) -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective", metadata={"source": "dataset"})])
    if with_prompts:
        group = AttackSeedGroup(seeds=[*group.seeds, SeedPrompt(value="context", role="user", sequence=99)])
    original = group.model_dump()

    assert SeedGroupRequirements(user_prompts_only=True).check(seed_group=group) == []
    assert group.model_dump() == original


@pytest.mark.parametrize("role", ["assistant", "system", "tool"])
def test_user_prompts_only_rejects_non_user_history(role: ChatMessageRole) -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="authored history", role=role)])
    original = group.model_dump()
    source_id = group.logical_id

    assert "non-user history" in SeedGroupRequirements(user_prompts_only=True).check(seed_group=group)[0]
    assert SeedGroupRequirements().check(seed_group=group) == []
    assert group.model_dump() == original
    assert group.logical_id == source_id


def test_user_prompts_only_rejects_simulation_without_mutating_source() -> None:
    group = AttackSeedGroup(
        seeds=[
            SeedObjective(value="objective"),
            SeedPrompt(value="context", sequence=0),
            SeedSimulatedConversation(
                sequence=2,
                num_turns=1,
                adversarial_chat_system_prompt=SeedPrompt(value="Use the supplied objective."),
            ),
        ]
    )
    original = group.model_dump()

    assert "simulated conversations" in SeedGroupRequirements(user_prompts_only=True).check(seed_group=group)[0]
    assert SeedGroupRequirements().check(seed_group=group) == []
    assert group.model_dump() == original
