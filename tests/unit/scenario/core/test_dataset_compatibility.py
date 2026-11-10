# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared preparation, policy validation, and independent delivery coverage."""

from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget

from pyrit.datasets import SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackSeedGroup,
    DatasetFetchPolicy,
    SeedDataset,
    SeedDatasetGroupProfile,
    SeedGroupRequirements,
    SeedMetadataRequirement,
    SeedObjective,
    SeedOrigin,
)
from pyrit.models.dataset_compatibility import DatasetCompatibilityStatus
from pyrit.models.dataset_limit import ResolvedDatasetLimit
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario import (
    AttackTechniqueFactory,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetSource,
    TechniqueRequirements,
)
from pyrit.scenario.core.dataset_compatibility import assess_candidate_profiles
from pyrit.scenario.scenarios.foundry import AssertPolicy
from pyrit.scenario.scenarios.foundry.assert_policy import _build_assert_policy_technique

pytestmark = pytest.mark.usefixtures("patch_central_database")


class _DeliveryMetadata(BaseModel):
    enabled: bool


@pytest.fixture(autouse=True)
def dataset_techniques() -> Iterator[None]:
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [
            AttackTechniqueFactory(
                name="objective_only",
                attack_class=PromptSendingAttack,
                requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True)),
            ),
            AttackTechniqueFactory(
                name="user_prompt",
                attack_class=PromptSendingAttack,
                requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(required_user_prompt=True)),
            ),
            AttackTechniqueFactory(
                name="marked",
                attack_class=PromptSendingAttack,
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(
                        metadata=(
                            SeedMetadataRequirement(
                                name="delivery",
                                key="delivery",
                                description="delivery metadata",
                                validator=lambda metadata: _DeliveryMetadata.model_validate(
                                    (metadata or {}).get("delivery")
                                ),
                            ),
                        )
                    )
                ),
            ),
            AttackTechniqueFactory(name="red_teaming", attack_class=RedTeamingAttack),
        ]
    )
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()


def _groups(*, group_count: int = 3) -> list[AttackSeedGroup]:
    groups = list(
        make_assert_result(
            config=make_assert_config(group_count=group_count, include_adversarial_prompt=False)
        ).seed_groups
    )
    groups[0] = AttackSeedGroup(seeds=[groups[0].objective])
    for group in groups:
        for seed in group.seeds:
            seed.origin = SeedOrigin.USER
            seed.source = "hand-imported policy cases"
    return groups


def _scenario(
    *,
    groups: list[AttackSeedGroup],
    techniques: tuple[str, ...] = ("objective_only", "user_prompt"),
    baseline: bool = True,
    policy: str = "skip",
    cap: ResolvedDatasetLimit = "all",
) -> AssertPolicy:
    scenario = AssertPolicy(judge_target=MockPromptTarget(), adversarial_chat=MockPromptTarget())
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scenario_techniques": [scenario._technique_class(name) for name in techniques],
            "dataset_config": DatasetAttackConfiguration(seed_groups=groups, max_per_dataset="all", max_total=cap),
            "include_baseline": baseline,
            "incompatible_technique_policy": policy,
        }
    )
    return scenario


async def test_imported_groups_and_disjoint_techniques_are_assessed_independently_async() -> None:
    groups = _groups()
    original = [group.model_dump() for group in groups]
    scenario = _scenario(groups=groups)
    result = await scenario.get_dataset_compatibility_async()

    assert result.selection_compatible is True
    assert result.items[0].status is DatasetCompatibilityStatus.COMPATIBLE
    counts = {item.technique_name: (item.usable_count, item.skipped_count) for item in result.items[0].techniques}
    assert counts == {"objective_only": (1, 2), "user_prompt": (2, 1), "baseline": (2, 1)}
    assert [group.model_dump() for group in groups] == original
    await scenario.initialize_async()
    assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 5


