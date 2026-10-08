# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real factory projections, REST shapes, and revision-aware scenario caches."""

import ast
import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.mappers.technique_mappers import technique_to_instance
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.backend.services.service_lifecycle import close_services_async
from pyrit.backend.services.technique_service import TechniqueService, get_technique_service
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import (
    AttackSeedGroup,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, Registry, ScenarioRegistry
from pyrit.scenario import Scenario
from pyrit.scenario.core import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration
from pyrit.scenario.scenarios import airt
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.techniques import build_technique_factories
from unit.mocks import MockPromptTarget


@pytest.fixture
def registry(patch_central_database: Any) -> Iterator[AttackTechniqueRegistry]:
    get_technique_service.cache_clear()
    with patch.dict(Registry._singletons, {}, clear=True):
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(build_technique_factories())
        yield registry
    get_technique_service.cache_clear()


async def test_catalog_real_factories_and_alias_metadata_async(registry: AttackTechniqueRegistry) -> None:
    service = TechniqueService()
    response = await service.list_async()
    assert {entry.name for entry in response.items} == set(registry.instances.get_names())
    item = await service.get_async(response.items[0].name)
    assert item == response.items[0]
    assert item is not None
    assert set(item.model_dump()) == {
        "name",
        "description",
        "attack_type",
        "tags",
        "uses_adversarial",
        "uses_default_adversarial_target",
        "creation_statement",
    }
    assert await service.get_async("missing") is None
    AttackRegistry.get_registry_singleton().register_class(PromptSendingAttack, name="alias")
    metadata = await service.types_async()
    assert "alias" in {entry.attack_type for entry in metadata.items}
    assert set(metadata.model_dump()) == {"items"}
    assert all(
        parameter.name
        not in {"objective_target", "attack_adversarial_config", "attack_scoring_config", "attack_converter_config"}
        for entry in metadata.items
        for parameter in entry.parameters
    )
    assert metadata.model_dump_json()
    created = await service.create_async(CreateTechniqueRequest(name="from_alias", type="alias"))
    assert created.attack_type == "PromptSendingAttack"


@pytest.mark.parametrize("name", ["tap", "crescendo_simulated", "role_play_video_game"])
def test_factory_creation_statement_uses_supplied_inputs_without_creating_attacks(
    *, registry: AttackTechniqueRegistry, name: str
) -> None:
    factory = registry.get_factories_or_raise()[name]
    identifier = factory.get_identifier()
    with (
        patch.object(factory, "create", side_effect=AssertionError("Listing must not construct an attack")),
        patch.object(factory, "get_identifier", side_effect=AssertionError("Listing does not need identity")),
    ):
        item = technique_to_instance(name=name, factory=factory)

    assert factory.get_identifier() == identifier
    assert item.creation_statement.startswith(factory.get_creation_calls()[0][0] + "(")
    call = ast.parse(item.creation_statement, mode="eval").body
    assert isinstance(call, ast.Call)
    arguments = {keyword.arg for keyword in call.keywords}
    assert {"name", "description", "technique_tags"} <= arguments
    assert not {"seed_technique", "uses_adversarial", "scorer_override_policy"} & arguments
    if name == "tap":
        assert isinstance(call.func, ast.Name)
        assert call.func.id == "AttackTechniqueFactory"
        assert arguments == {"name", "attack_class", "description", "technique_tags"}
    else:
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == "with_simulated_conversation"
    if name == "crescendo_simulated":
        assert arguments == {"name", "description", "technique_tags"}
    if name == "role_play_video_game":
        assert factory.seed_technique is not None
        seed = factory.seed_technique.seeds[0]
        assert isinstance(seed, SeedSimulatedConversation)
        assert isinstance(seed.adversarial_chat_system_prompt, SeedPrompt)
        assert isinstance(seed.next_message_system_prompt, SeedPrompt)
        assert item.creation_statement.count(repr(seed.adversarial_chat_system_prompt.value)) == 1
        assert item.creation_statement.count(repr(seed.next_message_system_prompt.value)) == 1


def test_factory_creation_statement_does_not_expose_target_credentials(
    registry: AttackTechniqueRegistry,
) -> None:
    target = OpenAIChatTarget(endpoint="https://local.invalid/v1", model_name="local", api_key="private-test-key")
    factory = registry.create_factory(name="private_target", attack_type="RedTeamingAttack", adversarial_chat=target)
    item = technique_to_instance(name=factory.name, factory=factory)
    assert "adversarial_chat=OpenAIChatTarget(...)" in item.creation_statement
    assert "private-test-key" not in item.model_dump_json()


def test_factory_creation_display_does_not_call_unknown_object_repr(registry: AttackTechniqueRegistry) -> None:
    class PrivateSettings:
        def __repr__(self) -> str:
            raise AssertionError("Display must not inspect private object contents")

    factory = AttackTechniqueFactory(
        name="private",
        attack_class=PromptSendingAttack,
        attack_kwargs={"max_attempts_on_failure": PrivateSettings()},
    )
    item = technique_to_instance(name=factory.name, factory=factory)
    assert "'max_attempts_on_failure': PrivateSettings(...)" in item.creation_statement


def test_rest_create_detail_types_and_errors(
    registry: AttackTechniqueRegistry, compatibility_headers: dict[str, str]
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    assert client.get("/api/techniques/types").status_code == 200
    created = client.post(
        "/api/techniques",
        json={
            "name": "rest_example",
            "description": "A basic technique",
            "tags": ["custom"],
            "type": "PromptSendingAttack",
            "params": {"max_attempts_on_failure": 0},
        },
    )
    assert created.status_code == 201, created.text
    assert "'max_attempts_on_failure': 0" in created.json()["creation_statement"]
    assert client.get("/api/techniques/rest_example").json() == created.json()
    assert client.get("/api/techniques/missing").status_code == 404
    assert (
        client.post("/api/techniques", json={"name": "rest_example", "type": "PromptSendingAttack"}).status_code == 400
    )
    assert client.post("/api/techniques", json={"name": "unknown", "type": "missing"}).status_code == 400
    assert client.post("/api/techniques", json={"name": "bad-name", "type": "PromptSendingAttack"}).status_code == 422
    assert "unknown" not in registry.instances.get_names()


@pytest.mark.parametrize("name", ["first_letter", "image", "prompt_sending"])
def test_rest_accepts_scenario_local_names(
    *, registry: AttackTechniqueRegistry, compatibility_headers: dict[str, str], name: str
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    created = client.post(
        "/api/techniques",
        json={"name": name, "type": "PromptSendingAttack", "description": "Runtime configuration"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "Runtime configuration"
    assert registry.instances.get(name) is not None
    assert name in {technique.value for technique in airt.RapidResponseTechnique.get_all_techniques()}


async def test_warm_catalog_estimates_and_summaries_refresh_without_changing_snapshot_async(
    registry: AttackTechniqueRegistry,
) -> None:
    scorer = SubStringScorer(substring="yes")
    scenario_registry = ScenarioRegistry.get_registry_singleton()
    with (
        patch.object(Scenario, "_get_default_objective_scorer", return_value=scorer),
        patch.object(scenario_registry, "_discover"),
    ):
        scenario_registry.register_class(RapidResponse, name="airt.rapid_response")
        old = RapidResponse(objective_scorer=scorer)
        old_class = airt.RapidResponseTechnique
        metadata = scenario_registry.get_registered_class_metadata("airt.rapid_response")
        assert metadata is not None
        service = ScenarioService()
        run_service = ScenarioRunService()
        try:
            before_estimate = await service._get_default_run_size_estimate_async(metadata=metadata)
            before_summaries = run_service._get_scenario_technique_summaries(scenario_name="airt.rapid_response")
            request = ScenarioRunSizeEstimateRequest(techniques=["all"], max_dataset_size=1, include_baseline=False)
            before_key = service._build_configured_estimate_key(
                scenario_name="airt.rapid_response", scenario_class=RapidResponse, request=request
            )
            before_configured = await service.estimate_scenario_run_size_async(
                scenario_name="airt.rapid_response", request=request
            )
            await TechniqueService().create_async(
                CreateTechniqueRequest(name="runtime_new", type="PromptSendingAttack", tags=["user_group"])
            )
            after_metadata = scenario_registry.get_registered_class_metadata("airt.rapid_response")
            assert after_metadata is not None
            assert "runtime_new" in after_metadata.all_techniques
            assert "runtime_new" not in metadata.all_techniques
            assert old._technique_class is old_class
            assert "runtime_new" not in {technique.value for technique in old._technique_class.get_all_techniques()}
            assert "runtime_new" in {technique.value for technique in airt.RapidResponseTechnique.get_all_techniques()}
            assert "runtime_new" in run_service._get_scenario_technique_summaries(scenario_name="airt.rapid_response")
            assert "runtime_new" not in before_summaries
            await service._get_default_run_size_estimate_async(metadata=after_metadata)
            assert len(service._estimate_cache) == 2
            assert before_estimate is not None
            assert (
                service._build_configured_estimate_key(
                    scenario_name="airt.rapid_response", scenario_class=RapidResponse, request=request
                )
                != before_key
            )
            after_configured = await service.estimate_scenario_run_size_async(
                scenario_name="airt.rapid_response", request=request
            )
            assert before_configured is not None and after_configured is not None
            assert after_configured.estimated_attack_count > before_configured.estimated_attack_count
            target = MockPromptTarget()
            current = RapidResponse(objective_scorer=scorer)
            current.set_params_from_args(
                args={
                    "objective_target": target,
                    "scenario_techniques": [current._technique_class("runtime_new")],
                    "include_baseline": False,
                }
            )
            with patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value={"local": [AttackSeedGroup(seeds=[SeedObjective(value="local objective")])]},
            ):
                await current.initialize_async()
            assert len(current._atomic_attacks) == 1
            assert isinstance(current._atomic_attacks[0].attack_technique.attack, PromptSendingAttack)
            assert target.prompt_sent == []
        finally:
            await service.close_async()
            await run_service.close_async()


def test_direct_registration_and_registry_replacement_refresh_exports(registry: AttackTechniqueRegistry) -> None:
    before = airt.RapidResponseTechnique
    registry.instances.register(
        AttackTechniqueFactory(name="direct_new", attack_class=PromptSendingAttack), name="direct_new"
    )
    after = airt.RapidResponseTechnique
    assert after is not before
    assert "direct_new" in {item.value for item in after.get_all_techniques()}
    AttackTechniqueRegistry.reset_registry_singleton()
    replacement = AttackTechniqueRegistry.get_registry_singleton()
    replacement.register_from_factories(build_technique_factories())
    current = airt.RapidResponseTechnique
    assert current is not after
    assert "direct_new" not in {item.value for item in current.get_all_techniques()}


def test_filtered_and_fixed_catalogs_keep_existing_pool_rules(registry: AttackTechniqueRegistry) -> None:
    from pyrit.scenario.scenarios.airt.jailbreak import _build_jailbreak_technique
    from pyrit.scenario.scenarios.benchmark.adversarial import _build_benchmark_technique

    fixed = _build_jailbreak_technique()
    filtered = _build_benchmark_technique()
    registry.instances.register_runtime(registry.create_factory(name="plain_user", attack_type="PromptSendingAttack"))
    registry.instances.register_runtime(
        registry.create_factory(name="adversarial_user", attack_type="RedTeamingAttack")
    )
    current = _build_benchmark_technique()
    names = {item.value for item in current.get_all_techniques()}
    assert current is not filtered
    assert "adversarial_user" in names and "plain_user" not in names
    assert _build_jailbreak_technique() is fixed
    assert "adversarial_user" not in {item.value for item in fixed.get_all_techniques()}


async def test_mutation_does_not_cancel_active_estimate_async(registry: AttackTechniqueRegistry) -> None:
    service = ScenarioService()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def pending_async(**kwargs: Any) -> Any:
        started.set()
        await finish.wait()
        return ScenarioRunSizeEstimate.unavailable()

    with patch.object(
        service, "_run_configured_estimate_with_capacity_async", new=AsyncMock(side_effect=pending_async)
    ):
        task = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name="airt.rapid_response",
                request=ScenarioRunSizeEstimateRequest(techniques=["all"]),
            )
        )
        await started.wait()
        registry.instances.register(
            AttackTechniqueFactory(name="while_running", attack_class=PromptSendingAttack), name="while_running"
        )
        assert not task.cancelled()
        assert all(not work.cancelled() for work in service._configured_estimate_tasks.values())
        finish.set()
        assert await task == ScenarioRunSizeEstimate.unavailable()
    await service.close_async()


async def test_lifecycle_clears_technique_binding_async(registry: AttackTechniqueRegistry) -> None:
    service = get_technique_service()
    await close_services_async()
    AttackTechniqueRegistry.reset_registry_singleton()
    assert get_technique_service() is not service


@pytest.mark.parametrize(
    "params",
    [
        {"max_attempts_on_failure": False},
        {"max_attempts_on_failure": "2"},
        {"max_attempts_on_failure": None},
        {"attack_converter_config": {"type": "AttackConverterConfig"}},
        {"prepended_conversation_config": {}},
    ],
)
async def test_rest_parameter_checks_do_not_change_registry_async(
    registry: AttackTechniqueRegistry, params: dict[str, Any]
) -> None:
    before = registry.catalog_revision
    with pytest.raises(ValueError):
        await TechniqueService().create_async(
            CreateTechniqueRequest(name="invalid", type="PromptSendingAttack", params=params)
        )
    assert registry.catalog_revision == before
    assert registry.instances.get("invalid") is None


@pytest.mark.parametrize(
    "extra",
    [
        {"name": "all"},
        {"name": "types"},
        {"name": "DEFAULT"},
        {"name": "bad-name"},
        {"tags": ["all"]},
        {"tags": ["alpha", "Alpha"]},
        {"tags": ["bad-tag"]},
        {"seed_technique": {"seeds": []}},
        {"factory_options": {}},
        {"attack_args": {}},
        {"adversarial_system_prompt": {"type": "SeedPrompt", "parameters": {"value": "x"}}},
        {"params": {"number": float("inf")}},
        {"request_converters": [False]},
    ],
)
def test_request_rejects_invalid_selectors_and_removed_inputs(
    registry: AttackTechniqueRegistry, extra: dict[str, Any]
) -> None:
    from pydantic import ValidationError

    before = registry.catalog_revision
    with pytest.raises(ValidationError):
        CreateTechniqueRequest.model_validate({"name": "new", "type": "PromptSendingAttack", **extra})
    assert registry.catalog_revision == before
