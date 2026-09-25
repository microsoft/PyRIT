# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend scenario service and routes.
"""

import asyncio
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ListRegisteredScenariosResponse
from pyrit.backend.routes.scenarios import estimate_scenario_run_size
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_service import (
    ScenarioService,
    _metadata_to_registered_scenario,
    get_scenario_service,
)
from pyrit.models import (
    Parameter,
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
    ScenarioTechniqueSummary,
)
from pyrit.models.catalog.scenario import RegisteredScenario
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.registry import ScenarioMetadata, ScenarioRegistry, TargetRegistry
from pyrit.scenario import Scenario
from pyrit.scenario.core import (
    DatasetAttackConfiguration,
    ScenarioTechnique,
    get_default_adversarial_target,
    override_default_adversarial_target,
)
from pyrit.scenario.scenarios.airt.scam import Scam
from unit.mocks import MockPromptTarget

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


class _EstimateTechnique(ScenarioTechnique):
    """Technique enum for configured catalog estimate tests."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    PROMPT_SENDING = ("prompt_sending", {"default"})
    JAILBREAK_SYSTEM_PROMPT = ("jailbreak_system_prompt", {"default"})
    FLIP = ("flip", {"direct"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return aggregate tags."""
        return {"all", "default"}


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app, headers=compatibility_headers)


@pytest.fixture(autouse=True)
def clear_service_cache():
    """Clear the scenario service singleton cache between tests."""
    get_scenario_service.cache_clear()
    yield
    get_scenario_service.cache_clear()


def _make_scenario_metadata(
    *,
    registry_name: str = "test.scenario",
    class_name: str = "TestScenario",
    scenario_version: int = 1,
    description: str = "A test scenario",
    description_markdown: str = "A test scenario",
    default_technique: str = "default",
    default_techniques: tuple[str, ...] = ("role_play", "many_shot"),
    all_techniques: tuple[str, ...] = ("role_play", "many_shot"),
    aggregate_techniques: tuple[str, ...] = ("all", "default"),
    aggregate_technique_expansions: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("all", ("role_play", "many_shot")),
        ("default", ("role_play",)),
    ),
    technique_summaries: tuple[ScenarioTechniqueSummary, ...] = (
        ScenarioTechniqueSummary(
            name="role_play",
            description="Frames the objective as role play.",
            tags=["default", "single_turn"],
        ),
        ScenarioTechniqueSummary(
            name="many_shot",
            description="Provides many examples before the objective.",
            tags=["multi_turn"],
        ),
    ),
    default_datasets: tuple[str, ...] = ("test_dataset",),
    baseline_policy: str = "enabled",
    include_baseline_by_default: bool = True,
    uses_default_adversarial_target: bool = False,
) -> ScenarioMetadata:
    """Create a ScenarioMetadata instance for testing."""
    return ScenarioMetadata(
        registry_name=registry_name,
        class_name=class_name,
        class_module="pyrit.scenario.scenarios.test",
        class_description=description,
        scenario_version=scenario_version,
        description_markdown=description_markdown,
        default_technique=default_technique,
        default_techniques=default_techniques,
        all_techniques=all_techniques,
        aggregate_techniques=aggregate_techniques,
        aggregate_technique_expansions=aggregate_technique_expansions,
        technique_summaries=technique_summaries,
        default_datasets=default_datasets,
        baseline_policy=baseline_policy,
        include_baseline_by_default=include_baseline_by_default,
        uses_default_adversarial_target=uses_default_adversarial_target,
    )


@pytest.mark.parametrize("uses_default", [False, True])
def test_catalog_preserves_adversarial_default_usage(uses_default: bool) -> None:
    """The public catalog exposes usage from real registry metadata."""
    metadata = _make_scenario_metadata(uses_default_adversarial_target=uses_default)
    summary = _metadata_to_registered_scenario(metadata=metadata)
    assert summary.model_dump()["uses_default_adversarial_target"] is uses_default


# ============================================================================
# ScenarioService Unit Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialEstimateScope:
    async def test_cold_registry_estimate_uses_selected_target_without_global_fallback_async(self) -> None:
        registry = ScenarioRegistry()
        selected = MockPromptTarget()
        targets = {
            "objective": MockPromptTarget(),
            "selected": selected,
            "objective_scorer_chat": MockPromptTarget(),
        }
        target_registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        target_registry.instances.get.side_effect = targets.get

        async def estimate_async(scenario: Scam, *, target_is_configured: bool = False) -> ScenarioRunSizeEstimate:
            assert scenario._adversarial_chat is selected
            assert get_default_adversarial_target() is selected
            return ScenarioRunSizeEstimate(estimated_attack_count=0)

        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            patch.object(TargetRegistry, "get_registry_singleton", return_value=target_registry),
            patch.object(Scam, "get_run_size_estimate_async", autospec=True, side_effect=estimate_async),
            patch(
                "pyrit.scenario.core.scenario_target_defaults.OpenAIChatTarget",
                side_effect=AssertionError("The request must not construct the server fallback."),
            ),
        ):
            assert registry._metadata_cache is None
            service = ScenarioService()
            result = await service.estimate_scenario_run_size_async(
                scenario_name="airt.scam",
                request=ScenarioRunSizeEstimateRequest(
                    target_name="objective",
                    adversarial_target_name="selected",
                    max_dataset_size=1,
                ),
            )
            assert result is not None
            assert result.estimated_attack_count == 0
            assert registry._metadata_cache is None
            assert not service._estimate_cache

    async def test_unknown_scenario_returns_none_without_metadata_construction_async(self) -> None:
        registry = MagicMock(spec=ScenarioRegistry)
        registry.get_class.side_effect = KeyError("missing")
        with patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry):
            result = await ScenarioService().estimate_scenario_run_size_async(
                scenario_name="missing",
                request=ScenarioRunSizeEstimateRequest(),
            )
        assert result is None
        registry.get_registered_class_metadata.assert_not_called()
        registry.create_and_estimate_async.assert_not_called()

    async def test_omitted_adversarial_selection_preserves_outer_scope_async(self) -> None:
        outer = MockPromptTarget()
        registry = MagicMock(spec=ScenarioRegistry)
        registry.get_registered_class_metadata.return_value = _make_scenario_metadata()

        async def estimate_async(**kwargs: object) -> ScenarioRunSizeEstimate:
            assert get_default_adversarial_target() is outer
            await asyncio.sleep(0)
            assert get_default_adversarial_target() is outer
            return ScenarioRunSizeEstimate(estimated_attack_count=0)

        registry.create_and_estimate_async = AsyncMock(side_effect=estimate_async)
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            override_default_adversarial_target(outer),
        ):
            result = await ScenarioService().estimate_scenario_run_size_async(
                scenario_name="test.scenario",
                request=ScenarioRunSizeEstimateRequest(),
            )
            assert result.estimated_attack_count == 0
            assert get_default_adversarial_target() is outer

    async def test_concurrent_estimates_scope_introspection_and_preserve_default_cache_async(self) -> None:
        first, second, fallback = (MockPromptTarget() for _ in range(3))
        targets = {"first": first, "second": second, "adversarial_chat": fallback}
        target_registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        target_registry.instances.get.side_effect = targets.get
        registry = MagicMock(spec=ScenarioRegistry)
        metadata = _make_scenario_metadata()
        registry.get_registered_class_metadata.return_value = metadata
        introspected: list[PromptTarget] = []
        estimated: list[PromptTarget] = []
        default_estimate = ScenarioRunSizeEstimate(estimated_attack_count=0)
        arrived = asyncio.Event()

        def construct() -> MagicMock:
            introspected.append(get_default_adversarial_target())
            scenario = MagicMock(spec=Scenario)
            scenario._default_dataset_config = DatasetAttackConfiguration(dataset_names=["test_dataset"])
            return scenario

        async def estimate_async(**kwargs: object) -> ScenarioRunSizeEstimate:
            target = get_default_adversarial_target()
            estimated.append(target)
            if len(estimated) == 2:
                arrived.set()
            await asyncio.wait_for(arrived.wait(), timeout=5)
            assert get_default_adversarial_target() is target
            return ScenarioRunSizeEstimate.unavailable(note="request-specific")

        default_scenario = MagicMock(spec=Scenario)
        default_scenario.get_default_run_size_estimate_async = AsyncMock(return_value=default_estimate)
        registry.create_instance.return_value = default_scenario
        registry.get_class.return_value = construct
        registry.create_and_estimate_async = AsyncMock(side_effect=estimate_async)
        with (
            patch.object(TargetRegistry, "get_registry_singleton", return_value=target_registry),
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        ):
            service = ScenarioService()
            service._estimate_semaphore = asyncio.Semaphore(2)
            before = await service.get_scenario_async(scenario_name=metadata.registry_name)
            cache_before = dict(service._estimate_cache)
            await asyncio.gather(
                *(
                    service.estimate_scenario_run_size_async(
                        scenario_name=metadata.registry_name,
                        request=ScenarioRunSizeEstimateRequest(adversarial_target_name=name, max_dataset_size=1),
                    )
                    for name in ("first", "second")
                )
            )
            assert introspected == estimated == [first, second]
            assert get_default_adversarial_target() is fallback
            assert dict(service._estimate_cache) == cache_before
            assert await service.get_scenario_async(scenario_name=metadata.registry_name) == before
            registry.create_instance.assert_called_once()

    @pytest.mark.parametrize("error", [ValueError, asyncio.CancelledError])
    async def test_estimate_scope_resets_on_failure_async(self, error: type[BaseException]) -> None:
        selected, fallback = MockPromptTarget(), MockPromptTarget()
        registry = MagicMock(spec=ScenarioRegistry)
        registry.get_registered_class_metadata.return_value = _make_scenario_metadata()
        target_registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        target_registry.instances.get.side_effect = {"selected": selected, "adversarial_chat": fallback}.get

        async def fail_async(**kwargs: object) -> ScenarioRunSizeEstimate:
            assert get_default_adversarial_target() is selected
            raise error("estimate stopped")

        registry.create_and_estimate_async = AsyncMock(side_effect=fail_async)
        with (
            patch.object(TargetRegistry, "get_registry_singleton", return_value=target_registry),
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        ):
            service = ScenarioService()
            with pytest.raises(error, match="estimate stopped"):
                await service.estimate_scenario_run_size_async(
                    scenario_name="test.scenario",
                    request=ScenarioRunSizeEstimateRequest(adversarial_target_name="selected"),
                )
            assert get_default_adversarial_target() is fallback
            assert not service._estimate_cache

    @pytest.mark.parametrize("selection", ["missing", "wrong_type", "single_turn"])
    async def test_estimate_rejects_invalid_explicit_target_async(self, selection: str) -> None:
        single_turn = MockPromptTarget()
        single_turn.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
        targets = {"wrong_type": object(), "single_turn": single_turn}
        target_registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
        target_registry.instances.get.side_effect = targets.get
        target_registry.instances.get_names.return_value = list(targets)
        registry = MagicMock(spec=ScenarioRegistry)
        registry.get_registered_class_metadata.return_value = _make_scenario_metadata()
        with (
            patch.object(TargetRegistry, "get_registry_singleton", return_value=target_registry),
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        ):
            service = ScenarioService()
            with pytest.raises(ValueError, match="not found|must be a PromptTarget|must support multi_turn"):
                await service.estimate_scenario_run_size_async(
                    scenario_name="test.scenario",
                    request=ScenarioRunSizeEstimateRequest(adversarial_target_name=selection),
                )
            registry.create_and_estimate_async.assert_not_called()
            assert ScenarioConfigurationResolver.resolve_adversarial_target(target_name=None) is None


class TestScenarioServiceListScenarios:
    """Tests for ScenarioService.list_scenarios_async."""

    async def test_list_scenarios_returns_empty_when_no_scenarios(self) -> None:
        """Test that list returns empty list when no scenarios are registered."""
        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = []

            result = await service.list_scenarios_async()

            assert result.items == []
            assert result.pagination.has_more is False

    async def test_list_scenarios_returns_scenarios_from_registry(self) -> None:
        """Test that list returns scenarios from registry."""
        metadata = _make_scenario_metadata()

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            assert len(result.items) == 1
            assert result.items[0].scenario_name == "test.scenario"
            assert result.items[0].scenario_type == "TestScenario"
            assert result.items[0].description == "A test scenario"
            assert result.items[0].description_markdown == "A test scenario"
            assert result.items[0].default_technique == "default"
            assert result.items[0].default_techniques == ["role_play", "many_shot"]
            assert result.items[0].aggregate_techniques == ["all", "default"]
            assert result.items[0].aggregate_technique_expansions["default"] == ["role_play"]
            assert result.items[0].all_techniques == ["role_play", "many_shot"]
            assert result.items[0].technique_summaries[0].description == "Frames the objective as role play."
            assert result.items[0].technique_summaries[0].tags == ["default", "single_turn"]
            assert result.items[0].default_datasets == ["test_dataset"]
            assert result.items[0].baseline_policy == "enabled"
            assert result.items[0].include_baseline_by_default is True

    async def test_list_scenarios_can_return_metadata_without_waiting_for_estimates(self) -> None:
        """Metadata-only catalog pages do not construct scenarios."""
        metadata = _make_scenario_metadata()

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async(include_estimates=False)
        assert result.items[0].scenario_name == "test.scenario"
        assert result.items[0].scenario_name == "test.scenario"
        assert result.items[0].default_run_size == ScenarioRunSizeEstimate.unavailable()
        service._registry.create_instance.assert_not_called()

    async def test_estimate_is_offloaded_and_cached(self) -> None:
        """Scenario-owned estimates run in a worker once and are reused by subsequent reads."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=4,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=4)],
            datasets=[
                ScenarioDatasetSummary(
                    name="test_dataset",
                    logical_seed_group_count=4,
                    selected_seed_group_count=2,
                    configured_caps=[
                        ScenarioDatasetSizeCap(
                            label="per-dataset cap",
                            count=2,
                            configured_on="dataset",
                            dataset_name="test_dataset",
                        )
                    ],
                )
            ],
        )
        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata
            service._registry.create_instance.return_value = scenario

            first = await service.get_scenario_async(scenario_name="test.scenario")
            second = await service.get_scenario_async(scenario_name="test.scenario")

        assert first is not None
        assert second is not None
        assert first.default_run_size == estimate
        assert second.default_run_size == estimate
        assert first.default_run_size.datasets == estimate.datasets
        assert second.default_run_size.datasets == estimate.datasets
        service._registry.create_instance.assert_called_once_with("test.scenario")

    async def test_default_catalog_estimate_uses_read_only_dataset_resolution(self) -> None:
        """Bulk catalog estimates do not auto-fetch datasets into memory."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch("pyrit.backend.services.scenario_service.read_only_dataset_resolution") as read_only_resolution,
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.create_instance.return_value = scenario

            result = await service._get_default_run_size_estimate_async(metadata=metadata)

        assert result == estimate
        read_only_resolution.assert_called_once_with()

    async def test_concurrent_estimate_reads_share_one_task(self) -> None:
        """Concurrent catalog readers share one atomic single-flight estimate."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async() -> ScenarioRunSizeEstimate:
            started.set()
            await release.wait()
            return estimate

        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(side_effect=estimate_async)

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.create_instance.return_value = scenario

            first = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
            await started.wait()
            second = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
            await asyncio.sleep(0)
            assert service._registry.create_instance.call_count == 1

            release.set()
            assert await asyncio.gather(first, second) == [estimate, estimate]
            await asyncio.sleep(0)

        assert service._estimate_tasks == {}

    def test_estimate_task_cleanup_preserves_replacement(self) -> None:
        """A stale completion callback cannot remove the replacement task for the same key."""
        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            cache_key = ("test.scenario", 1)
            completed_task = MagicMock(spec=asyncio.Task)
            replacement_task = MagicMock(spec=asyncio.Task)
            service._estimate_tasks = OrderedDict([(cache_key, replacement_task)])

            service._clear_estimate_task(task=completed_task, cache_key=cache_key)
            assert service._estimate_tasks[cache_key] is replacement_task

            service._clear_estimate_task(task=replacement_task, cache_key=cache_key)
            assert service._estimate_tasks == {}

    async def test_cancelled_estimate_waiter_does_not_cancel_shared_task(self) -> None:
        """Cancelling one waiter leaves the shared estimate available to other readers."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async() -> ScenarioRunSizeEstimate:
            started.set()
            await release.wait()
            return estimate

        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(side_effect=estimate_async)

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.create_instance.return_value = scenario

            cancelled_waiter = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
            await started.wait()
            cancelled_waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled_waiter

            surviving_waiter = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
            release.set()
            assert await surviving_waiter == estimate
            await asyncio.sleep(0)

        assert service._registry.create_instance.call_count == 1
        assert service._estimate_tasks == {}

    async def test_failed_estimate_task_is_removed_and_retryable(self) -> None:
        """A failed single-flight task is removed so the next caller can retry."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        service = ScenarioService()
        service._compute_default_run_size_estimate_async = AsyncMock(side_effect=RuntimeError("estimate failed"))

        with pytest.raises(RuntimeError, match="estimate failed"):
            await service._get_default_run_size_estimate_async(metadata=metadata)
        await asyncio.sleep(0)

        assert service._estimate_tasks == {}
        service._compute_default_run_size_estimate_async = AsyncMock(return_value=estimate)
        assert await service._get_default_run_size_estimate_async(metadata=metadata) == estimate

    async def test_cancelled_estimate_task_is_removed_and_retryable(self) -> None:
        """A cancelled single-flight task is removed so the next caller can retry."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def cancelled_estimate_async(
            *,
            scenario_name: str,
            cache_key: tuple[str, int],
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            assert cache_key == (metadata.registry_name, metadata.scenario_version)
            started.set()
            await blocked.wait()
            raise AssertionError("The estimate task should have been cancelled.")

        service = ScenarioService()
        service._compute_default_run_size_estimate_async = AsyncMock(side_effect=cancelled_estimate_async)
        waiter = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
        await asyncio.wait_for(started.wait(), timeout=1)

        service._estimate_tasks[(metadata.registry_name, metadata.scenario_version)].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)

        assert service._estimate_tasks == {}
        service._compute_default_run_size_estimate_async = AsyncMock(return_value=estimate)
        assert await service._get_default_run_size_estimate_async(metadata=metadata) == estimate

    async def test_completed_stale_task_cannot_block_inflight_capacity(self) -> None:
        """A done task is pruned before the bounded inflight capacity check."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch("pyrit.backend.services.scenario_service._ESTIMATE_INFLIGHT_SIZE", 1),
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.create_instance.return_value = scenario
            stale = asyncio.create_task(asyncio.sleep(0, result=estimate))
            await stale
            service._estimate_tasks = OrderedDict([(("stale.scenario", 1), stale)])

            result = await asyncio.wait_for(
                service._get_default_run_size_estimate_async(metadata=metadata),
                timeout=1,
            )

        assert result == estimate

    async def test_one_failed_estimate_does_not_break_catalog(self) -> None:
        """A scenario estimate failure is explicit and isolated from other catalog entries."""
        metadata = [
            _make_scenario_metadata(registry_name="test.good"),
            _make_scenario_metadata(registry_name="test.bad"),
        ]
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=2,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=2)],
        )
        good_scenario = MagicMock()
        good_scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)
        bad_scenario = MagicMock()
        bad_scenario.get_default_run_size_estimate_async = AsyncMock(side_effect=RuntimeError("dataset unavailable"))

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata
            service._registry.create_instance.side_effect = lambda name: {
                "test.good": good_scenario,
                "test.bad": bad_scenario,
            }[name]

            result = await service.list_scenarios_async()
        assert "RuntimeError" in result.items[1].default_run_size.note

    async def test_catalog_estimates_use_bounded_parallelism(self) -> None:
        """Catalog estimates run concurrently without exceeding their configured bound."""
        metadata = [_make_scenario_metadata(registry_name=f"test.scenario_{index}") for index in range(3)]
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        two_started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum_active = 0

        async def estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            nonlocal active, maximum_active
            assert scenario_name.startswith("test.scenario_")
            assert not execution_timed_out.is_set()
            construction_complete.set()
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1
            return estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_all_registered_class_metadata.return_value = metadata
        service._estimate_semaphore = asyncio.Semaphore(2)
        service._run_default_estimate_async = AsyncMock(side_effect=estimate_async)

        catalog_task = asyncio.create_task(service.list_scenarios_async())
        await asyncio.wait_for(two_started.wait(), timeout=1)
        await asyncio.sleep(0)

        assert service._run_default_estimate_async.await_count == 2
        release.set()
        result = await catalog_task

        assert maximum_active == 2
        assert service._run_default_estimate_async.await_count == 3
        assert all(item.default_run_size == estimate for item in result.items)

    async def test_catalog_queue_wait_does_not_start_execution_timeout(self) -> None:
        """A queued catalog estimate starts its timeout only after acquiring capacity."""
        metadata = [_make_scenario_metadata(registry_name=f"test.scenario_{index}") for index in range(2)]
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        first_estimate_started = asyncio.Event()
        release_first_estimate = asyncio.Event()
        second_timeout_started = asyncio.Event()
        estimate_count = 0
        timeout_count = 0

        async def estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            nonlocal estimate_count
            assert scenario_name.startswith("test.scenario_")
            assert not execution_timed_out.is_set()
            construction_complete.set()
            estimate_count += 1
            if estimate_count == 1:
                first_estimate_started.set()
                await release_first_estimate.wait()
            return estimate

        original_wait = asyncio.wait

        async def wait_async(
            tasks: set[asyncio.Task[ScenarioRunSizeEstimate]],
            *,
            timeout: float,
        ) -> tuple[set[asyncio.Task[ScenarioRunSizeEstimate]], set[asyncio.Task[ScenarioRunSizeEstimate]]]:
            nonlocal timeout_count
            assert timeout > 0
            timeout_count += 1
            if timeout_count == 2:
                second_timeout_started.set()
            return await original_wait(tasks, timeout=timeout)

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_all_registered_class_metadata.return_value = metadata
        service._estimate_semaphore = asyncio.Semaphore(1)
        service._run_default_estimate_async = AsyncMock(side_effect=estimate_async)

        with patch("pyrit.backend.services.scenario_service.asyncio.wait", side_effect=wait_async):
            catalog_task = asyncio.create_task(service.list_scenarios_async())
            async with asyncio.timeout(1):
                await first_estimate_started.wait()
            await asyncio.sleep(0)
            assert not second_timeout_started.is_set()

            release_first_estimate.set()
            result = await catalog_task

        assert second_timeout_started.is_set()
        assert timeout_count == 2
        assert all(item.default_run_size == estimate for item in result.items)

    async def test_catalog_execution_timeout_is_unavailable_and_cached(self) -> None:
        """A genuine estimate execution timeout is unavailable and reused from cache."""
        metadata = _make_scenario_metadata()
        estimate_cancelled = asyncio.Event()
        block_estimate = asyncio.Event()

        async def slow_estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            assert not execution_timed_out.is_set()
            construction_complete.set()
            try:
                await block_estimate.wait()
            except asyncio.CancelledError:
                estimate_cancelled.set()
                raise
            raise AssertionError("The blocked estimate should be cancelled by its execution timeout.")

        service = ScenarioService()

        with (
            patch.object(
                service,
                "_run_default_estimate_async",
                new_callable=AsyncMock,
                side_effect=slow_estimate_async,
            ) as run_default_estimate,
            patch("pyrit.backend.services.scenario_service._DEFAULT_ESTIMATE_TIMEOUT_SECONDS", 0.01),
        ):
            result = await service._get_default_run_size_estimate_async(metadata=metadata)
            cached = await service._get_default_run_size_estimate_async(metadata=metadata)
            await asyncio.sleep(0)

        assert result.estimated_attack_count is None
        assert result.note is not None and "timed out" in result.note
        assert cached is result
        assert estimate_cancelled.is_set()
        assert service._timed_out_estimate_workers == set()
        assert run_default_estimate.await_count == 1
        assert run_default_estimate.await_args.kwargs["scenario_name"] == metadata.registry_name
        assert service._estimate_tasks == {}

    async def test_cancelled_catalog_compute_tracks_worker_until_exit(self) -> None:
        """Cancelling a catalog compute retains its capacity until the worker exits."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            construction_complete.set()
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            assert execution_timed_out.is_set()
            return estimate

        service = ScenarioService()
        service._estimate_semaphore = asyncio.Semaphore(1)
        service._run_default_estimate_async = AsyncMock(side_effect=estimate_async)
        compute_task = asyncio.create_task(
            service._compute_default_run_size_estimate_async(
                scenario_name=metadata.registry_name,
                cache_key=(metadata.registry_name, metadata.scenario_version),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        compute_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await compute_task

        await asyncio.wait_for(cancelled.wait(), timeout=1)
        assert service._estimate_semaphore.locked()
        assert len(service._timed_out_estimate_workers) == 1

        release.set()
        async with asyncio.timeout(1):
            while service._timed_out_estimate_workers:
                await asyncio.sleep(0)

        assert not service._estimate_semaphore.locked()

    async def test_catalog_timeout_holds_capacity_until_blocking_constructor_exits(self) -> None:
        """A timed-out constructor retains its slot until the underlying thread exits."""
        first_metadata = _make_scenario_metadata(registry_name="test.first")
        second_metadata = _make_scenario_metadata(registry_name="test.second")
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        loop = asyncio.get_running_loop()
        first_started = asyncio.Event()
        second_waiting = asyncio.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        active_lock = threading.Lock()
        active = 0
        maximum_active = 0

        def create_instance(name: str) -> MagicMock:
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            if name == first_metadata.registry_name:
                loop.call_soon_threadsafe(first_started.set)
                release_first.wait()
            else:
                second_started.set()
            with active_lock:
                active -= 1
            scenario = MagicMock()
            scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)
            return scenario

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.create_instance.side_effect = create_instance
        service._estimate_semaphore = asyncio.Semaphore(1)
        acquire = service._estimate_semaphore.acquire

        async def acquire_second_async() -> bool:
            second_waiting.set()
            return await acquire()

        second_task = None
        with patch("pyrit.backend.services.scenario_service._DEFAULT_ESTIMATE_TIMEOUT_SECONDS", 10):
            try:
                with patch("pyrit.backend.services.scenario_service._DEFAULT_ESTIMATE_TIMEOUT_SECONDS", 0.02):
                    first_result = await service._get_default_run_size_estimate_async(metadata=first_metadata)
                await asyncio.wait_for(first_started.wait(), timeout=10)

                with patch.object(service._estimate_semaphore, "acquire", side_effect=acquire_second_async):
                    second_task = asyncio.create_task(
                        service._get_default_run_size_estimate_async(metadata=second_metadata)
                    )
                    await asyncio.wait_for(second_waiting.wait(), timeout=10)

                    assert first_started.is_set()
                    assert service._estimate_semaphore.locked()
                    assert not second_started.is_set()
                    assert not second_task.done()

                    release_first.set()
                    second_result = await asyncio.wait_for(second_task, timeout=10)
            finally:
                release_first.set()
                tasks = [*service._estimate_tasks.values(), *service._timed_out_estimate_workers]
                if second_task is not None:
                    tasks.append(second_task)
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

        assert first_result.estimated_attack_count is None
        assert first_result.note is not None and "timed out" in first_result.note
        assert second_result == estimate
        assert maximum_active == 1

    async def test_configured_estimate_does_not_wait_for_catalog_estimate(self) -> None:
        """Configured estimates use separate capacity from default catalog estimates."""
        metadata = _make_scenario_metadata()
        default_estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        configured_estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=2,
            components=[ScenarioRunSizeComponent(label="Configured sweep", count=2)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def default_estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            assert not execution_timed_out.is_set()
            construction_complete.set()
            started.set()
            await release.wait()
            return default_estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_registered_class_metadata.return_value = metadata
        service._estimate_semaphore = asyncio.Semaphore(1)
        service._run_default_estimate_async = AsyncMock(side_effect=default_estimate_async)
        service._estimate_configured_run_size_async = AsyncMock(return_value=configured_estimate)

        catalog_task = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
        await asyncio.wait_for(started.wait(), timeout=1)
        interactive = await asyncio.wait_for(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=ScenarioRunSizeEstimateRequest(),
            ),
            timeout=1,
        )
        release.set()

        assert interactive == configured_estimate
        assert await catalog_task == default_estimate

    async def test_concurrent_configured_estimates_share_one_task(self) -> None:
        """Equivalent configured requests share one cancellation-safe execution task."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Configured sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async(
            *,
            scenario_name: str,
            request: ScenarioRunSizeEstimateRequest,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            assert request.scenario_params == {"first": 1, "second": 2}
            started.set()
            await release.wait()
            return estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_registered_class_metadata.return_value = metadata
        service._estimate_configured_run_size_async = AsyncMock(side_effect=estimate_async)
        first = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=ScenarioRunSizeEstimateRequest(scenario_params={"first": 1, "second": 2}),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        second = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=ScenarioRunSizeEstimateRequest(scenario_params={"second": 2, "first": 1}),
            )
        )
        await asyncio.sleep(0)

        assert service._estimate_configured_run_size_async.await_count == 1
        release.set()
        assert await asyncio.gather(first, second) == [estimate, estimate]
        await asyncio.sleep(0)
        assert service._configured_estimate_tasks == {}

    async def test_cancelled_configured_waiter_does_not_cancel_shared_task(self) -> None:
        """Cancelling one configured waiter leaves the shared estimate available."""
        metadata = _make_scenario_metadata()
        request = ScenarioRunSizeEstimateRequest()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Configured sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async(
            *,
            scenario_name: str,
            request: ScenarioRunSizeEstimateRequest,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            started.set()
            await release.wait()
            return estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_registered_class_metadata.return_value = metadata
        service._estimate_configured_run_size_async = AsyncMock(side_effect=estimate_async)
        cancelled_waiter = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=request,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        surviving_waiter = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=request,
            )
        )
        release.set()
        assert await surviving_waiter == estimate
        await asyncio.sleep(0)

        assert service._estimate_configured_run_size_async.await_count == 1
        assert service._configured_estimate_tasks == {}

    async def test_failed_configured_estimate_is_removed_and_retryable(self) -> None:
        """A failed configured task is removed so a later request can retry."""
        metadata = _make_scenario_metadata()
        request = ScenarioRunSizeEstimateRequest()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Configured sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def failed_estimate_async(
            *,
            scenario_name: str,
            request: ScenarioRunSizeEstimateRequest,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            started.set()
            await release.wait()
            raise RuntimeError("estimate failed")

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_registered_class_metadata.return_value = metadata
        service._estimate_configured_run_size_async = AsyncMock(side_effect=failed_estimate_async)
        first = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=request,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        second = asyncio.create_task(
            service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=request,
            )
        )
        release.set()

        for waiter in (first, second):
            with pytest.raises(RuntimeError, match="estimate failed"):
                await waiter
        await asyncio.sleep(0)
        assert service._configured_estimate_tasks == {}

        service._estimate_configured_run_size_async = AsyncMock(return_value=estimate)
        assert (
            await service.estimate_scenario_run_size_async(
                scenario_name=metadata.registry_name,
                request=request,
            )
            == estimate
        )

    async def test_configured_estimates_use_bounded_parallelism(self) -> None:
        """Configured estimates run concurrently without exceeding their configured bound."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Configured sweep", count=1)],
        )
        two_started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum_active = 0

        async def estimate_async(
            *,
            scenario_name: str,
            request: ScenarioRunSizeEstimateRequest,
        ) -> ScenarioRunSizeEstimate:
            nonlocal active, maximum_active
            assert scenario_name == metadata.registry_name
            assert request.scenario_params is not None
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1
            return estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_registered_class_metadata.return_value = metadata
        service._configured_estimate_semaphore = asyncio.Semaphore(2)
        service._estimate_configured_run_size_async = AsyncMock(side_effect=estimate_async)

        tasks = [
            asyncio.create_task(
                service.estimate_scenario_run_size_async(
                    scenario_name=metadata.registry_name,
                    request=ScenarioRunSizeEstimateRequest(scenario_params={"request_index": index}),
                )
            )
            for index in range(3)
        ]
        await asyncio.wait_for(two_started.wait(), timeout=1)
        await asyncio.sleep(0)

        assert service._estimate_configured_run_size_async.await_count == 2
        release.set()
        assert await asyncio.gather(*tasks) == [estimate, estimate, estimate]
        assert maximum_active == 2

    async def test_metadata_catalog_remains_responsive_during_estimate(self) -> None:
        """Metadata-only catalog requests do not wait for running estimates."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def estimate_async(
            *,
            scenario_name: str,
            construction_complete: asyncio.Event,
            execution_timed_out: asyncio.Event,
        ) -> ScenarioRunSizeEstimate:
            assert scenario_name == metadata.registry_name
            assert not execution_timed_out.is_set()
            construction_complete.set()
            started.set()
            await release.wait()
            return estimate

        service = ScenarioService()
        service._registry = MagicMock()
        service._registry.get_all_registered_class_metadata.return_value = [metadata]
        service._run_default_estimate_async = AsyncMock(side_effect=estimate_async)

        estimate_task = asyncio.create_task(service._get_default_run_size_estimate_async(metadata=metadata))
        await asyncio.wait_for(started.wait(), timeout=1)
        catalog = await asyncio.wait_for(
            service.list_scenarios_async(include_estimates=False),
            timeout=1,
        )
        assert not estimate_task.done()

        release.set()
        assert await estimate_task == estimate
        assert catalog.items[0].scenario_name == metadata.registry_name

    async def test_unavailable_estimate_cache_expires(self) -> None:
        """A transient estimate failure is retried after the unavailable-result TTL."""
        metadata = _make_scenario_metadata()
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(
            side_effect=[RuntimeError("temporary failure"), estimate]
        )

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch("pyrit.backend.services.scenario_service._UNAVAILABLE_CACHE_TTL_SECONDS", 0),
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata
            service._registry.create_instance.return_value = scenario

            first = await service.get_scenario_async(scenario_name="test.scenario")
            second = await service.get_scenario_async(scenario_name="test.scenario")

        assert first is not None
        assert second is not None
        assert second.default_run_size == estimate
        assert service._registry.create_instance.call_count == 2

    async def test_estimate_cache_is_version_aware_and_bounded(self) -> None:
        """Scenario version changes invalidate estimates and the LRU stays bounded."""
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Default sweep", count=1)],
        )
        scenario = MagicMock()
        scenario.get_default_run_size_estimate_async = AsyncMock(return_value=estimate)

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch("pyrit.backend.services.scenario_service._ESTIMATE_CACHE_SIZE", 1),
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.create_instance.return_value = scenario

            await service._get_default_run_size_estimate_async(metadata=_make_scenario_metadata(scenario_version=1))
            await service._get_default_run_size_estimate_async(metadata=_make_scenario_metadata(scenario_version=2))

        assert service._registry.create_instance.call_count == 2
        assert list(service._estimate_cache) == [("test.scenario", 2)]

    async def test_list_scenarios_preserves_disabled_baseline_policy(self) -> None:
        metadata = _make_scenario_metadata(
            baseline_policy="disabled",
            include_baseline_by_default=False,
        )

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

        assert result.items[0].baseline_policy == "disabled"
        assert result.items[0].include_baseline_by_default is False

    async def test_list_scenarios_paginates_with_limit(self) -> None:
        """Test that list respects the limit parameter."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(5)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=3)

            assert len(result.items) == 3
            assert result.pagination.has_more is True
            assert result.pagination.next_cursor == "test.scenario_2"
            assert [item.scenario_name for item in result.items] == [
                "test.scenario_0",
                "test.scenario_1",
                "test.scenario_2",
            ]

    async def test_list_scenarios_paginates_with_cursor(self) -> None:
        """Test that list uses cursor for pagination."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(5)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=2, cursor="test.scenario_1")

            assert len(result.items) == 2
            assert result.items[0].scenario_name == "test.scenario_2"
            assert result.items[1].scenario_name == "test.scenario_3"
            assert result.pagination.has_more is True

    async def test_list_scenarios_last_page_has_more_false(self) -> None:
        """Test that last page shows has_more=False."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(3)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=5)

            assert len(result.items) == 3
            assert result.pagination.has_more is False
            assert result.pagination.next_cursor is None


class TestScenarioServiceGetScenario:
    """Tests for ScenarioService.get_scenario_async."""

    async def test_configured_estimate_uses_shared_launch_resolution(self) -> None:
        """Configured estimates pass typed selections and parameters into the registry lifecycle."""
        metadata = _make_scenario_metadata(registry_name="airt.jailbreak")
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=12,
            components=[ScenarioRunSizeComponent(label="Configured Jailbreak", count=12)],
        )
        introspection_instance = MagicMock()
        introspection_instance._technique_class = _EstimateTechnique
        introspection_instance._default_dataset_config = DatasetAttackConfiguration(dataset_names=["harmbench"])
        scenario_class = MagicMock(return_value=introspection_instance)
        objective_target = MagicMock()

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch.object(
                ScenarioConfigurationResolver, "resolve_target", return_value=objective_target
            ) as resolve_target,
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata
            service._registry.get_class.return_value = scenario_class
            service._registry.create_and_estimate_async = AsyncMock(return_value=estimate)

            result = await service.estimate_scenario_run_size_async(
                scenario_name="airt.jailbreak",
                request=ScenarioRunSizeEstimateRequest(
                    target_name="preview_target",
                    techniques=["prompt_sending"],
                    dataset_names=["harmbench"],
                    max_dataset_size=3,
                    dataset_filters={"harm_categories": ["violence"]},
                    include_baseline=True,
                    scenario_params={
                        "num_jailbreaks": 2,
                        "num_jailbreak_attempts": 1,
                    },
                ),
            )

        assert result == estimate
        resolve_target.assert_called_once_with(target_name="preview_target")
        call = service._registry.create_and_estimate_async.await_args
        assert call.args == ()
        assert call.kwargs["name"] == "airt.jailbreak"
        assert call.kwargs["scenario_params"] == {
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
        assert call.kwargs["scenario_techniques"] == [_EstimateTechnique.PROMPT_SENDING]
        assert call.kwargs["include_baseline"] is True
        assert call.kwargs["objective_target"] is objective_target
        dataset_config = call.kwargs["dataset_config"]
        assert type(dataset_config) is DatasetAttackConfiguration
        assert dataset_config.dataset_names == ["harmbench"]
        assert dataset_config.max_dataset_size == 3
        assert dataset_config.filters == {"harm_categories": ["violence"]}

    async def test_configured_estimate_rejects_incompatible_v4_jailbreak_technique(self) -> None:
        """Request previews reject techniques omitted by Jailbreak's v4 compatibility policy."""
        metadata = _make_scenario_metadata(registry_name="airt.jailbreak")
        introspection_instance = MagicMock()
        introspection_instance._technique_class = _EstimateTechnique
        introspection_instance._default_dataset_config = DatasetAttackConfiguration(dataset_names=["harmbench"])
        scenario_class = MagicMock(return_value=introspection_instance)

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata
            service._registry.get_class.return_value = scenario_class
            service._registry.create_and_estimate_async = AsyncMock()

            with pytest.raises(ValueError, match="context_compliance"):
                await service.estimate_scenario_run_size_async(
                    scenario_name="airt.jailbreak",
                    request=ScenarioRunSizeEstimateRequest(techniques=["context_compliance"]),
                )

        service._registry.create_and_estimate_async.assert_not_awaited()

    async def test_configured_estimate_without_target_does_not_resolve_or_send_to_target(self) -> None:
        """Target-conditional previews stay side-effect free when no target is configured."""
        metadata = _make_scenario_metadata(registry_name="adaptive.text")
        estimate = ScenarioRunSizeEstimate(
            note="Target compatibility is unknown.",
        )
        introspection_instance = MagicMock()
        introspection_instance._technique_class = _EstimateTechnique
        introspection_instance._default_dataset_config = DatasetAttackConfiguration(dataset_names=["harmbench"])
        scenario_class = MagicMock(return_value=introspection_instance)

        with (
            patch.object(ScenarioService, "__init__", lambda self: None),
            patch.object(ScenarioConfigurationResolver, "resolve_target") as resolve_target,
        ):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata
            service._registry.get_class.return_value = scenario_class
            service._registry.create_and_estimate_async = AsyncMock(return_value=estimate)

            result = await service.estimate_scenario_run_size_async(
                scenario_name="adaptive.text",
                request=ScenarioRunSizeEstimateRequest(),
            )

        assert result == estimate
        resolve_target.assert_not_called()
        call = service._registry.create_and_estimate_async.await_args
        assert "target_is_configured" not in call.kwargs
        assert "objective_target" not in call.kwargs

    async def test_get_scenario_returns_matching_scenario(self) -> None:
        """Test that get returns the matching scenario."""
        metadata = _make_scenario_metadata(registry_name="foundry.red_team_agent")

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = metadata

            result = await service.get_scenario_async(scenario_name="foundry.red_team_agent")

            assert result is not None
            assert result.scenario_name == "foundry.red_team_agent"

    async def test_get_scenario_returns_none_for_missing(self) -> None:
        """Test that get returns None when scenario not found."""
        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_registered_class_metadata.return_value = None

            result = await service.get_scenario_async(scenario_name="nonexistent")

            assert result is None


