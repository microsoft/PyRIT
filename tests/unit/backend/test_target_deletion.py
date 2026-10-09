# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Manual-origin and live-usage guarantees for target deletion."""

import asyncio
import threading
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.attacks import MessagePieceRequest
from pyrit.backend.models.message_sends import MessageSendRequest
from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.services.message_send_service import MessageSendService, _ValidatedMessage
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.backend.services.scorer_service import ScorerService
from pyrit.backend.services.target_service import (
    TargetDeletionConflictError,
    TargetDeletionProtectedError,
    TargetService,
    get_target_service,
)
from pyrit.converter import Converter
from pyrit.models import ComponentIdentifier
from pyrit.models.catalog.scenario import ScenarioRunSizeEstimate, ScenarioRunSizeEstimateRequest
from pyrit.models.catalog.target import TargetInstance
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.score import Scorer
from pyrit.setup.initializers import TargetInitializer


@pytest.fixture
def service(patch_central_database: MagicMock) -> Iterator[TargetService]:
    registries = (TargetRegistry, ConverterRegistry, ScorerRegistry, AttackTechniqueRegistry)
    get_target_service.cache_clear()
    for registry in registries:
        registry.reset_registry_singleton()
    yield get_target_service()
    get_target_service.cache_clear()
    for registry in registries:
        registry.reset_registry_singleton()


async def _create_async(*, service: TargetService, name: str = "manual") -> TargetInstance:
    return await service.create_target_async(
        request=CreateTargetRequest(
            name=name,
            type="OpenAIChatTarget",
            params={"endpoint": f"https://{name}.example/v1", "model_name": "test-model", "api_key": "test-key"},
        )
    )


async def test_manual_origin_is_returned_by_create_get_and_list_async(service: TargetService) -> None:
    created = await _create_async(service=service)
    loaded = await service.get_target_async(target_registry_name="manual")
    assert created.can_delete
    assert created.deletion_blocked_reason is None
    assert loaded is not None and loaded.can_delete
    assert loaded.deletion_blocked_reason is None
    assert (await service.list_targets_async()).items[0].can_delete
    assert await service.delete_target_async(target_registry_name="manual")
    assert await service.get_target_async(target_registry_name="manual") is None
    assert not await service.delete_target_async(target_registry_name="manual")


async def test_duplicate_name_preserves_original_target_async(service: TargetService) -> None:
    created = await _create_async(service=service, name="team-image-model")
    with pytest.raises(ValueError, match="already"):
        await _create_async(service=service, name="team-image-model")
    loaded = await service.get_target_async(target_registry_name="team-image-model")
    assert loaded == created
    assert loaded.can_delete


@pytest.mark.parametrize("name", ["", "   ", "bad/name", "bad name", "-bad", "x" * 65])
def test_invalid_target_names_are_rejected_by_api(
    *, service: TargetService, compatibility_headers: dict[str, str], name: str
) -> None:
    with patch("pyrit.backend.routes.targets.get_target_service", return_value=service):
        response = TestClient(app, headers=compatibility_headers).post(
            "/api/targets", json={"name": name, "type": "OpenAIChatTarget", "params": {}}
        )
    assert response.status_code == 422
    assert len(service._registry.instances) == 0


@pytest.mark.parametrize("name", ["initializer_target", "compat_875589e4c92f4bb4962d4e4ed2100f7a"])
@pytest.mark.parametrize("metadata", [{}, {"created_by_target_api": "true"}, {"created_by_target_api": False}])
async def test_unknown_or_initializer_origin_is_protected_async(
    *, service: TargetService, name: str, metadata: dict[str, object]
) -> None:
    await _create_async(service=service)
    target = service.get_target_object(target_registry_name="manual")
    service._registry.instances.register(target, name=name, metadata=metadata)
    snapshot = await service.get_target_async(target_registry_name=name)
    assert snapshot is not None and not snapshot.can_delete
    with pytest.raises(TargetDeletionProtectedError, match="Only manually"):
        await service.delete_target_async(target_registry_name=name)
    assert service.get_target_object(target_registry_name=name) is target


