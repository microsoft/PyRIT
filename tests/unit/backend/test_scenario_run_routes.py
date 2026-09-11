# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for scenario run API routes.
"""

from datetime import datetime, timezone
from threading import get_ident
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import pyrit.backend.services.scenario_run_service as _svc_mod
from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ScenarioRunListResponse
from pyrit.backend.routes.scenarios import get_scenario_run_progress, list_scenario_runs
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioProgressCounts,
    ScenarioProgressHeader,
    ScenarioProgressSummary,
    ScenarioRunPlan,
    ScenarioRunProgress,
    ScenarioRunState,
)
from pyrit.models.catalog.scenario import ScenarioRunListItem, ScenarioRunSummary
from unit.mocks import make_scenario_result


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
                    "techniques": ["base64", "rot13"],
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

    def test_start_jailbreak_run_preserves_explicit_selection_and_params(self, client: TestClient) -> None:
        """The route parses the exact Jailbreak selection without adding catalog defaults."""
        mock_response = _mock_run_response()

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.start_run_async = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            response = client.post(
                "/api/scenarios/runs",
                json={
                    "scenario_name": "airt.jailbreak",
                    "target_name": "my_target",
                    "techniques": ["prompt_sending"],
                    "include_baseline": False,
                    "scenario_params": {
                        "num_jailbreaks": 2,
                        "num_jailbreak_attempts": 1,
                    },
                },
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        request = mock_service.start_run_async.await_args.kwargs["request"]
        assert request.techniques == ["prompt_sending"]
        assert request.include_baseline is False
        assert request.scenario_params == {
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }


class TestListScenarioRunsRoute:
    """Tests for GET /api/scenarios/runs."""

    def test_list_runs_returns_200(self, client: TestClient) -> None:
        """Test that list runs returns 200 with empty list."""
        route_thread: list[int] = []
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.list_runs.side_effect = lambda **_: (
                route_thread.append(get_ident())
                or ScenarioRunListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=100, has_more=False),
                )
            )
            mock_get.return_value = mock_service

            request_thread = get_ident()
            response = client.get("/api/scenarios/runs")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"] == []
        assert route_thread[0] != request_thread

    def test_list_runs_rejects_unbounded_limit(self, client: TestClient) -> None:
        response = client.get("/api/scenarios/runs?limit=101")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_list_runs_requires_keyword_arguments(self) -> None:
        """Test that route parameters cannot be passed positionally."""
        with pytest.raises(TypeError, match="positional"):
            await list_scenario_runs(None, None, None, 100, None)

    def test_list_runs_returns_multiple_runs(self, client: TestClient) -> None:
        """Test that list runs returns all tracked runs."""
        runs = [
            ScenarioRunListItem.model_validate(_mock_run_response(run_id="run-1").model_dump()),
            ScenarioRunListItem.model_validate(
                _mock_run_response(run_id="run-2", run_status=ScenarioRunState.IN_PROGRESS).model_dump()
            ),
        ]

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.list_runs.return_value = ScenarioRunListResponse(
                items=runs,
                pagination=PaginationInfo(limit=100, has_more=False),
            )
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["items"]) == 2

    def test_list_runs_passes_repeated_filters_and_labels(self, client: TestClient) -> None:
        """Test that history query parameters preserve repeated values."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.list_runs.return_value = ScenarioRunListResponse(
                items=[],
                pagination=PaginationInfo(limit=10, has_more=False),
            )
            mock_get.return_value = mock_service

            response = client.get(
                "/api/scenarios/runs"
                "?scenario_names=first&scenario_names=second"
                "&run_statuses=IN_PROGRESS&run_statuses=FAILED"
                "&label=operator%3Aalice&label=operator%3Abob&label=team%3Asafety"
                "&limit=10&cursor=opaque"
            )

        assert response.status_code == status.HTTP_200_OK
        mock_service.list_runs.assert_called_once_with(
            scenario_names=["first", "second"],
            statuses=[ScenarioRunState.IN_PROGRESS, ScenarioRunState.FAILED],
            labels={"operator": ["alice", "bob"], "team": ["safety"]},
            limit=10,
            cursor="opaque",
        )


