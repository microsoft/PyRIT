# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend scenario service and routes.
"""

import asyncio
from collections import OrderedDict
from typing import Literal
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
from pyrit.registry import ScenarioMetadata
from pyrit.scenario.core import DatasetAttackConfiguration, ScenarioTechnique


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
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


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
    )


# ============================================================================
# ScenarioService Unit Tests
# ============================================================================


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
            assert [call.args[0] for call in service._registry.create_instance.call_args_list] == [
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
        """POST catalog estimate forwards request fields and returns the structured estimate."""
        estimate = ScenarioRunSizeEstimate(
            estimated_attack_count=12,
            components=[ScenarioRunSizeComponent(label="Configured Jailbreak", count=12)],
        )
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.estimate_scenario_run_size_async = AsyncMock(return_value=estimate)
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/scenarios/catalog/airt.jailbreak/estimate",
                json={
                    "techniques": ["prompt_sending"],
                    "include_baseline": True,
                    "scenario_params": {
                        "num_jailbreaks": 2,
                        "num_jailbreak_attempts": 1,
                    },
                },
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["estimated_attack_count"] == 12
        request = mock_service.estimate_scenario_run_size_async.await_args.kwargs["request"]
        assert request.techniques == ["prompt_sending"]
        assert request.include_baseline is True
        assert request.scenario_params == {
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }

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
