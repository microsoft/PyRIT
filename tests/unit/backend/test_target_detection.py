# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for automatic target capability detection."""

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrit.backend.routes.targets import router
from pyrit.backend.services.target_service import TargetService
from pyrit.models import TargetCapabilities
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget, TargetConfiguration
from pyrit.prompt_target.common.discover_target_capabilities import CapabilityDiscoveryError
from pyrit.registry import TargetRegistry
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.mocks import MockPromptTarget


class _DetectableTarget(MockPromptTarget):
    def __init__(self, *, custom_configuration: TargetConfiguration | None = None) -> None:
        super().__init__(custom_configuration=custom_configuration)
        self.closed = False

    def get_reconstruction_parameters(self) -> dict[str, object]:
        return {"custom_configuration": self.configuration}

    async def cleanup_target_async(self) -> None:
        self.closed = True


@pytest.fixture
def detection_service(patch_central_database: None) -> Iterator[tuple[TargetService, _DetectableTarget]]:
    TargetRegistry.reset_registry_singleton()
    service = TargetService()
    service._registry.register_class(_DetectableTarget)
    target = _DetectableTarget()
    service._registry.instances.register(target, name="test-target")
    yield service, target
    TargetRegistry.reset_registry_singleton()


@pytest.fixture
def composite_detection(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> tuple[TargetService, RoundRobinTarget]:
    service, first = detection_service
    second = _DetectableTarget()
    parent = RoundRobinTarget(targets=[first, second], weights=[2, 1])
    service._registry.instances.register(parent, name="round-robin")
    return service, parent


async def test_composite_detection_probes_each_child_privately_and_updates_only_parent(
    composite_detection: tuple[TargetService, RoundRobinTarget],
) -> None:
    service, parent = composite_detection
    parent._counter = 1
    children = parent.inner_targets
    originals = [child.configuration for child in children]
    expected_hash = parent.get_identifier().hash
    results = [
        children[0].capabilities.model_copy(
            update={
                "supports_json_output": True,
                "supports_json_schema": True,
                "input_modalities": frozenset({frozenset({"text"}), frozenset({"text", "image_path"})}),
            }
        ),
        children[1].capabilities.model_copy(update={"supports_json_output": True, "supports_json_schema": False}),
    ]
    probes: list[_DetectableTarget] = []

    async def discover_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        assert all(target is not child for child in children)
        probes.append(target)
        return results[len(probes) - 1]

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=discover_async):
        response = await service.detect_capabilities_async(
            target_registry_name="round-robin", expected_hash=expected_hash
        )

    assert len(probes) == 2
    assert probes[0] is not probes[1]
    assert all(probe.closed for probe in probes)
    assert response.capabilities == parent.aggregate_capabilities(child_capabilities=results)
    assert parent.capabilities == response.capabilities
    assert parent.capabilities.supports_json_output
    assert not parent.capabilities.supports_json_schema
    assert parent.capabilities.input_modalities == frozenset({frozenset({"text"})})
    assert all(child.configuration is original for child, original in zip(children, originals, strict=True))
    assert all(not child.closed for child in children)
    assert parent._counter == 1
    assert parent.get_identifier().hash == expected_hash
    assert not service._detecting
    assert not service._detecting_sources


async def test_composite_detection_failure_preserves_parent_and_children(
    composite_detection: tuple[TargetService, RoundRobinTarget],
) -> None:
    service, parent = composite_detection
    original = parent.configuration
    children = parent.inner_targets
    originals = [child.configuration for child in children]
    probes: list[_DetectableTarget] = []

    async def discover_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        if len(probes) == 2:
            raise CapabilityDiscoveryError("Connection failed")
        return target.capabilities.model_copy(update={"supports_json_output": True})

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=discover_async):
        with pytest.raises(CapabilityDiscoveryError, match=r"Child 2 .*Connection failed"):
            await service.detect_capabilities_async(
                target_registry_name="round-robin", expected_hash=parent.get_identifier().hash
            )

    assert len(probes) == 2
    assert all(probe.closed for probe in probes)
    assert parent.configuration is original
    assert all(child.configuration is original for child, original in zip(children, originals, strict=True))
    assert not service._detecting
    assert not service._detecting_sources


