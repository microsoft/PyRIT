# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared preparation, policy validation, and independent delivery coverage."""

import threading
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, ClassVar
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
    AssertGroupMetadata,
    AttackSeedGroup,
    DatasetFetchPolicy,
    DatasetSelectionMode,
    ScenarioDatasetOptions,
    SeedDataset,
    SeedDatasetGroupProfile,
    SeedGroupRequirements,
    SeedMetadataRequirement,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
)
from pyrit.models.dataset_limit import ResolvedDatasetLimit
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario import (
    AttackTechniqueFactory,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetSource,
    Scenario,
    TechniqueRequirements,
)
from pyrit.scenario.core import (
    DatasetConstraintError,
    ScenarioRequiredData,
    TechniqueResolutionError,
    override_default_adversarial_target,
)
from pyrit.scenario.core.dataset_compatibility import assess_candidate_profiles
from pyrit.scenario.scenarios.airt import Scam
from pyrit.scenario.scenarios.foundry import AssertPolicy
from pyrit.scenario.scenarios.foundry.assert_policy import _build_assert_policy_technique
from pyrit.score import SubStringScorer

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
                attack_class=RedTeamingAttack,
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
    groups[0] = make_assert_result(
        config=make_assert_config(group_count=1, include_user_prompt=False, include_adversarial_prompt=True)
    ).seed_groups[0]
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
    adversarial = MockPromptTarget()
    adversarial.apply_capabilities(
        capabilities=adversarial.capabilities.model_copy(
            update={"supports_json_output": True, "supports_json_schema": True}
        )
    )
    scenario = AssertPolicy(adversarial_chat=adversarial)
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scoring_target": MockPromptTarget(),
            "scenario_techniques": [scenario._technique_class(name) for name in techniques],
            "dataset_config": DatasetAttackConfiguration(seed_groups=groups, max_per_dataset="all", max_total=cap),
            "include_baseline": baseline,
            "incompatible_technique_policy": policy,
        }
    )
    return scenario


def _units_by_technique(scenario: AssertPolicy) -> dict[str, int]:
    units: Counter[str] = Counter()
    for attack in scenario._atomic_attacks:
        units[attack.technique_name or "baseline"] += len(attack.seed_groups)
    return dict(units)


def _package_scenario(*, groups: list[AttackSeedGroup]) -> AssertPolicy:
    chat = MockPromptTarget()
    chat.apply_capabilities(
        capabilities=chat.capabilities.model_copy(update={"supports_json_output": True, "supports_json_schema": True})
    )
    scenario = AssertPolicy(adversarial_chat=chat)
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scoring_target": MockPromptTarget(),
            "dataset_config": DatasetAttackConfiguration(seed_groups=groups, max_per_dataset="all", max_total="all"),
        }
    )
    return scenario


async def test_default_assert_package_and_baseline_have_independent_coverage_async() -> None:
    groups = [
        *make_assert_result(config=make_assert_config(group_count=2, include_adversarial_prompt=True)).seed_groups,
        *make_assert_result(config=make_assert_config(group_count=2, include_adversarial_prompt=False)).seed_groups,
    ]
    scenario = _package_scenario(groups=groups)
    await scenario.get_run_size_estimate_async()
    await scenario.initialize_async()
    assert _units_by_technique(scenario) == {"assert_package": 4, "baseline": 4}


async def test_default_package_accepts_native_prompt_cases_without_requiring_tester_inputs_async() -> None:
    groups = list(
        make_assert_result(config=make_assert_config(group_count=2, include_adversarial_prompt=False)).seed_groups
    )
    scenario = _package_scenario(groups=groups)
    await scenario.get_run_size_estimate_async()
    await scenario.initialize_async()
    assert _units_by_technique(scenario) == {"assert_package": 2, "baseline": 2}
    assert all(isinstance(attack.attack_technique.attack, PromptSendingAttack) for attack in scenario._atomic_attacks)


async def test_package_assessment_does_not_bind_unused_stored_prompt_slots_async() -> None:
    groups = list(make_assert_result(config=make_assert_config(group_count=1)).seed_groups)
    group = groups[0]
    prompts = group.adversarial_prompts
    assert prompts is not None and prompts.system_prompt is not None
    system = prompts.system_prompt.model_copy(update={"value": "{{ ignored }}", "parameters": ["ignored"]}, deep=True)
    prompts = prompts.model_copy(update={"system_prompt": system})
    group.adversarial_prompts = prompts
    group.objective.metadata = {**(group.objective.metadata or {}), **prompts.to_metadata()}
    scenario = _package_scenario(groups=groups)
    await scenario.get_run_size_estimate_async()
    await scenario.initialize_async()
    assert len(scenario._atomic_attacks) == 2


