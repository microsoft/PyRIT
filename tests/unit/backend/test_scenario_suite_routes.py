# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario suite definition routes."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.scenario_suites import (
    ScenarioSuiteEntryStatus,
    ScenarioSuiteListResponse,
    ScenarioSuiteResponse,
)
from pyrit.backend.services.scenario_suite_service import ScenarioSuiteService
from pyrit.models.catalog import PresetIssue, ScenarioSuite, ScenarioSuiteEntry, StoredScenarioSuite
from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteConflictError, ScenarioSuiteStorage

SUITE_NAME = "weekly"
ENTRY_ID = UUID("11111111-1111-4111-8111-111111111111")


def _suite(*, name: str = SUITE_NAME, author: str | None = None) -> ScenarioSuite:
    """Build a minimal scenario suite."""
    return ScenarioSuite(
        name=name,
        author=author,
        entries=[ScenarioSuiteEntry(entry_id=ENTRY_ID, preset_name="nightly")],
    )


def _response(
    *,
    suite: ScenarioSuite | None = None,
    version: str = "suite-v1",
    issues: list[PresetIssue] | None = None,
) -> ScenarioSuiteResponse:
    """Build a scenario suite response envelope."""
    definition = suite or _suite()
    return ScenarioSuiteResponse(
        suite=definition,
        version=version,
        entry_statuses=[
            ScenarioSuiteEntryStatus(
                entry_id=ENTRY_ID,
                preset_name="nightly",
                origin="stored",
                preset_version="preset-v1",
                issues=issues or [],
            )
        ],
    )


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> Iterator[TestClient]:
    """Create a client with administrator authorization satisfied."""
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app, headers=compatibility_headers)
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def anonymous_client(compatibility_headers: dict[str, str]) -> TestClient:
    """Create a client without administrator authorization."""
    return TestClient(app, headers=compatibility_headers)


@pytest.fixture
def service() -> Iterator[MagicMock]:
    """Patch the scenario suite service resolved by the routes."""
    mock_service = MagicMock(spec=ScenarioSuiteService)
    mock_service.get_suite_async.return_value = _response()
    mock_service.load_suite_async.return_value = StoredScenarioSuite(suite=_suite(), version="suite-v1")
    with patch("pyrit.backend.routes.scenario_suites.get_scenario_suite_service", return_value=mock_service):
        yield mock_service


@pytest.fixture
def unavailable_preset_backend(
    tmp_path: Path,
) -> Iterator[tuple[ScenarioSuiteService, ScenarioSuiteStorage]]:
    """Run routes against real suite storage and an unavailable preset registry."""
    registry = MagicMock(spec=ScenarioPresetRegistry)
    registry.get_preset.side_effect = AzureError("expired preset container signature")
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    scenario_service = ScenarioSuiteService(storage=storage)
    with (
        patch.object(ScenarioPresetRegistry, "get_registry_singleton", return_value=registry),
        patch(
            "pyrit.backend.routes.scenario_suites.get_scenario_suite_service",
            return_value=scenario_service,
        ),
    ):
        yield scenario_service, storage


@pytest.fixture
def signed_in_user() -> Iterator[None]:
    """Report a signed-in user to routes that record one."""
    app.dependency_overrides[current_user_name] = lambda: "Ada Lovelace"
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_user_name, None)