async def test_composite_detection_rejects_changes_to_child_configuration(
    composite_detection: tuple[TargetService, RoundRobinTarget],
) -> None:
    service, parent = composite_detection
    original = parent.configuration
    child = parent.inner_targets[1]

    async def change_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        child.apply_capabilities(capabilities=child.capabilities.model_copy(update={"supports_json_output": True}))
        return target.capabilities

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=change_async):
        with pytest.raises(FileExistsError, match="changed during"):
            await service.detect_capabilities_async(
                target_registry_name="round-robin", expected_hash=parent.get_identifier().hash
            )
    assert parent.configuration is original
    assert child.capabilities.supports_json_output
    assert not service._detecting_sources


async def test_composite_detection_has_one_deadline_for_all_children(
    composite_detection: tuple[TargetService, RoundRobinTarget],
) -> None:
    service, parent = composite_detection
    original = parent.configuration
    checks = 0
    deadline = asyncio.timeout(TEST_WATCHDOG_SECONDS)

    async def probe_async(target: _DetectableTarget) -> TargetCapabilities:
        nonlocal checks
        checks += 1
        if checks == 2:
            deadline.reschedule(asyncio.get_running_loop().time() - 1)
            await asyncio.Event().wait()
        return target.capabilities

    with (
        patch("pyrit.backend.services.target_service.asyncio.timeout", return_value=deadline) as timeout,
        patch.object(service, "_probe_capabilities_async", side_effect=probe_async),
    ):
        with pytest.raises(TimeoutError):
            await service.detect_capabilities_async(
                target_registry_name="round-robin", expected_hash=parent.get_identifier().hash
            )
    timeout.assert_called_once_with(service.DETECTION_TIMEOUT_SECONDS)
    assert checks == 2
    assert parent.configuration is original
    assert not service._detecting
    assert not service._detecting_sources