async def test_skip_policy_failure_names_the_target_requirement_async() -> None:
    groups = list(make_assert_result(config=make_assert_config(group_count=1)).seed_groups)
    scenario = _package_scenario(groups=groups)
    target = scenario.params["objective_target"]
    assert isinstance(target, MockPromptTarget)
    target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_multi_turn": False}))
    # Prompt-case delivery fits this target, so readiness passes; only the stored conversation case can't run.
    assert not [issue for issue in scenario.get_readiness().issues if issue.role == "objective_target"]
    expected = r"No usable selected techniques\..*'assert_package' is incompatible with the objective target"
    with pytest.raises(ValueError, match=expected):
        await scenario.get_run_size_estimate_async()
    with pytest.raises(ValueError, match=expected):
        await scenario.initialize_async()


async def test_imported_groups_and_disjoint_techniques_are_assessed_independently_async() -> None:
    groups = _groups()
    original = [group.model_dump() for group in groups]
    scenario = _scenario(groups=groups)
    await scenario.get_run_size_estimate_async()
    assert [group.model_dump() for group in groups] == original
    await scenario.initialize_async()
    assert _units_by_technique(scenario) == {"objective_only": 1, "user_prompt": 2, "baseline": 2}


@pytest.mark.parametrize("baseline", [False, True])
async def test_zero_usable_selected_techniques_is_not_rescued_by_baseline_async(baseline: bool) -> None:
    scenario = _scenario(groups=_groups()[1:], techniques=("objective_only",), baseline=baseline)
    # Skip-policy failures still say why the technique skipped each group.
    expected = "No usable selected techniques.*'objective_only' skips 2 group"
    with pytest.raises(ValueError, match=expected):
        await scenario.get_run_size_estimate_async()
    with pytest.raises(ValueError, match=expected):
        await scenario.initialize_async()


async def test_strict_metadata_validation_precedes_sampling_and_cannot_be_bypassed_async() -> None:
    groups = [*_groups(), AttackSeedGroup(seeds=[SeedObjective(value="plain objective"), SeedPrompt(value="prompt")])]
    scenario = _scenario(groups=groups, cap=1)
    with patch("pyrit.scenario.core.dataset_configuration.random.sample") as sample:
        # The error names the failing group and keeps the validator's detail.
        with pytest.raises(ValueError, match="'plain objective'.*ASSERT generation metadata.*Details: "):
            await scenario.get_run_size_estimate_async()
        with pytest.raises(ValueError, match="ASSERT generation metadata"):
            await scenario.initialize_async()
    sample.assert_not_called()


@pytest.mark.parametrize("policy", ["skip", "raise"])
async def test_baseline_and_each_technique_follow_skip_or_raise_async(policy: str) -> None:
    scenario = _scenario(groups=_groups(), policy=policy)
    if policy == "skip":
        await scenario.get_run_size_estimate_async()
        return
    with pytest.raises(ValueError, match="policy is 'raise'") as error:
        await scenario.get_run_size_estimate_async()
    assert all(f"'{name}' skips 1 group" in str(error.value) for name in ("user_prompt", "baseline"))
    assert "'objective_only' skips 2 group" in str(error.value)


async def test_sampling_retains_usable_technique_input_instead_of_only_baseline_cases_async() -> None:
    scenario = _scenario(groups=_groups(), techniques=("objective_only",), cap=1)
    await scenario.initialize_async()
    assert len(scenario._atomic_attacks) == 1
    assert scenario._atomic_attacks[0].technique_name == "objective_only"
    assert len(scenario._atomic_attacks[0].seed_groups) == 1
    assert not scenario._atomic_attacks[0].seed_groups[0].prompts


