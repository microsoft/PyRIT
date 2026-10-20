# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario scan definition routes."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.scenario_scans import (
    ScenarioScanEntryStatus,
    ScenarioScanListResponse,
    ScenarioScanResponse,
)
from pyrit.backend.services.scenario_scan_service import ScenarioScanService
from pyrit.models.catalog import PresetIssue, ScenarioScan, ScenarioScanEntry
from pyrit.registry import ScenarioScanConflictError

SCAN_NAME = "weekly"
ENTRY_ID = UUID("11111111-1111-4111-8111-111111111111")


def _scan(*, name: str = SCAN_NAME, author: str | None = None) -> ScenarioScan:
    """Build a minimal scenario scan."""
    return ScenarioScan(
        name=name,
        author=author,
        entries=[ScenarioScanEntry(entry_id=ENTRY_ID, preset_name="nightly")],
    )


def _response(
    *,
    scan: ScenarioScan | None = None,
    version: str = "scan-v1",
    issues: list[PresetIssue] | None = None,
) -> ScenarioScanResponse:
    """Build a scenario scan response envelope."""
    definition = scan or _scan()
    return ScenarioScanResponse(
        scan=definition,
        version=version,
        entry_statuses=[
            ScenarioScanEntryStatus(
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
    """Patch the scenario scan service resolved by the routes."""
    mock_service = MagicMock(spec=ScenarioScanService)
    mock_service.get_scan_async.return_value = _response()
    with patch("pyrit.backend.routes.scenario_scans.get_scenario_scan_service", return_value=mock_service):
        yield mock_service


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
    service.list_scans_async = AsyncMock(
        return_value=ScenarioScanListResponse(source="/tmp/scans", items=[_response(issues=[issue])])
    )

    response = anonymous_client.get("/api/scenario-scans")

    assert response.status_code == 200
    assert response.json()["source"] == "/tmp/scans"
    assert response.json()["items"][0]["entry_statuses"][0]["issues"][0]["message"] == "Preset is missing."


def test_get_returns_scan_and_version(client: TestClient, service: MagicMock) -> None:
    """Test a single open read."""
    service.get_scan_async = AsyncMock(return_value=_response())

    response = client.get(f"/api/scenario-scans/{SCAN_NAME}")

    assert response.status_code == 200
    assert response.json()["version"] == "scan-v1"
    assert response.json()["scan"]["entries"][0]["entry_id"] == str(ENTRY_ID)


def test_get_reports_missing_scan(client: TestClient, service: MagicMock) -> None:
    """Test missing document handling."""
    service.get_scan_async = AsyncMock(return_value=None)

    assert client.get("/api/scenario-scans/missing").status_code == 404


def test_storage_failure_is_sanitized(client: TestClient, service: MagicMock) -> None:
    """Test that Azure SDK details do not reach API callers."""
    service.list_scans_async = AsyncMock(side_effect=AzureError("container key=secret"))

    response = client.get("/api/scenario-scans")

    assert response.status_code == 503
    assert "secret" not in response.text


def test_create_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test mutation authorization."""
    service.save_scan_async = AsyncMock(return_value=_response())

    response = anonymous_client.post("/api/scenario-scans", json=_scan().model_dump(mode="json"))

    assert response.status_code == 403
    service.save_scan_async.assert_not_called()


@pytest.mark.usefixtures("signed_in_user")
def test_create_records_signed_in_author(client: TestClient, service: MagicMock) -> None:
    """Test author stamping for new scans."""
    service.save_scan_async = AsyncMock(return_value=_response(scan=_scan(author="Ada Lovelace")))

    response = client.post("/api/scenario-scans", json=_scan().model_dump(mode="json"))

    assert response.status_code == 201
    assert service.save_scan_async.call_args.kwargs["scan"].author == "Ada Lovelace"
    assert service.save_scan_async.call_args.kwargs["expected_version"] is None


@pytest.mark.usefixtures("signed_in_user")
def test_create_preserves_imported_author(client: TestClient, service: MagicMock) -> None:
    """Test that importing a scan does not rewrite its descriptive author."""
    service.save_scan_async = AsyncMock(return_value=_response(scan=_scan(author="Grace Hopper")))

    client.post("/api/scenario-scans", json=_scan(author="Grace Hopper").model_dump(mode="json"))

    assert service.save_scan_async.call_args.kwargs["scan"].author == "Grace Hopper"


def test_create_over_existing_name_is_conflict(client: TestClient, service: MagicMock) -> None:
    """Test create-if-absent behavior."""
    service.save_scan_async = AsyncMock(
        side_effect=ScenarioScanConflictError(name=SCAN_NAME, expected_version=None, actual_version="scan-v1")
    )

    response = client.post("/api/scenario-scans", json=_scan().model_dump(mode="json"))

    assert response.status_code == 409


def test_update_passes_expected_version(client: TestClient, service: MagicMock) -> None:
    """Test optimistic concurrency on update."""
    service.save_scan_async = AsyncMock(return_value=_response(version="scan-v2"))

    response = client.put(
        f"/api/scenario-scans/{SCAN_NAME}",
        json={"scan": _scan().model_dump(mode="json"), "expected_version": "scan-v1"},
    )

    assert response.status_code == 200
    assert service.save_scan_async.call_args.kwargs["expected_version"] == "scan-v1"


def test_update_preserves_stored_author(client: TestClient, service: MagicMock) -> None:
    """Test author preservation when the editor omits it."""
    stored = _scan(author="Grace Hopper")
    service.get_scan_async.return_value = _response(scan=stored)
    service.save_scan_async.return_value = _response(scan=stored, version="scan-v2")

    response = client.put(
        f"/api/scenario-scans/{SCAN_NAME}",
        json={"scan": _scan().model_dump(mode="json"), "expected_version": "scan-v1"},
    )

    assert response.status_code == 200
    assert service.save_scan_async.call_args.kwargs["scan"].author == "Grace Hopper"


def test_update_rejects_body_name_mismatch(client: TestClient, service: MagicMock) -> None:
    """Test path authority over document identity."""
    response = client.put(
        "/api/scenario-scans/other",
        json={"scan": _scan().model_dump(mode="json"), "expected_version": "scan-v1"},
    )

    assert response.status_code == 400
    service.save_scan_async.assert_not_called()


def test_update_reports_version_conflict(client: TestClient, service: MagicMock) -> None:
    """Test that stale editors do not overwrite a newer scan."""
    service.save_scan_async = AsyncMock(
        side_effect=ScenarioScanConflictError(
            name=SCAN_NAME,
            expected_version="scan-v1",
            actual_version="scan-v2",
        )
    )

    response = client.put(
        f"/api/scenario-scans/{SCAN_NAME}",
        json={"scan": _scan().model_dump(mode="json"), "expected_version": "scan-v1"},
    )

    assert response.status_code == 409
    assert "re-read" in response.json()["detail"]


def test_update_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test update authorization."""
    response = anonymous_client.put(
        f"/api/scenario-scans/{SCAN_NAME}",
        json={"scan": _scan().model_dump(mode="json"), "expected_version": "scan-v1"},
    )

    assert response.status_code == 403


def test_delete_requires_confirmation_from_storage(client: TestClient, service: MagicMock) -> None:
    """Test successful and missing deletes."""
    service.delete_scan_async = AsyncMock(side_effect=[True, False])

    assert client.delete(f"/api/scenario-scans/{SCAN_NAME}").status_code == 204
    assert client.delete(f"/api/scenario-scans/{SCAN_NAME}").status_code == 404


def test_delete_requires_admin(anonymous_client: TestClient, service: MagicMock) -> None:
    """Test delete authorization."""
    response = anonymous_client.delete(f"/api/scenario-scans/{SCAN_NAME}")

    assert response.status_code == 403
    service.delete_scan_async.assert_not_called()