def test_list_is_open_and_returns_entry_issues(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test open reads with entry-scoped advisory issues."""
    issue = PresetIssue(field="preset_name", message="Preset is missing.")
    service.list_suites_async = AsyncMock(
        return_value=ScenarioSuiteListResponse(source="/tmp/suites", items=[_response(issues=[issue])])
    )

    response = anonymous_client.get("/api/scenario-suites")

    assert response.status_code == 200
    assert response.json()["source"] == "/tmp/suites"
    assert response.json()["items"][0]["entry_statuses"][0]["issues"][0]["message"] == "Preset is missing."


def test_get_returns_suite_and_version(client: TestClient, service: MagicMock) -> None:
    """Test a single open read."""
    service.get_suite_async = AsyncMock(return_value=_response())

    response = client.get(f"/api/scenario-suites/{SUITE_NAME}")

    assert response.status_code == 200
    assert response.json()["version"] == "suite-v1"
    assert response.json()["suite"]["entries"][0]["entry_id"] == str(ENTRY_ID)


def test_get_reports_missing_suite(client: TestClient, service: MagicMock) -> None:
    """Test missing document handling."""
    service.get_suite_async = AsyncMock(return_value=None)

    assert client.get("/api/scenario-suites/missing").status_code == 404


def test_storage_failure_is_sanitized(client: TestClient, service: MagicMock) -> None:
    """Test that Azure SDK details do not reach API callers."""
    service.list_suites_async = AsyncMock(side_effect=AzureError("container key=secret"))

    response = client.get("/api/scenario-suites")

    assert response.status_code == 503
    assert "secret" not in response.text


def test_create_succeeds_when_preset_storage_is_unavailable(
    client: TestClient,
    unavailable_preset_backend: tuple[ScenarioSuiteService, ScenarioSuiteStorage],
) -> None:
    """Test that a committed create returns its advisory projection instead of 503."""
    _, storage = unavailable_preset_backend

    response = client.post("/api/scenario-suites", json=_suite().model_dump(mode="json"))

    assert response.status_code == 201
    assert storage.load_suite(SUITE_NAME) is not None
    assert "preset storage is temporarily unavailable" in response.json()["entry_statuses"][0]["issues"][0]["message"]


def test_list_succeeds_when_preset_storage_is_unavailable(
    client: TestClient,
    unavailable_preset_backend: tuple[ScenarioSuiteService, ScenarioSuiteStorage],
) -> None:
    """Test that one unreadable preset projection does not fail the suite library."""
    _, storage = unavailable_preset_backend
    storage.save_suite(suite=_suite(), expected_version=None)

    response = client.get("/api/scenario-suites")

    assert response.status_code == 200
    assert response.json()["items"][0]["suite"]["name"] == SUITE_NAME
    assert (
        "preset storage is temporarily unavailable"
        in (response.json()["items"][0]["entry_statuses"][0]["issues"][0]["message"])
    )


def test_get_succeeds_when_preset_storage_is_unavailable(
    client: TestClient,
    unavailable_preset_backend: tuple[ScenarioSuiteService, ScenarioSuiteStorage],
) -> None:
    """Test that a stored suite remains readable when preset metadata is unavailable."""
    _, storage = unavailable_preset_backend
    storage.save_suite(suite=_suite(), expected_version=None)

    response = client.get(f"/api/scenario-suites/{SUITE_NAME}")

    assert response.status_code == 200
    assert response.json()["suite"]["name"] == SUITE_NAME
    assert "preset storage is temporarily unavailable" in response.json()["entry_statuses"][0]["issues"][0]["message"]


def test_create_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test mutation authorization."""
    service.save_suite_async = AsyncMock(return_value=_response())

    response = anonymous_client.post("/api/scenario-suites", json=_suite().model_dump(mode="json"))

    assert response.status_code == 403
    service.save_suite_async.assert_not_called()


@pytest.mark.usefixtures("signed_in_user")
def test_create_records_signed_in_author(client: TestClient, service: MagicMock) -> None:
    """Test author stamping for new suites."""
    service.save_suite_async = AsyncMock(return_value=_response(suite=_suite(author="Ada Lovelace")))

    response = client.post("/api/scenario-suites", json=_suite().model_dump(mode="json"))

    assert response.status_code == 201
    assert service.save_suite_async.call_args.kwargs["suite"].author == "Ada Lovelace"
    assert service.save_suite_async.call_args.kwargs["expected_version"] is None


@pytest.mark.usefixtures("signed_in_user")
def test_create_preserves_imported_author(client: TestClient, service: MagicMock) -> None:
    """Test that importing a suite does not rewrite its descriptive author."""
    service.save_suite_async = AsyncMock(return_value=_response(suite=_suite(author="Grace Hopper")))

    client.post("/api/scenario-suites", json=_suite(author="Grace Hopper").model_dump(mode="json"))

    assert service.save_suite_async.call_args.kwargs["suite"].author == "Grace Hopper"


def test_create_over_existing_name_is_conflict(client: TestClient, service: MagicMock) -> None:
    """Test create-if-absent behavior."""
    service.save_suite_async = AsyncMock(
        side_effect=ScenarioSuiteConflictError(name=SUITE_NAME, expected_version=None, actual_version="suite-v1")
    )

    response = client.post("/api/scenario-suites", json=_suite().model_dump(mode="json"))

    assert response.status_code == 409


def test_update_passes_expected_version(client: TestClient, service: MagicMock) -> None:
    """Test optimistic concurrency on update."""
    service.save_suite_async = AsyncMock(return_value=_response(version="suite-v2"))

    response = client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 200
    assert service.save_suite_async.call_args.kwargs["expected_version"] == "suite-v1"


def test_update_preserves_stored_author(client: TestClient, service: MagicMock) -> None:
    """Test author preservation when the editor omits it."""
    stored = _suite(author="Grace Hopper")
    service.load_suite_async.return_value = StoredScenarioSuite(suite=stored, version="suite-v1")
    service.save_suite_async.return_value = _response(suite=stored, version="suite-v2")

    response = client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 200
    assert service.save_suite_async.call_args.kwargs["suite"].author == "Grace Hopper"
    service.get_suite_async.assert_not_called()


def test_update_succeeds_when_preset_storage_is_unavailable(
    client: TestClient,
    unavailable_preset_backend: tuple[ScenarioSuiteService, ScenarioSuiteStorage],
) -> None:
    """Test that author carry-over and a committed update do not require preset storage."""
    _, storage = unavailable_preset_backend
    stored = storage.save_suite(suite=_suite(author="Grace Hopper"), expected_version=None)

    response = client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={
            "suite": _suite().model_dump(mode="json"),
            "expected_version": stored.version,
        },
    )

    assert response.status_code == 200
    assert response.json()["suite"]["author"] == "Grace Hopper"
    assert "preset storage is temporarily unavailable" in response.json()["entry_statuses"][0]["issues"][0]["message"]