async def test_launch_assesses_and_samples_off_the_event_loop_async() -> None:
    scenario = _scenario(groups=_groups(), cap=1)
    sample = AssertPolicy._sample_dataset_population
    sampling_threads: list[int] = []

    def record_thread(self: AssertPolicy, **kwargs: Any) -> dict[str, list[AttackSeedGroup]]:
        sampling_threads.append(threading.get_ident())
        return sample(self, **kwargs)

    with patch.object(AssertPolicy, "_sample_dataset_population", record_thread):
        await scenario.initialize_async()
    assert sampling_threads
    assert threading.get_ident() not in sampling_threads


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


async def _store_async(groups: list[AttackSeedGroup], *, dataset_name: str) -> None:
    for group in groups:
        for seed in group.seeds:
            seed.dataset_name = dataset_name
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )


def _named(scenario: AssertPolicy, *sources: DatasetSource) -> AssertPolicy:
    scenario.set_params_from_args(
        args={
            **scenario.params,
            "dataset_config": DatasetAttackConfiguration(sources=list(sources), max_per_dataset="all"),
        }
    )
    return scenario


async def test_estimate_checks_stored_selections_and_leaves_unstored_ones_to_launch_async() -> None:
    await _store_async([AttackSeedGroup(seeds=[SeedObjective(value="Plain objective")])], dataset_name="malformed")
    provider_only = DatasetSource(name="provider_only")
    malformed = DatasetSource(name="malformed")
    # Nothing is stored for the selection yet, so the launch checks it once it loads.
    await _named(_scenario(groups=_groups()), provider_only).get_run_size_estimate_async()
    await _named(_scenario(groups=_groups()), malformed, provider_only).get_run_size_estimate_async()
    with pytest.raises(ValueError, match="Dataset 'malformed'.*ASSERT generation metadata"):
        await _named(_scenario(groups=_groups()), malformed).get_run_size_estimate_async()


async def test_baseline_only_run_without_usable_inputs_says_so_async() -> None:
    # The first group lacks the user prompt the prompt-case baseline needs.
    scenario = _scenario(groups=_groups()[:1])
    expected = "No selected technique or baseline has usable inputs.*'baseline' skips 1 group"
    # A scenario whose technique hook resolves no techniques runs only its baseline.
    with patch.object(AssertPolicy, "_resolve_scenario_techniques", return_value=[]):
        with pytest.raises(ValueError, match=expected):
            await scenario.get_run_size_estimate_async()
        with pytest.raises(ValueError, match=expected):
            await scenario.initialize_async()


async def test_estimate_leaves_unregistered_techniques_to_initialization_async() -> None:
    scenario = _scenario(groups=_groups())
    with patch.object(
        AssertPolicy, "_get_dataset_technique_factories", side_effect=TechniqueResolutionError("custom technique")
    ):
        await scenario.get_run_size_estimate_async()


async def test_estimate_leaves_required_ingredient_assembly_to_initialization_async() -> None:
    class _AssembledPolicy(AssertPolicy):
        def get_required_data(self) -> ScenarioRequiredData:
            return ScenarioRequiredData(sources=[DatasetSource(name="ingredient")])

    scenario = _AssembledPolicy()
    scenario.set_params_from_args(args={**_scenario(groups=_groups()).params})
    with patch.object(
        DatasetAttackConfiguration, "get_attack_groups_by_dataset_async", side_effect=AssertionError("Read datasets")
    ):
        await scenario.get_run_size_estimate_async()


def _refresh_scenario(
    *, stored: list[AttackSeedGroup], refreshed: list[AttackSeedGroup], techniques: tuple[str, ...]
) -> tuple[AssertPolicy, MagicMock]:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "policy_refresh"
    for group in refreshed:
        for seed in group.seeds:
            seed.dataset_name = "policy_refresh"
    provider.fetch_dataset_async = AsyncMock(
        return_value=SeedDataset(
            dataset_name="policy_refresh", seeds=[seed for group in refreshed for seed in group.seeds]
        )
    )
    scenario = _scenario(groups=stored, techniques=techniques, baseline=False)
    scenario.set_params_from_args(
        args={
            **scenario.params,
            "dataset_config": DatasetAttackConfiguration(
                sources=[DatasetSource(name="policy_refresh", fetch=DatasetFetchPolicy.ALWAYS, provider=provider)],
                max_per_dataset="all",
            ),
        }
    )
    return scenario, provider


