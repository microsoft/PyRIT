# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend scenario service and routes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ScenarioListResponse, ScenarioSummary
from pyrit.backend.services.scenario_service import ScenarioService, get_scenario_service
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
    all_strategies: tuple[str, ...] = ("prompt_sending", "role_play"),
    aggregate_strategies: tuple[str, ...] = ("all", "default"),
    default_datasets: tuple[str, ...] = ("test_dataset",),
    max_dataset_size: int | None = None,
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
        max_dataset_size=max_dataset_size,
    )


# ============================================================================
# ScenarioService Unit Tests
# ============================================================================


class TestScenarioServiceListScenarios:
    """Tests for ScenarioService.list_scenarios_async."""

    @pytest.mark.asyncio
    async def test_list_scenarios_returns_empty_when_no_scenarios(self) -> None:
        """Test that list returns empty list when no scenarios are registered."""
        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = []

            result = await service.list_scenarios_async()

            assert result.items == []
            assert result.pagination.has_more is False

    @pytest.mark.asyncio
    async def test_list_scenarios_returns_scenarios_from_registry(self) -> None:
        """Test that list returns scenarios from registry."""
        metadata = _make_scenario_metadata()

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            assert len(result.items) == 1
            assert result.items[0].scenario_name == "test.scenario"
            assert result.items[0].scenario_type == "TestScenario"
            assert result.items[0].description == "A test scenario"
            assert result.items[0].default_strategy == "default"
            assert result.items[0].aggregate_strategies == ["all", "default"]
            assert result.items[0].all_strategies == ["prompt_sending", "role_play"]
            assert result.items[0].default_datasets == ["test_dataset"]
            assert result.items[0].max_dataset_size is None

    @pytest.mark.asyncio
    async def test_list_scenarios_paginates_with_limit(self) -> None:
        """Test that list respects the limit parameter."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(5)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=3)

            assert len(result.items) == 3
            assert result.pagination.has_more is True
            assert result.pagination.next_cursor == "test.scenario_2"

    @pytest.mark.asyncio
    async def test_list_scenarios_paginates_with_cursor(self) -> None:
        """Test that list uses cursor for pagination."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(5)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=2, cursor="test.scenario_1")

            assert len(result.items) == 2
            assert result.items[0].scenario_name == "test.scenario_2"
            assert result.items[1].scenario_name == "test.scenario_3"
            assert result.pagination.has_more is True

    @pytest.mark.asyncio
    async def test_list_scenarios_last_page_has_more_false(self) -> None:
        """Test that last page shows has_more=False."""
        metadata_list = [
            _make_scenario_metadata(registry_name=f"test.scenario_{i}", class_name=f"Scenario{i}") for i in range(3)
        ]

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_scenarios_async(limit=5)

            assert len(result.items) == 3
            assert result.pagination.has_more is False
            assert result.pagination.next_cursor is None

    @pytest.mark.asyncio
    async def test_list_scenarios_includes_max_dataset_size(self) -> None:
        """Test that max_dataset_size is included in response."""
        metadata = _make_scenario_metadata(max_dataset_size=10)

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.list_scenarios_async()

            assert result.items[0].max_dataset_size == 10


class TestScenarioServiceGetScenario:
    """Tests for ScenarioService.get_scenario_async."""

    @pytest.mark.asyncio
    async def test_get_scenario_returns_matching_scenario(self) -> None:
        """Test that get returns the matching scenario."""
        metadata = _make_scenario_metadata(registry_name="foundry.red_team_agent")

        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.get_scenario_async(scenario_name="foundry.red_team_agent")

            assert result is not None
            assert result.scenario_name == "foundry.red_team_agent"

    @pytest.mark.asyncio
    async def test_get_scenario_returns_none_for_missing(self) -> None:
        """Test that get returns None when scenario not found."""
        with patch.object(ScenarioService, "__init__", lambda self: None):
            service = ScenarioService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = []

            result = await service.get_scenario_async(scenario_name="nonexistent")

            assert result is None


# ============================================================================
# Route Tests
# ============================================================================


class TestScenarioRoutes:
    """Tests for scenario API routes."""

    def test_list_scenarios_returns_200(self, client: TestClient) -> None:
        """Test that GET /api/scenarios returns 200."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ScenarioListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []
            assert data["pagination"]["has_more"] is False

    def test_list_scenarios_with_items(self, client: TestClient) -> None:
        """Test that GET /api/scenarios returns scenario data."""
        summary = ScenarioSummary(
            scenario_name="foundry.red_team_agent",
            scenario_type="RedTeamAgentScenario",
            description="Red team agent testing",
            default_strategy="default",
            aggregate_strategies=["all", "default"],
            all_strategies=["prompt_sending", "role_play"],
            default_datasets=["airt_hate"],
            max_dataset_size=10,
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ScenarioListResponse(
                    items=[summary],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["scenario_name"] == "foundry.red_team_agent"
            assert item["scenario_type"] == "RedTeamAgentScenario"
            assert item["default_strategy"] == "default"
            assert item["aggregate_strategies"] == ["all", "default"]
            assert item["all_strategies"] == ["prompt_sending", "role_play"]
            assert item["default_datasets"] == ["airt_hate"]
            assert item["max_dataset_size"] == 10

    def test_list_scenarios_passes_pagination_params(self, client: TestClient) -> None:
        """Test that pagination params are forwarded to service."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_scenarios_async = AsyncMock(
                return_value=ScenarioListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=10, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios?limit=10&cursor=test.scenario_1")

            assert response.status_code == status.HTTP_200_OK
            mock_service.list_scenarios_async.assert_called_once_with(limit=10, cursor="test.scenario_1")

    def test_get_scenario_returns_200(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/{name} returns 200 when found."""
        summary = ScenarioSummary(
            scenario_name="foundry.red_team_agent",
            scenario_type="RedTeamAgentScenario",
            description="Red team agent testing",
            default_strategy="default",
            aggregate_strategies=["all"],
            all_strategies=["prompt_sending"],
            default_datasets=["airt_hate"],
            max_dataset_size=None,
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/foundry.red_team_agent")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["scenario_name"] == "foundry.red_team_agent"

    def test_get_scenario_returns_404_when_not_found(self, client: TestClient) -> None:
        """Test that GET /api/scenarios/{name} returns 404 when not found."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_scenario_with_dotted_name(self, client: TestClient) -> None:
        """Test that dotted scenario names (e.g., 'foundry.red_team_agent') work in path."""
        summary = ScenarioSummary(
            scenario_name="garak.encoding",
            scenario_type="EncodingScenario",
            description="Encoding scenario",
            default_strategy="all",
            aggregate_strategies=["all"],
            all_strategies=["base64", "rot13"],
            default_datasets=[],
            max_dataset_size=None,
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_scenario_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/scenarios/garak.encoding")

            assert response.status_code == status.HTTP_200_OK
            mock_service.get_scenario_async.assert_called_once_with(scenario_name="garak.encoding")
