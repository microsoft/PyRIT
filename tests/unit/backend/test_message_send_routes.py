# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-conversation asynchronous submission and read-only status contracts."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.message_sends import MessageSendStatus
from pyrit.backend.services.manual_send_scheduler import ManualSendConflictError, ManualSendQueueFullError
from pyrit.backend.services.message_send_service import MessageSendNotFoundError, MessageSendService


@pytest.fixture
def sender() -> MagicMock:
    service = MagicMock(spec=MessageSendService)
    progress = MessageSendStatus(send_id="send", attack_result_id="attack", conversation_id="conversation")
    service.submit_async.return_value = progress
    service.get_status_async.return_value = progress
    return service


@pytest.fixture
def client(sender: MagicMock) -> Iterator[TestClient]:
    with patch("pyrit.backend.routes.message_sends.get_message_send_service", return_value=sender):
        yield TestClient(app)


@pytest.fixture
def payload() -> dict[str, object]:
    return {
        "pieces": [{"original_value": "one message"}],
        "submission_id": "submission",
        "target_conversation_id": "conversation",
        "target_registry_name": "target",
    }


def test_submission_returns_accepted_handle(
    *, client: TestClient, sender: MagicMock, payload: dict[str, object]
) -> None:
    response = client.post("/api/attacks/attack/message-sends", json=payload)
    assert response.status_code == 202
    assert response.json() == {
        "send_id": "send",
        "attack_result_id": "attack",
        "conversation_id": "conversation",
        "request_turn_number": None,
        "state": "queued",
        "error": None,
        "failure_stage": None,
    }
    sender.submit_async.assert_awaited_once()


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("count", 2),
        ("count", 1),
        ("request_converter_mode", "shared"),
        ("submission_id", ""),
        ("submission_id", " "),
        ("submission_id", "s" * 129),
        ("target_registry_name", ""),
        ("target_conversation_id", " "),
        ("pieces", []),
        ("send", False),
    ],
)
def test_invalid_or_later_phase_fields_are_not_admitted(
    *, client: TestClient, sender: MagicMock, payload: dict[str, object], override: str, value: object
) -> None:
    payload[override] = value
    assert client.post("/api/attacks/attack/message-sends", json=payload).status_code == 422
    sender.submit_async.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("Attack not found"), 404),
        (ValueError("Target mismatch"), 400),
        (ManualSendConflictError("Conversation busy"), 409),
        (ManualSendQueueFullError("Queue full"), 429),
        (RuntimeError("sensitive failure"), 500),
    ],
)
def test_admission_errors(
    *, client: TestClient, sender: MagicMock, payload: dict[str, object], error: Exception, status: int
) -> None:
    sender.submit_async.side_effect = error
    response = client.post("/api/attacks/attack/message-sends", json=payload)
    assert response.status_code == status
    assert "sensitive failure" not in response.text


@pytest.mark.parametrize("wait_ms", [0, 1000])
def test_status_read_does_not_submit(*, client: TestClient, sender: MagicMock, wait_ms: int) -> None:
    assert client.get(f"/api/attacks/attack/message-sends/send?wait_ms={wait_ms}").status_code == 200
    sender.get_status_async.assert_awaited_once_with(attack_result_id="attack", send_id="send", wait_ms=wait_ms)
    sender.submit_async.assert_not_awaited()


@pytest.mark.parametrize("wait_ms", [-1, 1001])
def test_status_wait_is_bounded(*, client: TestClient, sender: MagicMock, wait_ms: int) -> None:
    assert client.get(f"/api/attacks/attack/message-sends/send?wait_ms={wait_ms}").status_code == 422
    sender.get_status_async.assert_not_awaited()


def test_missing_handle_is_not_recreated(*, client: TestClient, sender: MagicMock) -> None:
    sender.get_status_async.side_effect = MessageSendNotFoundError("Status unavailable; do not resend")
    response = client.get("/api/attacks/attack/message-sends/lost")
    assert response.status_code == 404
    assert "do not resend" in response.json()["detail"]
    sender.submit_async.assert_not_awaited()