async def test_replaced_manual_entry_is_protected_async(service: TargetService) -> None:
    await _create_async(service=service)
    target = service.get_target_object(target_registry_name="manual")
    service._registry.instances.register(target, name="manual", replace=True)
    with pytest.raises(TargetDeletionProtectedError):
        await service.delete_target_async(target_registry_name="manual")


async def test_nested_reservations_block_only_used_target_and_release_on_error_async(service: TargetService) -> None:
    await _create_async(service=service)
    await _create_async(service=service, name="unrelated")
    with service.reserve_targets(["manual", "manual", None]):
        with pytest.raises(RuntimeError, match="operation failed"), service.reserve_targets(["manual"]):
            assert await service.delete_target_async(target_registry_name="unrelated")
            raise RuntimeError("operation failed")
        with pytest.raises(TargetDeletionConflictError, match="active or queued"):
            await service.delete_target_async(target_registry_name="manual")
    assert await service.delete_target_async(target_registry_name="manual")


async def test_round_robin_dependency_blocks_child_but_not_parent_deletion_async(service: TargetService) -> None:
    await _create_async(service=service)
    await _create_async(service=service, name="second")
    round_robin = await service.create_target_async(
        request=CreateTargetRequest(
            name="round-robin", type="RoundRobinTarget", params={"targets": ["manual", "second"]}
        )
    )
    assert round_robin.can_delete
    assert round_robin.deletion_blocked_reason is None
    with pytest.raises(TargetDeletionConflictError, match="round-robin"):
        await service.delete_target_async(target_registry_name="manual")
    assert await service.delete_target_async(target_registry_name="round-robin")
    assert await service.delete_target_async(target_registry_name="manual")


async def test_initializer_targets_have_source_specific_protection_async(service: TargetService) -> None:
    env = {
        "OPENAI_CHAT_ENDPOINT": "https://first.example/v1",
        "OPENAI_CHAT_KEY": "test-key",
        "OPENAI_CHAT_MODEL": "gpt-4o",
        "PLATFORM_OPENAI_CHAT_ENDPOINT": "https://second.example/v1",
        "PLATFORM_OPENAI_CHAT_KEY": "test-key",
        "PLATFORM_OPENAI_CHAT_MODEL": "gpt-4o",
        "ADVERSARIAL_CHAT_ENDPOINT": "https://adversarial.example/v1",
        "ADVERSARIAL_CHAT_KEY": "test-key",
        "ADVERSARIAL_CHAT_MODEL": "gpt-4o",
    }
    with patch.dict("os.environ", env, clear=True):
        await TargetInitializer().initialize_async()

    targets = {target.target_registry_name: target for target in (await service.list_targets_async()).items}
    for name, explanation in (
        ("openai_chat", "generated automatically from your .env configuration"),
        ("platform_openai_chat", "generated automatically from your .env configuration"),
        ("adversarial_chat_primary", "generated automatically from your .env configuration"),
        ("OpenAIChatTarget_gpt-4o_rr", "generated automatically from your configured targets"),
        ("adversarial_chat", "generated automatically from your configured targets"),
    ):
        target = targets[name]
        assert not target.can_delete
        assert target.deletion_blocked_reason is not None
        assert explanation in target.deletion_blocked_reason
        assert ".env" in target.deletion_blocked_reason
        assert ".pyrit_conf" in target.deletion_blocked_reason
        assert "reinitialize" in target.deletion_blocked_reason
        with pytest.raises(TargetDeletionProtectedError, match=explanation):
            await service.delete_target_async(target_registry_name=name)
        assert service.get_target_object(target_registry_name=name) is not None