async def test_composite_detection_reconstructs_each_openai_endpoint_with_its_own_client(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, _ = detection_service
    service._registry.register_class(OpenAIChatTarget)
    children = [
        OpenAIChatTarget(api_key="test-key", endpoint=f"https://child-{index}.example.test", model_name="test-model")
        for index in range(2)
    ]
    parent = RoundRobinTarget(targets=children)
    service._registry.instances.register(parent, name="openai-round-robin")
    probes: list[OpenAIChatTarget] = []

    async def discover_async(*, target: OpenAIChatTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        return target.capabilities.model_copy(update={"supports_json_output": True})

    try:
        with patch(
            "pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=discover_async
        ):
            result = await service.detect_capabilities_async(
                target_registry_name="openai-round-robin", expected_hash=parent.get_identifier().hash
            )
        assert result.capabilities.supports_json_output
        assert len(probes) == len(children)
        for probe, child in zip(probes, children, strict=True):
            assert probe.get_identifier().hash == child.get_identifier().hash
            assert probe._async_client is not child._async_client
            assert probe._async_client is not None
            assert probe._async_client.is_closed()
            assert child._async_client is not None
            assert not child._async_client.is_closed()
    finally:
        for child in children:
            await child.cleanup_target_async()


@pytest.mark.parametrize("first_name", ["round-robin", "test-target"])
async def test_composite_detection_blocks_overlapping_child_and_alias_requests(
    composite_detection: tuple[TargetService, RoundRobinTarget], first_name: str
) -> None:
    service, parent = composite_detection
    child = parent.inner_targets[0]
    service._registry.instances.register(child, name="child-alias")
    started = asyncio.Event()
    probes: list[_DetectableTarget] = []
    original = parent.configuration

    async def wait_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        started.set()
        await asyncio.Event().wait()
        return target.capabilities

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=wait_async):
        first = parent if first_name == "round-robin" else child
        task = asyncio.create_task(
            service.detect_capabilities_async(
                target_registry_name=first_name, expected_hash=first.get_identifier().hash
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=TEST_WATCHDOG_SECONDS)
            second_name = "test-target" if first_name == "round-robin" else "round-robin"
            second = child if second_name == "test-target" else parent
            with pytest.raises(FileExistsError, match="already running"):
                await service.detect_capabilities_async(
                    target_registry_name=second_name, expected_hash=second.get_identifier().hash
                )
            with pytest.raises(FileExistsError, match="already running"):
                await service.detect_capabilities_async(
                    target_registry_name="child-alias", expected_hash=child.get_identifier().hash
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert parent.configuration is original
    assert all(probe.closed for probe in probes)
    assert not service._detecting
    assert not service._detecting_sources


async def test_detection_applies_private_result_and_releases_probe(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    original = target.configuration
    expected_hash = target.get_identifier().hash
    probes: list[_DetectableTarget] = []
    detected = target.capabilities.model_copy(update={"supports_json_output": True})

    async def discover_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        assert target is not detection_service[1]
        assert detection_service[1].configuration is original
        assert kwargs["raise_on_error"] is True
        assert frozenset({"text", "image_path"}) in kwargs["test_modalities"]
        return detected

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=discover_async):
        result = await service.detect_capabilities_async(
            target_registry_name="test-target", expected_hash=expected_hash
        )

    assert result.capabilities == detected
    assert target.capabilities == detected
    assert target.configuration.policy == original.policy
    assert result.identifier.hash == expected_hash
    assert probes[0].closed
    assert not target.closed
    rebuilt = service._registry.recreate_instance(source=target, params={})
    assert rebuilt.capabilities == detected
    await rebuilt.cleanup_target_async()


async def test_detection_uses_real_probes_to_find_new_input_support(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    assert frozenset({"image_path"}) not in target.capabilities.input_modalities
    result = await service.detect_capabilities_async(
        target_registry_name="test-target", expected_hash=target.get_identifier().hash
    )
    assert frozenset({"image_path"}) in result.capabilities.input_modalities
    assert frozenset({"text", "audio_path"}) in result.capabilities.input_modalities
    assert result.capabilities.output_modalities == frozenset({frozenset({"text"})})


async def test_detection_reconstructs_openai_without_closing_the_live_client(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, _ = detection_service
    service._registry.register_class(OpenAIChatTarget)
    target = OpenAIChatTarget(api_key="test-key", endpoint="https://example.test", model_name="test-model")
    service._registry.instances.register(target, name="openai")
    probes: list[OpenAIChatTarget] = []

    async def discover_async(*, target: OpenAIChatTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        return target.capabilities.model_copy(update={"supports_json_output": True})

    try:
        with patch(
            "pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=discover_async
        ):
            result = await service.detect_capabilities_async(
                target_registry_name="openai", expected_hash=target.get_identifier().hash
            )
        assert result.capabilities.supports_json_output
        assert probes[0]._async_client is not target._async_client
        assert probes[0]._async_client is not None
        assert target._async_client is not None
        assert probes[0]._async_client.is_closed()
        assert not target._async_client.is_closed()
    finally:
        await target.cleanup_target_async()


@pytest.mark.parametrize("error", [CapabilityDiscoveryError("Connection failed"), TimeoutError()])
async def test_detection_failure_keeps_source_and_releases_probe(
    detection_service: tuple[TargetService, _DetectableTarget], error: Exception
) -> None:
    service, target = detection_service
    original = target.configuration
    probes: list[_DetectableTarget] = []

    async def fail_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        raise error

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=fail_async):
        with pytest.raises(type(error)):
            await service.detect_capabilities_async(
                target_registry_name="test-target", expected_hash=target.get_identifier().hash
            )
    assert target.configuration is original
    assert probes[0].closed
    assert not service._detecting


async def test_detection_rejects_changed_source_before_commit(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    replacement = target.capabilities.model_copy(update={"supports_multi_turn": False})

    async def change_async(**kwargs: object) -> TargetCapabilities:
        target.apply_capabilities(capabilities=replacement)
        return TargetCapabilities(supports_json_output=True)

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=change_async):
        with pytest.raises(FileExistsError, match="changed during"):
            await service.detect_capabilities_async(
                target_registry_name="test-target", expected_hash=target.get_identifier().hash
            )
    assert target.capabilities == replacement


async def test_detection_rejects_missing_stale_and_duplicate_requests(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    with pytest.raises(LookupError):
        await service.detect_capabilities_async(target_registry_name="missing", expected_hash="hash")
    with pytest.raises(FileExistsError, match="changed"):
        await service.detect_capabilities_async(target_registry_name="test-target", expected_hash="old")
    service._detecting.add("test-target")
    with pytest.raises(FileExistsError, match="already running"):
        await service.detect_capabilities_async(
            target_registry_name="test-target", expected_hash=target.get_identifier().hash
        )


async def test_detection_rejects_unsafe_targets(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, _ = detection_service
    target = MockPromptTarget()
    service._registry.instances.register(target, name="unsafe")
    with pytest.raises(ValueError, match="reconstruction"):
        await service.detect_capabilities_async(
            target_registry_name="unsafe", expected_hash=target.get_identifier().hash
        )
    target.inner_targets = [MockPromptTarget()]
    with pytest.raises(ValueError, match="composite"):
        await service.detect_capabilities_async(
            target_registry_name="unsafe", expected_hash=target.get_identifier().hash
        )
    assert not service._detecting


async def test_detection_operation_deadline_preserves_source(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    original = target.configuration

    async def wait_async(**kwargs: object) -> TargetCapabilities:
        await asyncio.Event().wait()
        return TargetCapabilities()

    with (
        patch.object(service, "DETECTION_TIMEOUT_SECONDS", 0.1),
        patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=wait_async),
    ):
        with pytest.raises(TimeoutError):
            await service.detect_capabilities_async(
                target_registry_name="test-target", expected_hash=target.get_identifier().hash
            )
    assert target.configuration is original
    assert not service._detecting


async def test_detection_cancellation_releases_probe_and_admission(
    detection_service: tuple[TargetService, _DetectableTarget],
) -> None:
    service, target = detection_service
    original = target.configuration
    entered = asyncio.Event()
    probes: list[_DetectableTarget] = []

    async def wait_async(*, target: _DetectableTarget, **kwargs: object) -> TargetCapabilities:
        probes.append(target)
        entered.set()
        await asyncio.Event().wait()
        return TargetCapabilities()

    with patch("pyrit.backend.services.target_service.discover_target_capabilities_async", side_effect=wait_async):
        task = asyncio.create_task(
            service.detect_capabilities_async(
                target_registry_name="test-target", expected_hash=target.get_identifier().hash
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=TEST_WATCHDOG_SECONDS)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert probes[0].closed
    assert target.configuration is original
    assert not service._detecting
    assert not service._detecting_sources


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (LookupError("missing"), 404),
        (FileExistsError("busy"), 409),
        (ValueError("unsafe"), 400),
        (CapabilityDiscoveryError("inconclusive"), 502),
        (TimeoutError(), 504),
    ],
)
def test_detection_route_errors(error: Exception, status_code: int) -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    service = AsyncMock(spec=TargetService)
    service.detect_capabilities_async.side_effect = error
    with patch("pyrit.backend.routes.targets.get_target_service", return_value=service):
        response = TestClient(app).post("/api/targets/test-target/detect-capabilities", json={"expected_hash": "hash"})
    assert response.status_code == status_code


@pytest.mark.parametrize("target_name", ["test-target", "round-robin"])
def test_detection_route_returns_updated_target(
    composite_detection: tuple[TargetService, RoundRobinTarget], target_name: str
) -> None:
    service, _ = composite_detection
    target = service.get_target_object(target_registry_name=target_name)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with patch("pyrit.backend.routes.targets.get_target_service", return_value=service):
        response = TestClient(app).post(
            f"/api/targets/{target_name}/detect-capabilities", json={"expected_hash": target.get_identifier().hash}
        )
    assert response.status_code == 200
    assert response.json()["capabilities"]["supports_json_output"] is True
