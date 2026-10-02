# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset routes."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models.scenario_presets import (
    PresetIssue,
    ScenarioPresetListResponse,
    ScenarioPresetResponse,
)
from pyrit.backend.services.scenario_preset_service import (
    ScenarioPresetNotFoundError,
    ScenarioPresetService,
)
from pyrit.models.catalog import ScenarioPreset
from pyrit.registry import ScenarioPresetConflictError

PRESET_NAME = "quick_scan"
SCENARIO_NAME = "foundry.red_team_agent"


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> Iterator[TestClient]:
    """Create a test client with admin authorization satisfied."""
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app, headers=compatibility_headers)
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def anonymous_client(compatibility_headers: dict[str, str]) -> Iterator[TestClient]:
    """Create a test client without admin authorization."""
    yield TestClient(app, headers=compatibility_headers)


@pytest.fixture
def service() -> Iterator[MagicMock]:
    """Patch the preset service the routes resolve."""
    mock_service = MagicMock(spec=ScenarioPresetService)
    with patch(
        "pyrit.backend.routes.scenario_presets.get_scenario_preset_service",
        return_value=mock_service,
    ):
        yield mock_service


def _preset(name: str = PRESET_NAME) -> ScenarioPreset:
    """Build a minimal preset."""
    return ScenarioPreset(name=name, scenario_name=SCENARIO_NAME)


def _response(*, name: str = PRESET_NAME, version: str = "v1", issues: list[PresetIssue] | None = None):
    """Build a preset response envelope."""
    return ScenarioPresetResponse(preset=_preset(name), version=version, issues=issues or [])


class TestListPresets:
    """GET /api/scenario-presets."""

    def test_list_returns_the_source_and_items(self, client: TestClient, service: MagicMock) -> None:
        service.list_presets_async = AsyncMock(
            return_value=ScenarioPresetListResponse(source="/tmp/presets", items=[_response()])
        )

        response = client.get("/api/scenario-presets")

        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "/tmp/presets"
        assert body["items"][0]["preset"]["name"] == PRESET_NAME
        assert body["items"][0]["version"] == "v1"

    def test_list_reports_advisory_issues(self, client: TestClient, service: MagicMock) -> None:
        issue = PresetIssue(field="scenario_name", message="Scenario is not registered in this deployment.")
        service.list_presets_async = AsyncMock(
            return_value=ScenarioPresetListResponse(source="/tmp", items=[_response(issues=[issue])])
        )

        response = client.get("/api/scenario-presets")

        assert response.json()["items"][0]["issues"] == [
            {"field": "scenario_name", "message": "Scenario is not registered in this deployment."}
        ]

    def test_list_is_readable_without_admin(self, anonymous_client: TestClient, service: MagicMock) -> None:
        service.list_presets_async = AsyncMock(return_value=ScenarioPresetListResponse(source="/tmp", items=[]))

        assert anonymous_client.get("/api/scenario-presets").status_code == 200

    def test_storage_failure_is_reported_without_sdk_detail(self, client: TestClient, service: MagicMock) -> None:
        service.list_presets_async = AsyncMock(side_effect=AzureError("container 'x' key=secret"))

        response = client.get("/api/scenario-presets")

        assert response.status_code == 503
        assert "secret" not in response.text


