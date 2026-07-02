# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for scenario run API routes.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import pyrit.backend.services.scenario_run_service as _svc_mod
from pyrit.backend.main import app
from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.models import ScenarioRunState
from pyrit.models.catalog.scenario import ScenarioRunSummary


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_service_cache():
    """Clear the service singleton between tests."""
    _svc_mod._service_instance = None
    yield
    _svc_mod._service_instance = None


def _mock_run_response(
    *,
    run_id: str = "test-run-id",
    scenario_name: str = "foundry.red_team_agent",
    run_status: ScenarioRunState = ScenarioRunState.CREATED,
) -> ScenarioRunSummary:
    """Create a mock ScenarioRunResponse."""
    return ScenarioRunSummary(
        scenario_result_id=run_id,
        scenario_name=scenario_name,
        status=run_status,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        error=None,
    )


class TestStartScenarioRunRoute:
    """Tests for POST /api/scenarios/runs."""

    def test_start_run_returns_202(self, client: TestClient) -> None:
        """Test that a valid request returns 202 Accepted."""
        mock_response = _mock_run_response()

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.start_run_async = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            response = client.post(
                "/api/scenarios/runs",
                json={"scenario_name": "foundry.red_team_agent", "target_name": "my_target"},
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["scenario_result_id"] == "test-run-id"
        assert data["status"] == "CREATED"

    def test_start_run_invalid_scenario_returns_400(self, client: TestClient) -> None:
        """Test that an invalid scenario returns 400."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.start_run_async = AsyncMock(side_effect=ValueError("'bad.scenario' not found in registry."))
            mock_get.return_value = mock_service

            response = client.post(
                "/api/scenarios/runs",
                json={"scenario_name": "bad.scenario", "target_name": "my_target"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not found" in response.json()["detail"]

    def test_start_run_missing_required_fields_returns_422(self, client: TestClient) -> None:
        """Test that missing required fields returns 422."""
        response = client.post("/api/scenarios/runs", json={})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_start_run_with_all_options(self, client: TestClient) -> None:
        """Test that all optional fields are accepted."""
        mock_response = _mock_run_response()

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.start_run_async = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            response = client.post(
                "/api/scenarios/runs",
                json={
                    "scenario_name": "foundry.red_team_agent",
                    "target_name": "my_target",
                    "initializers": ["target", "load_default_datasets"],
                    "strategies": ["base64", "rot13"],
                    "dataset_names": ["harmful_content"],
                    "max_dataset_size": 50,
                    "max_concurrency": 5,
                    "max_retries": 2,
                    "memory_labels": {"team": "red"},
                    "scenario_params": {"max_turns": 10, "threshold": 0.8},
                    "initializer_args": {"target": {"endpoint": "https://example.com"}},
                },
            )

        assert response.status_code == status.HTTP_202_ACCEPTED


class TestListScenarioRunsRoute:
    """Tests for GET /api/scenarios/runs."""

    def test_list_runs_returns_200(self, client: TestClient) -> None:
        """Test that list runs returns 200 with empty list."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.list_runs.return_value = ScenarioRunListResponse(items=[])
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"] == []

    def test_list_runs_returns_multiple_runs(self, client: TestClient) -> None:
        """Test that list runs returns all tracked runs."""
        runs = [
            _mock_run_response(run_id="run-1"),
            _mock_run_response(run_id="run-2", run_status=ScenarioRunState.IN_PROGRESS),
        ]

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.list_runs.return_value = ScenarioRunListResponse(items=runs)
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["items"]) == 2


class TestGetScenarioRunRoute:
    """Tests for GET /api/scenarios/runs/{id}."""

    def test_get_run_returns_200(self, client: TestClient) -> None:
        """Test that getting an existing run returns 200."""
        mock_response = _mock_run_response(run_status=ScenarioRunState.IN_PROGRESS)

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_run.return_value = mock_response
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "IN_PROGRESS"

    def test_get_run_not_found_returns_404(self, client: TestClient) -> None:
        """Test that getting a non-existent run returns 404."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_run.return_value = None
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/nonexistent")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCancelScenarioRunRoute:
    """Tests for POST /api/scenarios/runs/{id}/cancel."""

    def test_cancel_run_returns_200(self, client: TestClient) -> None:
        """Test that cancelling a running scenario returns 200."""
        mock_response = _mock_run_response(run_status=ScenarioRunState.CANCELLED)

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.cancel_run_async = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            response = client.post("/api/scenarios/runs/test-run-id/cancel")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "CANCELLED"

    def test_cancel_run_not_found_returns_404(self, client: TestClient) -> None:
        """Test that cancelling a non-existent run returns 404."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.cancel_run_async = AsyncMock(return_value=None)
            mock_get.return_value = mock_service

            response = client.post("/api/scenarios/runs/nonexistent/cancel")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cancel_completed_run_returns_409(self, client: TestClient) -> None:
        """Test that cancelling a completed run returns 409 Conflict."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.cancel_run_async = AsyncMock(side_effect=ValueError("Cannot cancel run in 'completed' state."))
            mock_get.return_value = mock_service

            response = client.post("/api/scenarios/runs/test-run-id/cancel")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "Cannot cancel" in response.json()["detail"]


class TestGetScenarioRunResultsRoute:
    """Tests for GET /api/scenarios/runs/{id}/results."""

    def test_get_results_returns_200(self, client: TestClient) -> None:
        """Test that getting results of a completed run returns 200."""
        from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, ScenarioIdentifier, ScenarioResult

        attack = AttackResult(
            conversation_id="conv-1",
            objective="Extract sensitive info",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            execution_time_ms=100,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        scenario_result = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name="foundry.red_team_agent", description="Foundry red-team agent"),
            objective_target_identifier=ComponentIdentifier.from_dict(
                {"__type__": "FakeTarget", "__module__": "test.mod", "params": {}}
            ),
            objective_scorer_identifier=None,
            attack_results={"base64_attack": [attack]},
            scenario_run_state="COMPLETED",
        )

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_run_results.return_value = scenario_result
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id/results")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["scenario_identifier"]["name"] == "foundry.red_team_agent"
        assert "base64_attack" in data["attack_results"]

    def test_get_results_not_found_returns_404(self, client: TestClient) -> None:
        """Test that getting results of a non-existent run returns 404."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_run_results.return_value = None
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/nonexistent/results")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_results_not_completed_returns_409(self, client: TestClient) -> None:
        """Test that getting results of a non-completed run returns 409."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_run_results.side_effect = ValueError(
                "Results are only available for completed runs. Current status: 'running'."
            )
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id/results")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "only available for completed runs" in response.json()["detail"]