# ============================================================================
# Route Tests
# ============================================================================


class TestScenarioRoutes:
    """Tests for scenario API routes."""

    def test_list_scenarios_returns_200(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog returns 200."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ListRegisteredScenariosResponse(
                    items=[],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []
            assert data["pagination"]["has_more"] is False

    def test_list_scenarios_with_items(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog returns scenario data."""
        summary = RegisteredScenario(
            scenario_name="foundry.red_team_agent",
            scenario_type="RedTeamAgentScenario",
            description="Red team agent testing",
            description_markdown='<script>alert("untrusted")</script>',
            default_technique="default",
            aggregate_techniques=["all", "default"],
            aggregate_technique_expansions={
                "all": ["role_play", "many_shot"],
                "default": ["role_play"],
            },
            all_techniques=["role_play", "many_shot"],
            default_datasets=["airt_hate"],
            default_run_size=ScenarioRunSizeEstimate(
                datasets=[
                    ScenarioDatasetSummary(
                        name="airt_hate",
                        logical_seed_group_count=4,
                        selected_seed_group_count=4,
                        configured_caps=[
                            ScenarioDatasetSizeCap(
                                label="per-dataset cap",
                                count=4,
                                configured_on="dataset",
                                dataset_name="airt_hate",
                            )
                        ],
                    )
                ]
            ),
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ListRegisteredScenariosResponse(
                    items=[summary],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["scenario_name"] == "foundry.red_team_agent"
            assert item["scenario_type"] == "RedTeamAgentScenario"
            assert item["description_markdown"] == '<script>alert("untrusted")</script>'
            assert item["default_technique"] == "default"
            assert item["aggregate_techniques"] == ["all", "default"]
            assert item["aggregate_technique_expansions"]["default"] == ["role_play"]
            assert item["all_techniques"] == ["role_play", "many_shot"]
            assert item["default_datasets"] == ["airt_hate"]
            assert item["default_run_size"]["datasets"][0]["configured_caps"][0]["count"] == 4
            assert "default_dataset_summaries" not in item
            assert "default_run_size_pending" not in item

    def test_list_scenarios_passes_pagination_params(self, client: TestClient) -> None:
        """Test that pagination params are forwarded to service."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ListRegisteredScenariosResponse(
                    items=[],
                    pagination=PaginationInfo(limit=10, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog?limit=10&cursor=test.scenario_1")

            assert response.status_code == status.HTTP_200_OK
            mock_service.list_scenarios_async.assert_called_once_with(
                limit=10,
                cursor="test.scenario_1",
                include_estimates=True,
            )

    def test_list_scenarios_can_skip_estimates(self, client: TestClient) -> None:
        """The catalog route forwards metadata-only requests to the service."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ListRegisteredScenariosResponse(
                    items=[],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog?include_estimates=false")

        assert response.status_code == status.HTTP_200_OK
        mock_service.list_scenarios_async.assert_awaited_once_with(
            limit=50,
            cursor=None,
            include_estimates=False,
        )

    def test_get_scenario_returns_200(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog/{name} returns 200 when found."""
        summary = RegisteredScenario(
            scenario_name="foundry.red_team_agent",
            scenario_type="RedTeamAgentScenario",
            description="Red team agent testing",
            default_technique="default",
            default_techniques=["role_play"],
            aggregate_techniques=["all"],
            all_techniques=["role_play"],
            default_datasets=["airt_hate"],
            default_run_size=ScenarioRunSizeEstimate(
                estimated_attack_count=8,
                components=[
                    ScenarioRunSizeComponent(
                        label="Default technique sweep",
                        count=8,
                    )
                ],
            ),
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog/foundry.red_team_agent")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["scenario_name"] == "foundry.red_team_agent"
            assert data["default_techniques"] == ["role_play"]
            assert data["default_run_size"]["estimated_attack_count"] == 8

    def test_get_scenario_returns_404_when_not_found(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog/{name} returns 404 when not found."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_estimate_scenario_returns_configured_projection(self, client: TestClient) -> None:
        """Configured estimation returns the exact projection without touching run scheduling."""
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=12,
            components=[ScenarioRunSizeComponent(label="Configured Jailbreak", count=12)],
        )
        with (
            patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service,
            patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get_run_service,
        ):
            mock_service = MagicMock()
            mock_service.estimate_scenario_run_size_async = AsyncMock(return_value=estimate)
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/scenarios/catalog/airt.jailbreak/estimate",
                json={
                    "techniques": ["prompt_sending"],
                    "include_baseline": False,
                    "scenario_params": {
                        "num_jailbreaks": 2,
                        "num_jailbreak_attempts": 1,
                    },
                },
            )

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        assert payload["status"] == "exact"
        assert payload["total_attack_count"] == 12
        assert payload["estimated_attack_count"] == 12
        assert payload["minimum_attack_count"] == 12
        assert payload["maximum_attack_count"] == 12
        assert payload["components"][0]["factors"] == []
        request = mock_service.estimate_scenario_run_size_async.await_args.kwargs["request"]
        assert request.techniques == ["prompt_sending"]
        assert request.include_baseline is False
        assert request.scenario_params == {
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
        mock_get_run_service.assert_not_called()

    async def test_estimate_scenario_supports_direct_keyword_call(self) -> None:
        """The FastAPI handler remains directly callable through its keyword-only API."""
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Configured estimate", count=1)],
        )
        request = ScenarioRunSizeEstimateRequest()
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.estimate_scenario_run_size_async = AsyncMock(return_value=estimate)
            mock_get_service.return_value = mock_service

            result = await estimate_scenario_run_size(
                scenario_name="test.scenario",
                request=request,
            )

        assert result == estimate
        mock_service.estimate_scenario_run_size_async.assert_awaited_once_with(
            scenario_name="test.scenario",
            request=request,
        )

    def test_estimate_scenario_returns_400_for_invalid_configuration(self, client: TestClient) -> None:
        """Configured estimate validation errors become clear client errors."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.estimate_scenario_run_size_async = AsyncMock(
                side_effect=ValueError("Technique 'unknown' not found")
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/scenarios/catalog/airt.jailbreak/estimate",
                json={"techniques": ["unknown"]},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Technique 'unknown' not found" in response.json()["detail"]

    def test_estimate_scenario_returns_404_for_unknown_scenario(self, client: TestClient) -> None:
        """Unknown configured estimates preserve the catalog not-found contract."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.estimate_scenario_run_size_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.post("/api/scenarios/catalog/missing.scenario/estimate", json={})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "missing.scenario" in response.json()["detail"]

    def test_get_scenario_with_dotted_name(self, client: TestClient) -> None:
        """Test that dotted scenario names (e.g., 'foundry.red_team_agent') work in path."""
        summary = RegisteredScenario(
            scenario_name="garak.encoding",
            scenario_type="EncodingScenario",
            description="Encoding scenario",
            default_technique="all",
            aggregate_techniques=["all"],
            all_techniques=["base64", "rot13"],
            default_datasets=[],
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog/garak.encoding")

            assert response.status_code == status.HTTP_200_OK
            mock_service.get_scenario_async.assert_called_once_with(scenario_name="garak.encoding")


# ============================================================================
# Supported Parameters Tests
# ============================================================================


class TestScenarioServiceSupportedParameters:
    """Tests for supported_parameters in scenario service responses."""

    async def test_list_scenarios_includes_supported_parameters(self) -> None:
        """Test that supported_parameters are included in scenario listing."""
        metadata = _make_scenario_metadata(registry_name="param.scenario")
        metadata = ScenarioMetadata(
            registry_name="param.scenario",
            class_name="ParamScenario",
            class_module="pyrit.scenario.scenarios.param",
            class_description="A scenario with params",
            default_technique="default",
            all_techniques=("role_play",),
            aggregate_techniques=("all",),
            default_datasets=("test_dataset",),
            supported_parameters=(
                Parameter(
                    name="max_turns",
                    description="Maximum number of turns",
                    default=5,
                    param_type=int,
                ),
                Parameter(
                    name="mode",
                    description="Execution mode",
                    default="fast",
                    param_type=Literal["fast", "slow"],
                ),
            ),
        )

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            assert len(result.items) == 1
            params = result.items[0].supported_parameters
            assert len(params) == 2

            assert params[0].name == "max_turns"
            assert params[0].description == "Maximum number of turns"
            assert params[0].model_dump()["default"] == "5"
            assert params[0].type_name == "int"
            assert params[0].choices is None
            assert params[0].is_list is False

            assert params[1].name == "mode"
            assert params[1].description == "Execution mode"
            assert params[1].model_dump()["default"] == "fast"
            assert params[1].type_name == "str"
            assert params[1].choices == ["fast", "slow"]
            assert params[1].is_list is False

    async def test_scenario_with_no_parameters_has_empty_list(self) -> None:
        """Test that scenarios without parameters have empty supported_parameters."""
        metadata = _make_scenario_metadata()

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            assert result.items[0].supported_parameters == []

    async def test_supported_parameters_with_none_default(self) -> None:
        """Test that parameters with None default are serialized correctly."""
        metadata = ScenarioMetadata(
            registry_name="test.scenario",
            class_name="TestScenario",
            class_module="pyrit.scenario.scenarios.test",
            class_description="Test",
            default_technique="default",
            all_techniques=("all",),
            aggregate_techniques=("all",),
            default_datasets=(),
            supported_parameters=(
                Parameter(
                    name="optional_param",
                    description="An optional param",
                    default=None,
                    param_type=str,
                ),
            ),
        )

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            param = result.items[0].supported_parameters[0]
            assert param.default is None
            assert param.model_dump()["default"] is None
