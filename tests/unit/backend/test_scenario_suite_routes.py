# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario suite definition routes."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
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
from pyrit.backend.services.scenario_suite_execution_service import (
    ScenarioSuiteExecutionCapacityError,
    ScenarioSuiteExecutionService,
)
from pyrit.backend.services.scenario_suite_service import ScenarioSuiteService
from pyrit.models import (
    ScenarioSuiteExecutionEntrySummary,
    ScenarioSuiteExecutionStatus,
    ScenarioSuiteExecutionSummary,
    ScenarioSuiteSubmissionState,
)
from pyrit.models.catalog import PresetIssue, ScenarioSuite, ScenarioSuiteEntry, ScenarioSuiteRegistryEntry
from pyrit.registry import (
    ScenarioPresetRegistry,
    ScenarioSuiteConflictError,
    ScenarioSuiteExecutionNotFoundError,
    ScenarioSuiteLaunchConflictError,
    ScenarioSuiteNameRegisteredError,
    ScenarioSuiteNotRunnableError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
)

SUITE_NAME = "weekly"
ENTRY_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")


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
    origin: Literal["stored", "registered"] = "stored",
    version: str | None = "suite-v1",
    issues: list[PresetIssue] | None = None,
) -> ScenarioSuiteResponse:
    """Build a scenario suite response envelope."""
    definition = suite or _suite()
    return ScenarioSuiteResponse(
        suite=definition,
        origin=origin,
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


def _execution_summary(
    *,
    status_value: ScenarioSuiteExecutionStatus = ScenarioSuiteExecutionStatus.QUEUING,
) -> ScenarioSuiteExecutionSummary:
    """Build one transient suite execution response."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return ScenarioSuiteExecutionSummary(
        suite_execution_id=EXECUTION_ID,
        suite_name=SUITE_NAME,
        suite_origin="stored",
        suite_version="suite-v1",
        status=status_value,
        created_at=now,
        updated_at=now,
        entries=[
            ScenarioSuiteExecutionEntrySummary(
                entry_id=ENTRY_ID,
                entry_position=1,
                preset_name="nightly",
                preset_origin="stored",
                preset_version="preset-v1",
                state=ScenarioSuiteSubmissionState.PENDING,
            )
        ],
    )


def _launch_body() -> dict[str, object]:
    """Build the suite launch payload sent by the browser."""
    return {
        "expected_version": "suite-v1",
        "entries": [
            {
                "entry_id": str(ENTRY_ID),
                "preset_name": "nightly",
                "origin": "stored",
                "preset_version": "preset-v1",
            }
        ],
        "target_name": "objective-target",
        "max_concurrency": 5,
        "max_retries": 2,
    }


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
    mock_service.load_suite_async.return_value = ScenarioSuiteRegistryEntry(
        suite=_suite(),
        origin="stored",
        version="suite-v1",
    )
    with patch("pyrit.backend.routes.scenario_suites.get_scenario_suite_service", return_value=mock_service):
        yield mock_service


@pytest.fixture
def execution_service() -> Iterator[MagicMock]:
    """Patch the transient suite execution coordinator resolved by the routes."""
    mock_service = MagicMock(spec=ScenarioSuiteExecutionService)
    with patch(
        "pyrit.backend.routes.scenario_suites.get_scenario_suite_execution_service",
        return_value=mock_service,
    ):
        yield mock_service


@pytest.fixture
def unavailable_preset_backend(
    tmp_path: Path,
) -> Iterator[tuple[ScenarioSuiteService, ScenarioSuiteStorage]]:
    """Run routes against real suite storage and an unavailable preset registry."""
    registry = MagicMock(spec=ScenarioPresetRegistry)
    registry.get_preset.side_effect = AzureError("expired preset container signature")
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    scenario_service = ScenarioSuiteService(registry=ScenarioSuiteRegistry(storage=storage))
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


def test_launch_is_open_and_returns_server_execution_id(
    anonymous_client: TestClient,
    execution_service: MagicMock,
) -> None:
    """Test one launch action creates a server-owned transient execution."""
    execution_service.launch_async = AsyncMock(return_value=_execution_summary())

    response = anonymous_client.post(f"/api/scenario-suites/{SUITE_NAME}/launch", json=_launch_body())

    assert response.status_code == 202
    assert response.json()["suite_execution_id"] == str(EXECUTION_ID)
    launch = execution_service.launch_async.call_args.kwargs["request"]
    assert launch.expected_version == "suite-v1"
    assert launch.entries[0].preset_version == "preset-v1"
    assert launch.max_concurrency == 5


def test_launch_is_rejected_during_runtime_reinitialization(
    anonymous_client: TestClient,
    execution_service: MagicMock,
) -> None:
    """Test runtime transitions reject new suite work before preflight."""
    with patch.object(app.state, "runtime_lifecycle", MagicMock(state="initializing"), create=True):
        response = anonymous_client.post(
            f"/api/scenario-suites/{SUITE_NAME}/launch",
            json=_launch_body(),
        )

    assert response.status_code == 503
    execution_service.launch_async.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ScenarioSuiteExecutionNotFoundError("missing"), 404),
        (ScenarioSuiteLaunchConflictError("changed"), 409),
        (ScenarioSuiteNotRunnableError("not runnable"), 400),
        (ScenarioSuiteExecutionCapacityError("wait for an active suite"), 429),
        (AzureError("storage unavailable"), 503),
        (RuntimeError("scheduling is stopping"), 503),
    ],
)
def test_launch_maps_preflight_failures(
    anonymous_client: TestClient,
    execution_service: MagicMock,
    error: Exception,
    expected_status: int,
) -> None:
    """Test actionable launch failures without exposing storage details."""
    execution_service.launch_async = AsyncMock(side_effect=error)

    response = anonymous_client.post(f"/api/scenario-suites/{SUITE_NAME}/launch", json=_launch_body())

    assert response.status_code == expected_status
    if isinstance(error, AzureError):
        assert "storage unavailable" not in response.text
    if isinstance(error, ScenarioSuiteExecutionCapacityError):
        assert response.json()["detail"] == "wait for an active suite"


def test_get_and_cancel_transient_execution(
    anonymous_client: TestClient,
    execution_service: MagicMock,
) -> None:
    """Test grouped progress reads and best-effort cancellation."""
    execution_service.get_execution_async = AsyncMock(return_value=_execution_summary())
    execution_service.cancel_execution_async = AsyncMock(
        return_value=_execution_summary(status_value=ScenarioSuiteExecutionStatus.CANCELLED)
    )

    get_response = anonymous_client.get(f"/api/scenario-suites/executions/{EXECUTION_ID}")
    cancel_response = anonymous_client.post(f"/api/scenario-suites/executions/{EXECUTION_ID}/cancel")

    assert get_response.status_code == 200
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"


def test_missing_transient_execution_returns_not_found(
    anonymous_client: TestClient,
    execution_service: MagicMock,
) -> None:
    """Test process-local progress expiry is explicit."""
    execution_service.get_execution_async = AsyncMock(return_value=None)
    execution_service.cancel_execution_async = AsyncMock(return_value=None)

    assert anonymous_client.get(f"/api/scenario-suites/executions/{EXECUTION_ID}").status_code == 404
    assert anonymous_client.post(f"/api/scenario-suites/executions/{EXECUTION_ID}/cancel").status_code == 404


def test_get_returns_suite_and_version(client: TestClient, service: MagicMock) -> None:
    """Test a single open read."""
    service.get_suite_async = AsyncMock(return_value=_response())

    response = client.get(f"/api/scenario-suites/{SUITE_NAME}")

    assert response.status_code == 200
    assert response.json()["origin"] == "stored"
    assert response.json()["version"] == "suite-v1"
    assert response.json()["suite"]["entries"][0]["entry_id"] == str(ENTRY_ID)


def test_get_reports_missing_suite(client: TestClient, service: MagicMock) -> None:
    """Test missing document handling."""
    service.get_suite_async = AsyncMock(return_value=None)

    assert client.get("/api/scenario-suites/missing").status_code == 404


def test_get_registered_suite_returns_origin_and_null_version(client: TestClient, service: MagicMock) -> None:
    """Test the read-only registered suite response contract."""
    service.get_suite_async = AsyncMock(return_value=_response(origin="registered", version=None))

    response = client.get(f"/api/scenario-suites/{SUITE_NAME}")

    assert response.status_code == 200
    assert response.json()["origin"] == "registered"
    assert response.json()["version"] is None


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


def test_create_over_registered_name_is_actionable_conflict(client: TestClient, service: MagicMock) -> None:
    """Test code-owned names are not reported as retryable version conflicts."""
    service.save_suite_async = AsyncMock(side_effect=ScenarioSuiteNameRegisteredError(SUITE_NAME))

    response = client.post("/api/scenario-suites", json=_suite().model_dump(mode="json"))

    assert response.status_code == 409
    assert response.json()["detail"] == f"Scenario suite '{SUITE_NAME}' is registered in code and cannot be stored"


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
    service.load_suite_async.return_value = ScenarioSuiteRegistryEntry(
        suite=stored,
        origin="stored",
        version="suite-v1",
    )
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


def test_update_registered_name_is_actionable_conflict(client: TestClient, service: MagicMock) -> None:
    """Test a direct update cannot mutate a code-defined suite."""
    service.load_suite_async.return_value = ScenarioSuiteRegistryEntry(
        suite=_suite(),
        origin="registered",
    )
    service.save_suite_async = AsyncMock(side_effect=ScenarioSuiteNameRegisteredError(SUITE_NAME))

    response = client.put(
        f"/api/scenario-suites/{SUITE_NAME}",
        json={"suite": _suite().model_dump(mode="json"), "expected_version": "suite-v1"},
    )

    assert response.status_code == 409
    assert "registered in code" in response.json()["detail"]


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