async def test_valid_refreshed_policy_replaces_stored_data_async() -> None:
    stored = _groups()[1:2]
    await _store_async(stored, dataset_name="policy_refresh")
    refreshed = list(
        make_assert_result(config=make_assert_config(group_count=2, include_adversarial_prompt=False)).seed_groups
    )
    scenario, provider = _refresh_scenario(stored=stored, refreshed=refreshed, techniques=("user_prompt",))
    await scenario.initialize_async()
    provider.fetch_dataset_async.assert_awaited_once()
    saved = await CentralMemory.get_memory_instance().get_seeds_async(dataset_name="policy_refresh")
    assert {seed.value for seed in saved} == {seed.value for group in refreshed for seed in group.seeds}
    assert _units_by_technique(scenario) == {"user_prompt": 2}


async def test_refreshed_policy_no_technique_can_use_is_rejected_before_replacing_stored_data_async() -> None:
    stored = _groups()[1:2]
    await _store_async(stored, dataset_name="policy_refresh")
    # A valid ASSERT conversation case, but the only selected technique needs a stored user prompt.
    refreshed = list(
        make_assert_result(
            config=make_assert_config(group_count=1, include_user_prompt=False, include_adversarial_prompt=True)
        ).seed_groups
    )
    scenario, provider = _refresh_scenario(stored=stored, refreshed=refreshed, techniques=("user_prompt",))
    with pytest.raises(ValueError, match="No usable selected techniques.*'user_prompt' skips 1 group"):
        await scenario.initialize_async()
    provider.fetch_dataset_async.assert_awaited_once()
    saved = await CentralMemory.get_memory_instance().get_seeds_async(dataset_name="policy_refresh")
    assert {seed.value for seed in saved} == {seed.value for group in stored for seed in group.seeds}


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


def _profile(**facts: Any) -> SeedDatasetGroupProfile:
    values: dict[str, Any] = {
        "dataset_name": "imported",
        "group_count": 3,
        "single_objective": True,
        "single_seed": False,
        "has_user_prompt": True,
        "has_non_user_prompt": False,
        "has_simulated_conversation": False,
        "metadata_keys": ("policy",),
    }
    return SeedDatasetGroupProfile(**{**values, **facts})


_POLICY = SeedMetadataRequirement(name="policy", key="policy", description="policy metadata", validator=dict)


@pytest.mark.parametrize(
    ("requirements", "facts", "reason"),
    [
        (SeedGroupRequirements(metadata=(_POLICY,)), {}, None),
        (SeedGroupRequirements(metadata=(_POLICY,)), {"single_objective": False}, "exactly one objective"),
        (SeedGroupRequirements(metadata=(_POLICY,)), {"metadata_keys": ()}, "Requires valid policy metadata"),
        # Only a metadata contract reads a group's single objective; other hints don't.
        (SeedGroupRequirements(required_user_prompt=True), {"single_objective": False}, None),
        (SeedGroupRequirements(required_user_prompt=True), {"has_user_prompt": False}, "stored user prompt"),
        (SeedGroupRequirements(objective_only=True), {}, "objective-only inputs"),
        (SeedGroupRequirements(objective_only=True, try_adapt=True), {}, None),
        (SeedGroupRequirements(objective_only=True), {"single_seed": True}, None),
        (SeedGroupRequirements(user_prompts_only=True), {"has_non_user_prompt": True}, "static user prompts"),
        (SeedGroupRequirements(user_prompts_only=True), {"has_simulated_conversation": True}, "static user prompts"),
        (SeedGroupRequirements(user_prompts_only=True), {}, None),
    ],
)
def test_candidate_profiles_rule_out_only_datasets_that_cannot_comply(
    *, requirements: SeedGroupRequirements, facts: dict[str, Any], reason: str | None
) -> None:
    reasons = assess_candidate_profiles(profiles=[_profile(**facts)], requirements=requirements)
    if reason is None:
        assert reasons == []
    else:
        assert len(reasons) == 1
        assert reason in reasons[0]
        assert reasons[0].endswith("(3 group(s))")


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
        metadata_keys=("assert_generation",),
    )
    assert assess_candidate_profiles(profiles=[profile], requirements=requirements) == []
    parser.assert_not_called()


async def test_key_presence_is_not_validated_policy_metadata_async() -> None:
    groups = _groups()
    groups[1].objective.metadata = {"assert_generation": {"taxonomy": {}}}
    with pytest.raises(ValueError, match="ASSERT generation metadata"):
        await _scenario(groups=groups).get_run_size_estimate_async()


