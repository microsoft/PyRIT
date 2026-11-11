# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the prompt generation routes."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import current_user_name
from pyrit.backend.models.prompt_generations import (
    PromptGenerationPreflightResponse,
    PromptGenerationRecord,
    PromptGenerationRequestFromDataset,
    PromptGenerationReview,
    PromptGenerationStatus,
)
from pyrit.backend.services.prompt_generation_adapters import create_prompt_generation_adapters
from pyrit.backend.services.prompt_generation_service import (
    PromptGenerationConflictError,
    PromptGenerationNotFoundError,
    PromptGenerationService,
)
from pyrit.models import PromptGenerationEstimate

GENERATION_ID = "11111111-1111-4111-8111-111111111111"
GROUP_ID = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
CREATE_BODY = {
    "generator": "assert",
    "dataset_name": "support_policy",
    "request": {"behavior_name": "support", "behavior_description": "Explain policies.", "group_count": 2},
    "targets": {"generation": "openai_chat"},
}


def _record(**overrides: object) -> PromptGenerationRecord:
    values: dict[str, object] = {
        "id": GENERATION_ID,
        "generator": "assert",
        "dataset_name": "support_policy",
        "status": PromptGenerationStatus.QUEUED,
        "request": CREATE_BODY["request"],
        "targets": {"generation": "openai_chat"},
        "checkpoint": {"secret_progress": True},
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return PromptGenerationRecord.model_validate(values)


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    return TestClient(app, headers=compatibility_headers)


@pytest.fixture
def service() -> Iterator[MagicMock]:
    mock_service = MagicMock(spec=PromptGenerationService)
    with patch("pyrit.backend.routes.prompt_generations.get_prompt_generation_service", return_value=mock_service):
        yield mock_service


def test_list_generators_describes_each_generator(client: TestClient, service: MagicMock) -> None:
    service.list_generators.return_value = [
        adapter.descriptor for adapter in create_prompt_generation_adapters().values()
    ]
    response = client.get("/api/prompt-generators")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == ["assert", "target_objective"]
    assert items[0]["follow_up_scenario"] == "foundry.assert_policy"
    assert [stage["reviewable"] for stage in items[0]["stages"]] == [False, False, True, False]


def test_list_assert_library(client: TestClient) -> None:
    behaviors = client.get("/api/prompt-generators/assert/behaviors").json()["items"]
    suites = client.get("/api/prompt-generators/assert/suites").json()["items"]
    assert {"prompt_injection", "sycophancy"} <= {item["name"] for item in behaviors}
    assert "travel_planner" in {suite["name"] for suite in suites}
    for items in (behaviors, suites):
        names = [item["name"] for item in items]
        assert names == sorted(names)
        assert all({"name", "version", "summary"} <= set(item) for item in items)
    assert all(suite["behaviors"] and suite["context"] for suite in suites)


def test_create_records_user_and_hides_checkpoint(client: TestClient, service: MagicMock) -> None:
    service.create_async = AsyncMock(return_value=_record())
    app.dependency_overrides[current_user_name] = lambda: "Ada"
    try:
        response = client.post("/api/prompt-generations", json=CREATE_BODY)
    finally:
        app.dependency_overrides.pop(current_user_name, None)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == GENERATION_ID
    assert body["request"] == CREATE_BODY["request"]
    assert "checkpoint" not in body
    assert service.create_async.call_args.kwargs["created_by"] == "Ada"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (PromptGenerationConflictError("taken"), 409),
        (PromptGenerationNotFoundError("missing"), 404),
        (ValueError("invalid request"), 400),
    ],
)
def test_create_maps_errors(client: TestClient, service: MagicMock, error: Exception, status_code: int) -> None:
    service.create_async = AsyncMock(side_effect=error)
    response = client.post("/api/prompt-generations", json=CREATE_BODY)
    assert response.status_code == status_code


def test_create_rejects_invalid_dataset_name(client: TestClient, service: MagicMock) -> None:
    response = client.post("/api/prompt-generations", json={**CREATE_BODY, "dataset_name": "../escape"})
    assert response.status_code == 422