class TestGetPreset:
    """GET /api/scenario-presets/{name}."""

    def test_get_returns_the_preset_and_version(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response())

        response = client.get(f"/api/scenario-presets/{PRESET_NAME}")

        assert response.status_code == 200
        assert response.json()["version"] == "v1"

    def test_get_reports_a_missing_preset(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=None)

        response = client.get("/api/scenario-presets/missing")

        assert response.status_code == 404

    def test_an_illegal_name_is_a_client_error(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(side_effect=ValueError("Invalid registry name 'Bad Name'"))

        response = client.get("/api/scenario-presets/BadName")

        assert response.status_code == 400


class TestCreatePreset:
    """POST /api/scenario-presets."""

    def test_create_returns_201_with_the_new_version(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response(version="v2"))

        response = client.post("/api/scenario-presets", json=_preset().model_dump())

        assert response.status_code == 201
        assert response.json()["version"] == "v2"
        assert service.save_preset_async.call_args.kwargs["expected_version"] is None

    def test_create_over_an_existing_name_is_a_conflict(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(
            side_effect=ScenarioPresetConflictError(name=PRESET_NAME, expected_version=None, actual_version="v1")
        )

        response = client.post("/api/scenario-presets", json=_preset().model_dump())

        assert response.status_code == 409

    def test_create_requires_admin(self, anonymous_client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())

        response = anonymous_client.post("/api/scenario-presets", json=_preset().model_dump())

        assert response.status_code == 403
        service.save_preset_async.assert_not_called()

    def test_an_unknown_field_is_rejected(self, client: TestClient, service: MagicMock) -> None:
        payload = _preset().model_dump()
        payload["tecniques"] = ["crescendo"]

        response = client.post("/api/scenario-presets", json=payload)

        assert response.status_code == 422


class TestUpdatePreset:
    """PUT /api/scenario-presets/{name}."""

    def test_update_passes_the_expected_version_through(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response(version="v2"))

        response = client.put(
            f"/api/scenario-presets/{PRESET_NAME}",
            json={"preset": _preset().model_dump(), "expected_version": "v1"},
        )

        assert response.status_code == 200
        assert service.save_preset_async.call_args.kwargs["expected_version"] == "v1"

    def test_a_body_naming_a_different_preset_is_rejected(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())

        response = client.put(
            "/api/scenario-presets/other_name",
            json={"preset": _preset().model_dump(), "expected_version": "v1"},
        )

        assert response.status_code == 400
        service.save_preset_async.assert_not_called()

    def test_a_stale_version_is_a_conflict(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(
            side_effect=ScenarioPresetConflictError(name=PRESET_NAME, expected_version="v1", actual_version="v2")
        )

        response = client.put(
            f"/api/scenario-presets/{PRESET_NAME}",
            json={"preset": _preset().model_dump(), "expected_version": "v1"},
        )

        assert response.status_code == 409

    def test_an_omitted_version_is_rejected(self, client: TestClient, service: MagicMock) -> None:
        response = client.put(
            f"/api/scenario-presets/{PRESET_NAME}",
            json={"preset": _preset().model_dump()},
        )

        assert response.status_code == 422

    def test_update_requires_admin(self, anonymous_client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())

        response = anonymous_client.put(
            f"/api/scenario-presets/{PRESET_NAME}",
            json={"preset": _preset().model_dump(), "expected_version": "v1"},
        )

        assert response.status_code == 403
        service.save_preset_async.assert_not_called()


class TestDeletePreset:
    """DELETE /api/scenario-presets/{name}."""

    def test_delete_returns_204(self, client: TestClient, service: MagicMock) -> None:
        service.delete_preset_async = AsyncMock(return_value=None)

        response = client.delete(f"/api/scenario-presets/{PRESET_NAME}")

        assert response.status_code == 204

    def test_delete_reports_a_missing_preset(self, client: TestClient, service: MagicMock) -> None:
        service.delete_preset_async = AsyncMock(side_effect=ScenarioPresetNotFoundError(PRESET_NAME))

        response = client.delete(f"/api/scenario-presets/{PRESET_NAME}")

        assert response.status_code == 404

    def test_delete_requires_admin(self, anonymous_client: TestClient, service: MagicMock) -> None:
        service.delete_preset_async = AsyncMock(return_value=None)

        response = anonymous_client.delete(f"/api/scenario-presets/{PRESET_NAME}")

        assert response.status_code == 403
        service.delete_preset_async.assert_not_called()


class TestResolvePreset:
    """POST /api/scenario-presets/{name}/resolve."""

    def test_resolve_returns_a_run_request(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response())
        service.resolve_run_request = ScenarioPresetService.resolve_run_request

        response = client.post(
            f"/api/scenario-presets/{PRESET_NAME}/resolve",
            json={"target_name": "gpt4"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["scenario_name"] == SCENARIO_NAME
        assert body["target_name"] == "gpt4"
        assert body["max_concurrency"] == 10
        assert body["techniques"] is None

    def test_resolve_reports_a_missing_preset(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=None)

        response = client.post("/api/scenario-presets/missing/resolve", json={"target_name": "gpt4"})

        assert response.status_code == 404

    def test_resolve_requires_a_target(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response())

        response = client.post(f"/api/scenario-presets/{PRESET_NAME}/resolve", json={})

        assert response.status_code == 422