async def _store_listing_async(name: str, *, include_adversarial_prompt: bool = True) -> None:
    groups = make_assert_result(
        config=make_assert_config(group_count=2, include_adversarial_prompt=include_adversarial_prompt)
    ).seed_groups
    seeds = [seed for group in groups for seed in group.seeds]
    for seed in seeds:
        seed.dataset_name = name
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(seeds=seeds, added_by="test")


def _listing_scenario(scenario_class: type[AssertPolicy] = AssertPolicy, **args: Any) -> AssertPolicy:
    scenario = scenario_class()
    scenario.set_params_from_args(args=args)
    return scenario


class _RestrictedPolicy(AssertPolicy):
    DATASET_OPTIONS = ScenarioDatasetOptions(mode=DatasetSelectionMode.SUBSET, allowed_names=["imported"])


async def test_compatible_listing_validates_stored_contents_of_both_case_forms_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _store_listing_async("conversation_cases")
    await _store_listing_async("prompt_cases", include_adversarial_prompt=False)
    invalid_case = (
        make_assert_result(config=make_assert_config(group_count=1, include_adversarial_prompt=False))
        .seed_groups[0]
        .objective
    )
    invalid_case.dataset_name = "invalid_case"
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[
            invalid_case,
            SeedObjective(value="Generic objective", dataset_name="ordinary"),
            SeedObjective(value="Malformed policy", dataset_name="malformed", metadata={"assert_generation": {}}),
        ],
        added_by="test",
    )
    stored = ["conversation_cases", "prompt_cases", "invalid_case", "ordinary", "malformed"]
    hydrate = DatasetAttackConfiguration.get_attack_groups_by_dataset_async
    hydrated: list[str] = []

    async def record_async(self: DatasetAttackConfiguration, **kwargs: Any) -> dict[str, list[AttackSeedGroup]]:
        hydrated.extend(self.dataset_names)
        return await hydrate(self, **kwargs)

    with (
        patch.object(
            AssertPolicy,
            "_get_dataset_technique_factories",
            side_effect=AssertionError("Dataset eligibility must not depend on delivery techniques."),
        ),
        patch.object(DatasetAttackConfiguration, "get_attack_groups_by_dataset_async", record_async),
    ):
        names = await _listing_scenario().get_compatible_dataset_names_async(
            dataset_names=["unloaded", *stored], loaded_dataset_names=stored
        )
    assert names == ["conversation_cases", "prompt_cases"]
    # A stored dataset that carries the metadata key but fails validation says why it is left out.
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("'invalid_case' is not compatible" in message for message in warnings)
    assert any("'malformed' is not compatible" in message for message in warnings)
    # Stored facts rule out the dataset without ASSERT metadata; unloaded datasets are never read.
    assert sorted(hydrated) == ["conversation_cases", "invalid_case", "malformed", "prompt_cases"]


async def test_compatible_listing_leaves_out_only_a_dataset_whose_groups_cannot_be_built_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _store_listing_async("conversation_cases")
    objective = (
        make_assert_result(config=make_assert_config(group_count=1, include_adversarial_prompt=False))
        .seed_groups[0]
        .objective
    )
    group_id = uuid4()
    # Valid ASSERT metadata, but a system and a user prompt share one sequence, so no AttackSeedGroup can be built.
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[
            SeedObjective(
                value=objective.value, metadata=objective.metadata, dataset_name="broken", prompt_group_id=group_id
            ),
            SeedPrompt(value="System", role="system", sequence=1, dataset_name="broken", prompt_group_id=group_id),
            SeedPrompt(value="User", role="user", sequence=1, dataset_name="broken", prompt_group_id=group_id),
        ],
        added_by="test",
    )
    names = ["conversation_cases", "broken"]
    assert await _listing_scenario().get_compatible_dataset_names_async(
        dataset_names=names, loaded_dataset_names=names
    ) == ["conversation_cases"]
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("'broken' is not compatible" in message for message in warnings)
    # Launch and estimate reads name the dataset too.
    with pytest.raises(DatasetConstraintError, match=r"(?s)Dataset 'broken'.*Inconsistent roles"):
        await DatasetAttackConfiguration(sources=[DatasetSource(name="broken")]).get_attack_groups_by_dataset_async()