def test_update_rejects_body_name_mismatch(client: TestClient, service: MagicMock) -> None:
    """Test path authority over document identity."""
    response = client.put(
        "/api/scenario-suites/other",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 400
    service.save_suite_async.assert_not_called()


def test_update_reports_version_conflict(client: TestClient, service: MagicMock) -> None:
    """Test that stale editors do not overwrite a newer suite."""
    service.save_suite_async = AsyncMock(
        side_effect=ScenarioSuiteConflictError(
            name=SUITE_NAME,
            expected_version="suite-v1",
            actual_version="suite-v2",
        )
    )

    response = client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 409
    assert "re-read" in response.json()["detail"]


def test_update_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test update authorization."""
    response = anonymous_client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 403


def test_delete_requires_confirmation_from_storage(client: TestClient, service: MagicMock) -> None:
    """Test successful and missing deletes."""
    service.delete_suite_async = AsyncMock(side_effect=[True, False])

    assert client.delete(f"/api/scenario-suites/{SUITE_NAME}").status_code == 204
    assert client.delete(f"/api/scenario-suites/{SUITE_NAME}").status_code == 404


def test_delete_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test delete authorization."""
    response = anonymous_client.delete(f"/api/scenario-suites/{SUITE_NAME}")

    assert response.status_code == 403
    service.delete_suite_async.assert_not_called()
