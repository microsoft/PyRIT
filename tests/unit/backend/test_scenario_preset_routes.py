# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset routes."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.scenario_presets import (
    PresetIssue,
    ScenarioPresetListResponse,
    ScenarioPresetResponse,
)
from pyrit.backend.services.scenario_preset_service import (
    ScenarioPresetNotFoundError,
    ScenarioPresetService,
)
from pyrit.models.catalog import ScenarioPreset, ScenarioRunSizeEstimate
from pyrit.models.catalog.scenario import ScenarioRunSizeComponent
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


@pytest.fixture
def signed_in_user() -> Iterator[None]:
    """Report a signed-in user to routes that record one."""
    app.dependency_overrides[current_user_name] = lambda: "Ada Lovelace"
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_user_name, None)


def _estimate(count: int) -> ScenarioRunSizeEstimate:
    """Build an exact run-size estimate of *count* attacks."""
    return ScenarioRunSizeEstimate(
        estimated_attack_count=count,
        components=[ScenarioRunSizeComponent(label="Preset sweep", count=count)],
    )


def _preset(name: str = PRESET_NAME) -> ScenarioPreset:
    """Build a minimal preset."""
    return ScenarioPreset(name=name, scenario_name=SCENARIO_NAME)


def _response(
    *,
    name: str = PRESET_NAME,
    version: str = "v1",
    issues: list[PresetIssue] | None = None,
    run_size: ScenarioRunSizeEstimate | None = None,
):
    """Build a preset response envelope."""
    return ScenarioPresetResponse(preset=_preset(name), version=version, issues=issues or [], run_size=run_size)


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

    def test_each_preset_carries_its_own_run_size(self, client: TestClient, service: MagicMock) -> None:
        estimate = _estimate(42)
        service.list_presets_async = AsyncMock(
            return_value=ScenarioPresetListResponse(source="/tmp", items=[_response(run_size=estimate)])
        )

        response = client.get("/api/scenario-presets")

        assert response.json()["items"][0]["run_size"]["total_attack_count"] == 42
        assert service.list_presets_async.call_args.kwargs["include_estimates"] is True

    def test_sizing_can_be_skipped_so_the_table_paints_first(self, client: TestClient, service: MagicMock) -> None:
        service.list_presets_async = AsyncMock(
            return_value=ScenarioPresetListResponse(source="/tmp", items=[_response()])
        )

        response = client.get("/api/scenario-presets", params={"include_estimates": "false"})

        assert response.status_code == 200
        assert response.json()["items"][0]["run_size"] is None
        assert service.list_presets_async.call_args.kwargs["include_estimates"] is False


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

    @pytest.mark.usefixtures("signed_in_user")
    def test_the_signed_in_user_is_recorded_as_the_author(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())

        client.post("/api/scenario-presets", json=_preset().model_dump())

        assert service.save_preset_async.call_args.kwargs["preset"].author == "Ada Lovelace"

    @pytest.mark.usefixtures("signed_in_user")
    def test_an_author_in_the_body_survives_import(self, client: TestClient, service: MagicMock) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())
        payload = _preset().model_dump()
        payload["author"] = "Grace Hopper"

        client.post("/api/scenario-presets", json=payload)

        assert service.save_preset_async.call_args.kwargs["preset"].author == "Grace Hopper"

    def test_an_unauthenticated_deployment_leaves_the_author_unset(
        self, client: TestClient, service: MagicMock
    ) -> None:
        service.save_preset_async = AsyncMock(return_value=_response())

        client.post("/api/scenario-presets", json=_preset().model_dump())

        assert service.save_preset_async.call_args.kwargs["preset"].author is None


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

    def test_resolve_accepts_the_version_the_caller_read(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response(version="v1"))
        service.resolve_run_request = ScenarioPresetService.resolve_run_request

        response = client.post(
            f"/api/scenario-presets/{PRESET_NAME}/resolve",
            json={"target_name": "gpt4", "expected_version": "v1"},
        )

        assert response.status_code == 200

    def test_a_preset_edited_after_it_was_read_is_a_conflict(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response(version="v2"))
        service.resolve_run_request = ScenarioPresetService.resolve_run_request

        response = client.post(
            f"/api/scenario-presets/{PRESET_NAME}/resolve",
            json={"target_name": "gpt4", "expected_version": "v1"},
        )

        assert response.status_code == 409
        assert PRESET_NAME in response.json()["detail"]

    def test_an_omitted_version_resolves_whatever_is_stored(self, client: TestClient, service: MagicMock) -> None:
        service.get_preset_async = AsyncMock(return_value=_response(version="v9"))
        service.resolve_run_request = ScenarioPresetService.resolve_run_request

        response = client.post(f"/api/scenario-presets/{PRESET_NAME}/resolve", json={"target_name": "gpt4"})

        assert response.status_code == 200