@pytest.mark.parametrize("baseline", [False, True])
async def test_zero_usable_selected_techniques_is_not_rescued_by_baseline_async(baseline: bool) -> None:
    scenario = _scenario(groups=_groups()[1:], techniques=("objective_only",), baseline=baseline)
    result = await scenario.get_dataset_compatibility_async()
    assert result.selection_compatible is False
    assert any(reason.code == "no_usable_techniques" for reason in result.selection_reasons)
    with pytest.raises(ValueError, match="No usable selected techniques"):
        await scenario.initialize_async()


async def test_strict_metadata_validation_precedes_sampling_and_cannot_be_bypassed_async() -> None:
    groups = [*_groups(), AttackSeedGroup(seeds=[SeedObjective(value="plain objective")])]
    scenario = _scenario(groups=groups, cap=1)
    with patch("pyrit.scenario.core.dataset_configuration.random.sample") as sample:
        result = await scenario.get_dataset_compatibility_async()
        assert result.selection_compatible is False
        assert "ASSERT generation metadata" in result.selection_reasons[0].message
        with pytest.raises(ValueError, match="ASSERT generation metadata"):
            await scenario.get_run_size_estimate_async()
        with pytest.raises(ValueError, match="ASSERT generation metadata"):
            await scenario.initialize_async()
    sample.assert_not_called()


@pytest.mark.parametrize("policy", ["skip", "raise"])
async def test_baseline_and_each_technique_follow_skip_or_raise_async(policy: str) -> None:
    result = await _scenario(groups=_groups(), policy=policy).get_dataset_compatibility_async()
    assert result.selection_compatible is (policy == "skip")
    assert all(item.skipped_count > 0 for item in result.items[0].techniques)


async def test_sampling_retains_usable_technique_input_instead_of_only_baseline_cases_async() -> None:
    scenario = _scenario(groups=_groups(), techniques=("objective_only",), cap=1)
    await scenario.initialize_async()
    assert len(scenario._atomic_attacks) == 1
    assert scenario._atomic_attacks[0].technique_name == "objective_only"
    assert len(scenario._atomic_attacks[0].seed_groups) == 1
    assert not scenario._atomic_attacks[0].seed_groups[0].prompts


async def test_sampling_distinguishes_compatibility_when_logical_ids_coincide_async() -> None:
    rejected = _groups()[1]
    accepted = AttackSeedGroup(
        seeds=[seed.model_copy(update={"id": uuid4(), "prompt_group_id": None}, deep=True) for seed in rejected.seeds]
    )
    accepted.objective.metadata = {**(accepted.objective.metadata or {}), "delivery": {"enabled": True}}
    assert accepted.logical_id == rejected.logical_id
    scenario = _scenario(groups=[rejected, accepted], techniques=("marked",), cap=1)
    with patch(
        "pyrit.scenario.core.dataset_configuration.random.sample", side_effect=lambda values, count: values[:count]
    ):
        await scenario.initialize_async()
    assert len(scenario._atomic_attacks) == 2
    assert all(attack.seed_groups[0].objective.metadata.get("delivery") for attack in scenario._atomic_attacks)


async def test_sampling_applies_limits_only_to_the_union_of_usable_inputs_async() -> None:
    groups = _groups(group_count=8)
    for group in groups[4:]:
        group.objective.metadata = {**(group.objective.metadata or {}), "delivery": {"enabled": True}}
    scenario = _scenario(groups=groups, techniques=("marked",), baseline=False, cap=3)
    with patch(
        "pyrit.scenario.core.dataset_configuration.random.sample", side_effect=lambda values, count: values[:count]
    ):
        await scenario.initialize_async()
    assert len(scenario._atomic_attacks) == 3
    assert all(attack.seed_groups[0].objective.metadata.get("delivery") for attack in scenario._atomic_attacks)