async def test_compatible_listing_keeps_subset_restrictions_and_observes_changed_data_async() -> None:
    await _store_listing_async("imported")
    await _store_listing_async("other")
    names = ["imported", "other"]
    listing = _listing_scenario(_RestrictedPolicy)
    assert await listing.get_compatible_dataset_names_async(dataset_names=names, loaded_dataset_names=names) == [
        "imported"
    ]
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Changed after the picker loaded", dataset_name="imported")], added_by="test"
    )
    assert await listing.get_compatible_dataset_names_async(dataset_names=names, loaded_dataset_names=names) == []


async def test_compatible_listing_validates_filtered_contents_that_raw_hints_would_reject_async() -> None:
    await _store_listing_async("imported")
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Excluded ordinary objective", dataset_name="imported", harm_categories=["other"])],
        added_by="test",
    )
    names = ["imported"]
    unfiltered = _listing_scenario()
    assert await unfiltered.get_compatible_dataset_names_async(dataset_names=names, loaded_dataset_names=names) == []
    filtered = _listing_scenario(
        dataset_config=DatasetAttackConfiguration(
            sources=[DatasetSource(name="imported")], filters={"harm_categories": ["public_policy"]}
        )
    )
    assert await filtered.get_compatible_dataset_names_async(dataset_names=names, loaded_dataset_names=names) == names


class _CheckedScam(Scam):
    """A base-path scenario that declares requirements, as the next scenario with requirements would."""

    checked: ClassVar[list[int]] = []

    def get_dataset_requirements(self) -> SeedGroupRequirements:
        return SeedGroupRequirements(
            metadata=(
                SeedMetadataRequirement(
                    name="assert_generation",
                    key=AssertGroupMetadata.METADATA_KEY,
                    description="ASSERT generation metadata",
                    validator=AssertGroupMetadata.from_metadata,
                ),
            )
        )

    def _validate_dataset_assessment_population(self, population: dict[str, list[AttackSeedGroup]]) -> None:
        self.checked.append(sum(len(groups) for groups in population.values()))
        if any(group.objective.value.startswith("Rejected") for groups in population.values() for group in groups):
            raise DatasetConstraintError("The scenario rejects this selection.")


@pytest.mark.parametrize("rejected", [False, True])
async def test_base_launch_validates_assesses_and_samples_one_population_async(rejected: bool) -> None:
    # Scam's techniques take objective-only groups; the ASSERT metadata satisfies the declared requirement.
    groups = [
        AttackSeedGroup(seeds=[group.objective])
        for group in make_assert_result(
            config=make_assert_config(group_count=3, include_adversarial_prompt=False)
        ).seed_groups
    ]
    if rejected:
        groups[2].objective.value = "Rejected policy case."
    _CheckedScam.checked = []
    # Stand-ins for Scam's default techniques, so the base dataset checks can resolve them.
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [
            AttackTechniqueFactory(name=name, attack_class=PromptSendingAttack)
            for name in ("context_compliance", "role_play_persuasion_written")
        ]
    )
    with (
        patch.object(Scenario, "_get_default_objective_scorer", return_value=SubStringScorer(substring="test")),
        override_default_adversarial_target(MockPromptTarget()),
    ):
        scenario = _CheckedScam()
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "dataset_config": DatasetAttackConfiguration(seed_groups=groups, max_total=2),
            }
        )
        # Declaring requirements is what makes the GUI offer only compatible stored datasets.
        assert scenario.get_dataset_options().compatible_only is True
        assert Scam().get_dataset_options().compatible_only is False
        if rejected:
            with pytest.raises(DatasetConstraintError, match="rejects this selection"):
                await scenario.initialize_async()
        else:
            await scenario.initialize_async()
            sampled = {id(group) for attack in scenario._atomic_attacks for group in attack.seed_groups}
            assert len(sampled) == 2
    # The hook sees the whole selection once, before sampling.
    assert _CheckedScam.checked == [3]


async def test_estimate_reads_no_datasets_without_a_selection_async() -> None:
    scenario = _CheckedScam()
    scenario.set_params_from_args(args={"dataset_config": DatasetAttackConfiguration(sources=[])})
    with patch.object(
        DatasetAttackConfiguration, "get_attack_groups_by_dataset_async", side_effect=AssertionError("Read datasets")
    ):
        await scenario.get_run_size_estimate_async()