def test_preflight_returns_validation_and_estimate(client: TestClient, service: MagicMock) -> None:
    service.preflight_async = AsyncMock(
        return_value=PromptGenerationPreflightResponse(
            valid=False,
            errors=["Choose a target for: Generation target."],
            dataset_name_available=True,
            estimate=PromptGenerationEstimate(exact_calls=False),
        )
    )
    body = client.post("/api/prompt-generations/preflight", json=CREATE_BODY).json()
    assert body["valid"] is False
    assert body["estimate"]["min_calls"] == 0
    assert service.preflight_async.call_args.args[0].dataset_name == "support_policy"
    response = client.post("/api/prompt-generations/preflight", json={**CREATE_BODY, "generation_id": GENERATION_ID})
    assert response.status_code == 422


def test_list_and_lifecycle_actions(client: TestClient, service: MagicMock) -> None:
    service.list_async = AsyncMock(return_value=[_record()])
    listed = client.get("/api/prompt-generations").json()["items"]
    assert listed[0]["id"] == GENERATION_ID
    assert "request" not in listed[0]

    for action, method in (
        ("continue", "continue_async"),
        ("retry", "retry_async"),
        ("cancel", "cancel_async"),
    ):
        setattr(service, method, AsyncMock(return_value=_record(status=PromptGenerationStatus.CANCELLED)))
        response = client.post(f"/api/prompt-generations/{GENERATION_ID}/{action}")
        assert response.status_code == 200, action
        getattr(service, method).assert_awaited_once_with(GENERATION_ID)

    service.delete_async = AsyncMock(return_value=None)
    assert client.delete(f"/api/prompt-generations/{GENERATION_ID}").status_code == 204


def test_get_maps_not_found_and_invalid_id(client: TestClient, service: MagicMock) -> None:
    service.get_async = AsyncMock(side_effect=PromptGenerationNotFoundError("missing"))
    assert client.get(f"/api/prompt-generations/{GENERATION_ID}").status_code == 404
    assert client.get("/api/prompt-generations/not-a-uuid").status_code == 422


def test_review_round_trip(client: TestClient, service: MagicMock) -> None:
    review = PromptGenerationReview(generation_id=GENERATION_ID, stage="dimensions", payload={"taxonomy": {}})
    service.get_review_async = AsyncMock(return_value=review)
    service.save_review_async = AsyncMock(side_effect=PromptGenerationConflictError("not awaiting review"))
    assert client.get(f"/api/prompt-generations/{GENERATION_ID}/review").json()["stage"] == "dimensions"
    response = client.put(f"/api/prompt-generations/{GENERATION_ID}/review", json={"payload": {"taxonomy": {}}})
    assert response.status_code == 409


def test_estimate_and_from_dataset(client: TestClient, service: MagicMock) -> None:
    service.get_estimate_async = AsyncMock(return_value=PromptGenerationEstimate(exact_calls=True))
    assert client.get(f"/api/prompt-generations/{GENERATION_ID}/estimate").json()["exact_calls"] is True

    service.request_from_dataset_async = AsyncMock(
        return_value=PromptGenerationRequestFromDataset(
            generator="assert", request={"behavior_name": "support"}, source_dataset_name="support_policy"
        )
    )
    response = client.get(
        "/api/prompt-generations/from-dataset", params={"selection_key": "dataset:named:support_policy"}
    )
    assert response.json()["generator"] == "assert"
    service.request_from_dataset_async.assert_awaited_once_with("dataset:named:support_policy")


def test_batch_create_returns_records(client: TestClient, service: MagicMock) -> None:
    service.create_batch_async = AsyncMock(
        return_value=[_record(group_id=GROUP_ID, group_label="Suite", dataset_name="suite_a")]
    )
    body = {
        "generator": "assert",
        "label": "Suite",
        "items": [{"dataset_name": "suite_a", "request": CREATE_BODY["request"]}],
        "targets": {"generation": "openai_chat"},
    }
    response = client.post("/api/prompt-generations/batch", json=body)
    assert response.status_code == 201
    items = response.json()["items"]
    assert [(item["dataset_name"], item["group_id"], item["group_label"]) for item in items] == [
        ("suite_a", GROUP_ID, "Suite")
    ]
    assert client.post("/api/prompt-generations/batch", json={**body, "items": []}).status_code == 422
