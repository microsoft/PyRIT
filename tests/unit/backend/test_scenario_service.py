# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend scenario service and routes.
"""

from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ListRegisteredScenariosResponse
from pyrit.backend.services.scenario_service import (
    ScenarioService,
    get_scenario_service,
)
from pyrit.models import Parameter
from pyrit.models.catalog.scenario import RegisteredScenario
from pyrit.registry import ScenarioMetadata


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
    description: str = "A test scenario",
    default_strategy: str = "default",
    all_strategies: tuple[str, ...] = ("role_play", "many_shot"),
    aggregate_strategies: tuple[str, ...] = ("all", "default"),
    default_datasets: tuple[str, ...] = ("test_dataset",),
) -> ScenarioMetadata:
    """Create a ScenarioMetadata instance for testing."""
    return ScenarioMetadata(
        registry_name=registry_name,
        class_name=class_name,
        class_module="pyrit.scenario.scenarios.test",
        class_description=description,
        default_strategy=default_strategy,
        all_strategies=all_strategies,
        aggregate_strategies=aggregate_strategies,
        default_datasets=default_datasets,
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
            assert result.items[0].default_strategy == "default"
            assert result.items[0].aggregate_strategies == ["all", "default"]
            assert result.items[0].all_strategies == ["role_play", "many_shot"]
            assert result.items[0].default_datasets == ["test_dataset"]

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
            default_strategy="default",
            aggregate_strategies=["all", "default"],
            all_strategies=["role_play", "many_shot"],
            default_datasets=["airt_hate"],
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
            assert item["default_strategy"] == "default"
            assert item["aggregate_strategies"] == ["all", "default"]
            assert item["all_strategies"] == ["role_play", "many_shot"]
            assert item["default_datasets"] == ["airt_hate"]

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
            mock_service.list_scenarios_async.assert_called_once_with(limit=10, cursor="test.scenario_1")

    def test_get_scenario_returns_200(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog/{name} returns 200 when found."""
        summary = RegisteredScenario(
            scenario_name="foundry.red_team_agent",
            scenario_type="RedTeamAgentScenario",
            description="Red team agent testing",
            default_strategy="default",
            aggregate_strategies=["all"],
            all_strategies=["role_play"],
            default_datasets=["airt_hate"],
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog/foundry.red_team_agent")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["scenario_name"] == "foundry.red_team_agent"

    def test_get_scenario_returns_404_when_not_found(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/catalog/{name} returns 404 when not found."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/catalog/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_scenario_with_dotted_name(self, client: TestClient) -> None:
        """Test that dotted scenario names (e.g., 'foundry.red_team_agent') work in path."""
        summary = RegisteredScenario(
            scenario_name="garak.encoding",
            scenario_type="EncodingScenario",
            description="Encoding scenario",
            default_strategy="all",
            aggregate_strategies=["all"],
            all_strategies=["base64", "rot13"],
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
            default_strategy="default",
            all_strategies=("role_play",),
            aggregate_strategies=("all",),
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
            default_strategy="default",
            all_strategies=("all",),
            aggregate_strategies=("all",),
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