def test_deletion_is_global_across_clients_and_reload(
    *, service: TargetService, compatibility_headers: dict[str, str]
) -> None:
    first = TestClient(app, headers=compatibility_headers)
    second = TestClient(app, headers=compatibility_headers)
    with patch("pyrit.backend.routes.targets.get_target_service", return_value=service):
        created = first.post(
            "/api/targets",
            json={
                "name": "manual",
                "type": "OpenAIChatTarget",
                "params": {"endpoint": "https://manual.example/v1", "model_name": "test-model", "api_key": "test-key"},
            },
        )
        assert created.status_code == 201
        assert created.json()["can_delete"] is True
        assert second.get("/api/targets/manual").status_code == 200
        assert first.delete("/api/targets/manual").status_code == 204
        assert second.get("/api/targets/manual").status_code == 404
        assert "manual" not in [item["target_registry_name"] for item in second.get("/api/targets").json()["items"]]
        reloaded = TestClient(app, headers=compatibility_headers)
        assert reloaded.get("/api/targets/manual").status_code == 404
    assert service._registry.instances.get("manual") is None


@pytest.mark.parametrize("component_type", ["converter", "scorer"])
async def test_nested_registered_dependencies_block_deletion_async(
    *, service: TargetService, component_type: str
) -> None:
    await _create_async(service=service)
    target = service.get_target_object(target_registry_name="manual")
    identifier = ComponentIdentifier(
        class_name="Composite",
        class_module="test",
        children={
            "nested": [
                ComponentIdentifier(
                    class_name="Nested", class_module="test", children={"target": target.get_identifier()}
                )
            ]
        },
    )
    component = MagicMock(spec=Converter if component_type == "converter" else Scorer)
    component.get_identifier.return_value = identifier
    registry = ConverterRegistry if component_type == "converter" else ScorerRegistry
    registry.get_registry_singleton().instances.register(component, name="dependency")
    with pytest.raises(TargetDeletionConflictError, match=f"{component_type} 'dependency'"):
        await service.delete_target_async(target_registry_name="manual")
    with service.reserve_identifiers([identifier]):
        registry.get_registry_singleton().instances.unregister("dependency")
        with pytest.raises(TargetDeletionConflictError, match="active or queued"):
            await service.delete_target_async(target_registry_name="manual")
    assert await service.delete_target_async(target_registry_name="manual")


async def test_factory_adversarial_target_blocks_deletion_async(service: TargetService) -> None:
    await _create_async(service=service)
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.adversarial_chat = service.get_target_object(target_registry_name="manual")
    AttackTechniqueRegistry.get_registry_singleton().instances.register(factory, name="technique")
    with pytest.raises(TargetDeletionConflictError, match="attack technique 'technique'"):
        await service.delete_target_async(target_registry_name="manual")


async def test_scenario_converter_and_scorer_default_remain_reserved_async(service: TargetService) -> None:
    await _create_async(service=service)
    await _create_async(service=service, name="objective_scorer_chat")
    converter = MagicMock(spec=Converter)
    converter.get_identifier.return_value = ComponentIdentifier(
        class_name="Converter",
        class_module="test",
        children={"target": service.get_target_object(target_registry_name="manual").get_identifier()},
    )
    instances = ConverterRegistry.get_registry_singleton().instances
    instances.register(converter, name="custom")
    with ScenarioConfigurationResolver.reserve_targets(
        target_name="other", adversarial_target_name="adversary", techniques=["single_turn:converter.custom"]
    ):
        instances.unregister("custom")
        for name in ("manual", "objective_scorer_chat"):
            with pytest.raises(TargetDeletionConflictError):
                await service.delete_target_async(target_registry_name=name)
    assert await service.delete_target_async(target_registry_name="manual")
    assert await service.delete_target_async(target_registry_name="objective_scorer_chat")