@pytest.mark.parametrize("policy", ["skip", "raise"])
async def test_wholly_incompatible_dataset_can_be_skipped_when_another_is_usable_async(policy: str) -> None:
    groups = _groups()[1:]
    groups[1].objective.metadata = {**(groups[1].objective.metadata or {}), "delivery": {"enabled": True}}
    for name, group in zip(("unusable", "usable"), groups, strict=True):
        for seed in group.seeds:
            seed.dataset_name = name
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )
    scenario = _scenario(groups=groups, techniques=("marked",), baseline=False, policy=policy)
    scenario.set_params_from_args(
        args={
            **scenario.params,
            "dataset_config": DatasetAttackConfiguration(
                sources=[DatasetSource(name="unusable"), DatasetSource(name="usable")], max_per_dataset="all"
            ),
        }
    )
    result = await scenario.get_dataset_compatibility_async()
    assert result.selection_compatible is (policy == "skip")
    if policy == "raise":
        with pytest.raises(ValueError, match="delivery metadata"):
            await scenario.initialize_async()
    else:
        await scenario.initialize_async()
        assert len(scenario._atomic_attacks) == 1
        assert scenario._atomic_attacks[0].seed_groups[0].objective.dataset_name == "usable"


async def test_compatibility_sampling_preserves_compound_child_limits_async() -> None:
    groups = _groups()
    scenario = _scenario(groups=groups, techniques=("objective_only",))
    scenario.set_params_from_args(
        args={
            **scenario.params,
            "dataset_config": CompoundDatasetAttackConfiguration(
                configurations=[
                    DatasetAttackConfiguration(seed_groups=groups[:2], max_total=1),
                    DatasetAttackConfiguration(seed_groups=groups[2:], max_total=1),
                ]
            ),
        }
    )
    await scenario.initialize_async()
    assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 2
    assert any(attack.technique_name == "objective_only" for attack in scenario._atomic_attacks)


async def test_invalid_refreshed_policy_is_rejected_before_replacing_stored_data_async() -> None:
    group = _groups()[1]
    for seed in group.seeds:
        seed.dataset_name = "policy_refresh"
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(seeds=group.seeds, added_by="test")
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "policy_refresh"
    provider.fetch_dataset_async = AsyncMock(
        return_value=SeedDataset(
            dataset_name="policy_refresh",
            seeds=[SeedObjective(value="New invalid policy", dataset_name="policy_refresh")],
        )
    )
    scenario = _scenario(groups=[group], techniques=("user_prompt",))
    scenario.set_params_from_args(
        args={
            **scenario.params,
            "dataset_config": DatasetAttackConfiguration(
                sources=[DatasetSource(name="policy_refresh", fetch=DatasetFetchPolicy.ALWAYS, provider=provider)],
                max_per_dataset="all",
            ),
        }
    )
    with pytest.raises(ValueError, match="ASSERT generation metadata"):
        await scenario.initialize_async()
    provider.fetch_dataset_async.assert_awaited_once()
    saved = await memory.get_seeds_async(dataset_name="policy_refresh")
    assert {seed.value for seed in saved} == {seed.value for seed in group.seeds}


def test_candidate_hints_never_run_a_schema_validator() -> None:
    scenario = _scenario(groups=_groups())
    requirements = scenario.get_dataset_requirements()
    parser = Mock(side_effect=AssertionError("Listing must not parse taxonomy bodies."))
    requirements = replace(requirements, metadata=(replace(requirements.metadata[0], validator=parser),))
    profile = SeedDatasetGroupProfile(
        dataset_name="imported",
        group_count=4,
        single_objective=True,
        single_seed=False,
        has_user_prompt=True,
        has_non_user_prompt=False,
        has_simulated_conversation=False,
        has_adversarial_prompts=False,
        metadata_keys=("assert_generation",),
    )
    result = assess_candidate_profiles(name="imported", profiles=[profile], requirements=requirements)
    assert result.status is DatasetCompatibilityStatus.NOT_VERIFIED
    parser.assert_not_called()


async def test_key_presence_is_not_validated_policy_metadata_async() -> None:
    groups = _groups()
    groups[1].objective.metadata = {"assert_generation": {"taxonomy": {}}}
    result = await _scenario(groups=groups).get_dataset_compatibility_async()
    assert result.selection_compatible is False
    assert result.items[0].status is DatasetCompatibilityStatus.INCOMPATIBLE