class TestGetScenarioRunRoute:
    """Tests for GET /api/scenarios/runs/{id}."""

    def test_get_run_returns_200(self, client: TestClient) -> None:
        """Test that getting an existing run returns 200."""
        mock_response = _mock_run_response(run_status=ScenarioRunState.IN_PROGRESS)

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.snapshot_active_run.return_value = MagicMock(error=None)
            mock_service.get_run_from_storage.return_value = mock_response
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "IN_PROGRESS"

    def test_get_run_not_found_returns_404(self, client: TestClient) -> None:
        """Test that getting a non-existent run returns 404."""
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.snapshot_active_run.return_value = MagicMock(error=None)
            mock_service.get_run_from_storage.return_value = None
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/nonexistent")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_run_with_forward_version_plan_returns_legacy_detail(self, client: TestClient) -> None:
        attack_result = AttackResult(
            conversation_id="conversation-1",
            objective="objective",
            outcome=AttackOutcome.SUCCESS,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            attribution_data={"parent_collection": "legacy attack"},
        )
        db_result = make_scenario_result(
            scenario_name="foundry.red_team_agent",
            attack_results={"legacy attack": [attack_result]},
            metadata={
                SCENARIO_RUN_PLAN_METADATA_KEY: {
                    "version": 2,
                    "atomic_groups": [],
                    "seed_groups": [],
                }
            },
        )
        memory = MagicMock()
        memory.get_scenario_results.return_value = [db_result]
        memory.get_attack_results.return_value = []
        with patch.object(_svc_mod.CentralMemory, "get_memory_instance", return_value=memory):
            service = _svc_mod.ScenarioRunService()

        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=service):
            response = client.get(f"/api/scenarios/runs/{db_result.id}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["planned_total_available"] is False
        assert response.json()["total_attacks"] == 1
        assert response.json()["completed_attacks"] == 1
        assert response.json()["techniques_used"] == ["legacy attack"]

    def test_progress_invalid_cursor_returns_400(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.snapshot_active_run.return_value = MagicMock(active_group_ids=())
            mock_service.get_run_progress_from_storage.side_effect = ValueError("Malformed scenario progress cursor.")
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id/progress?since=bad")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Malformed scenario progress cursor."

    def test_progress_returns_compact_plan_response(self, client: TestClient) -> None:
        progress = ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id="test-run-id",
                scenario_name="TestScenario",
                scenario_registry_name="test.scenario",
                scenario_version=1,
                status=ScenarioRunState.IN_PROGRESS,
                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            plan=ScenarioRunPlan(
                scenario_registry_name="test.scenario",
                atomic_groups=[],
                seed_groups=[],
            ),
            summary=ScenarioProgressSummary(
                overall=ScenarioProgressCounts(
                    completed=0,
                    planned=0,
                    succeeded=0,
                    errors=0,
                    retries=0,
                )
            ),
            plan_complete=True,
        )
        snapshot_thread: list[int] = []
        storage_thread: list[int] = []
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.snapshot_active_run.side_effect = lambda **_: (
                snapshot_thread.append(get_ident()) or MagicMock(active_group_ids=("active-group",))
            )
            mock_service.get_run_progress_from_storage.side_effect = lambda **_: (
                storage_thread.append(get_ident()) or progress
            )
            mock_get.return_value = mock_service

            response = client.get("/api/scenarios/runs/test-run-id/progress?limit=25")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["plan"]["scenario_registry_name"] == "test.scenario"
        mock_service.get_run_progress_from_storage.assert_called_once_with(
            scenario_result_id="test-run-id",
            since=None,
            limit=25,
            active_group_ids=("active-group",),
        )
        assert snapshot_thread[0] != storage_thread[0]

    async def test_progress_supports_direct_keyword_call(self) -> None:
        progress = ScenarioRunProgress(
            run=ScenarioProgressHeader(
                scenario_result_id="test-run-id",
                scenario_name="TestScenario",
                scenario_registry_name="test.scenario",
                scenario_version=1,
                status=ScenarioRunState.IN_PROGRESS,
                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            plan=ScenarioRunPlan(
                scenario_registry_name="test.scenario",
                atomic_groups=[],
                seed_groups=[],
            ),
            summary=ScenarioProgressSummary(
                overall=ScenarioProgressCounts(
                    completed=0,
                    planned=0,
                    succeeded=0,
                    errors=0,
                    retries=0,
                )
            ),
            plan_complete=True,
        )
        with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get:
            mock_service = MagicMock()
            mock_service.snapshot_active_run.return_value = MagicMock(active_group_ids=())
            mock_service.get_run_progress_from_storage.return_value = progress
            mock_get.return_value = mock_service

            result = await get_scenario_run_progress(
                scenario_result_id="test-run-id",
                since=None,
                limit=25,
            )

        assert result == progress
        mock_service.get_run_progress_from_storage.assert_called_once_with(
            scenario_result_id="test-run-id",
            since=None,
            limit=25,
            active_group_ids=(),
        )


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
        from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier

        attack = AttackResult(
            conversation_id="conv-1",
            objective="Extract sensitive info",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            execution_time_ms=100,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        scenario_result = make_scenario_result(
            scenario_name="foundry.red_team_agent",
            scenario_description="Foundry red-team agent",
            objective_target_identifier=ComponentIdentifier.model_validate(
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
        assert data["scenario_name"] == "foundry.red_team_agent"
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