async def test_threaded_scorer_creation_reserves_its_target_async(service: TargetService) -> None:
    await _create_async(service=service)
    scorers = ScorerService()
    original = scorers._registry.create_named_instance
    started = threading.Event()
    release = threading.Event()

    def create(**kwargs: object) -> Scorer:
        started.set()
        assert release.wait(timeout=5)
        return original(**kwargs)

    with patch.object(scorers._registry, "create_named_instance", side_effect=create):
        task = asyncio.create_task(
            scorers.create_scorer_async(
                request=CreateScorerRequest(
                    name="dependent", type="SelfAskRefusalScorer", params={"chat_target": "manual"}
                )
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 5)
            with pytest.raises(TargetDeletionConflictError, match="active or queued"):
                await service.delete_target_async(target_registry_name="manual")
        finally:
            release.set()
            await task
    with pytest.raises(TargetDeletionConflictError, match="scorer 'dependent'"):
        await service.delete_target_async(target_registry_name="manual")


async def test_entry_replacement_during_delete_does_not_remove_replacement_async(service: TargetService) -> None:
    await _create_async(service=service)
    instances = service._registry.instances
    original = instances.unregister
    target = instances.get("manual")

    def replace_before_unregister(name: str, **kwargs: object) -> object:
        instances.register(target, name=name, replace=True)
        return original(name, **kwargs)

    with patch.object(instances, "unregister", side_effect=replace_before_unregister):
        with pytest.raises(TargetDeletionConflictError, match="registration changed"):
            await service.delete_target_async(target_registry_name="manual")
    assert instances.get("manual") is target


async def test_background_send_keeps_target_until_worker_finishes_async(service: TargetService) -> None:
    await _create_async(service=service)
    await _create_async(service=service, name="unrelated")
    sender = MessageSendService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute_async(**kwargs: object) -> None:
        started.set()
        await release.wait()

    request = MessageSendRequest(
        submission_id="submission",
        target_registry_name="manual",
        target_conversation_id="conversation",
        pieces=[MessagePieceRequest(original_value="hello")],
    )
    with (
        patch.object(
            sender,
            "_validate_message_async",
            return_value=_ValidatedMessage(
                target=service.get_target_object(target_registry_name="manual"),
                request_configurations=[],
                response_configurations=[],
                applied_identifiers={},
            ),
        ),
        patch.object(sender, "_execute_validated_message_async", side_effect=execute_async),
    ):
        try:
            await sender.submit_async(attack_result_id="attack", request=request)
            await asyncio.wait_for(started.wait(), timeout=5)
            with pytest.raises(TargetDeletionConflictError, match="active or queued"):
                await service.delete_target_async(target_registry_name="manual")
            assert await service.delete_target_async(target_registry_name="unrelated")
        finally:
            release.set()
            await sender.shutdown_async()
    assert await service.delete_target_async(target_registry_name="manual")


async def test_estimate_retains_reservation_after_request_cancellation_async(service: TargetService) -> None:
    await _create_async(service=service)
    await _create_async(service=service, name="adversary")
    await _create_async(service=service, name="unrelated")
    estimator = ScenarioService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def estimate_async(**kwargs: object) -> ScenarioRunSizeEstimate:
        started.set()
        await release.wait()
        return ScenarioRunSizeEstimate(total_attack_count=1)

    with (
        patch.object(estimator._registry, "get_class", return_value=type),
        patch.object(estimator, "_run_configured_estimate_with_capacity_async", side_effect=estimate_async),
    ):
        waiter = asyncio.create_task(
            estimator.estimate_scenario_run_size_async(
                scenario_name="scenario",
                request=ScenarioRunSizeEstimateRequest(target_name="manual", adversarial_target_name="adversary"),
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            for name in ("manual", "adversary"):
                with pytest.raises(TargetDeletionConflictError):
                    await service.delete_target_async(target_registry_name=name)
            assert await service.delete_target_async(target_registry_name="unrelated")
        finally:
            release.set()
            await estimator.close_async()
    assert await service.delete_target_async(target_registry_name="manual")
    assert await service.delete_target_async(target_registry_name="adversary")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (True, 204),
        (False, 404),
        (TargetDeletionProtectedError("Only manually added targets can be deleted."), 403),
        (TargetDeletionConflictError("Target is in use."), 409),
    ],
)
def test_delete_route_status(
    *, outcome: bool | Exception, expected: int, compatibility_headers: dict[str, str]
) -> None:
    service = MagicMock(spec=TargetService)
    if isinstance(outcome, Exception):
        service.delete_target_async.side_effect = outcome
    else:
        service.delete_target_async.return_value = outcome
    with patch("pyrit.backend.routes.targets.get_target_service", return_value=service):
        response = TestClient(app, headers=compatibility_headers).delete("/api/targets/manual")
    assert response.status_code == expected
    service.delete_target_async.assert_awaited_once_with(target_registry_name="manual")
    if expected == 204:
        assert response.content == b""
