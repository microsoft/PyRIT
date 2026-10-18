# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.models.attacks import AddMessageResponse, AttackSummary, ConversationMessagesResponse
from pyrit.backend.routes.attacks import router
from pyrit.backend.services.attack_service import AttackService
from pyrit.memory.memory_interface import AttackStateConflictError


@pytest.fixture
def save_service() -> MagicMock:
    service = MagicMock(spec=AttackService)
    now = datetime.now(UTC)
    service.save_conversation_async.return_value = AddMessageResponse(
        attack=AttackSummary(
            attack_result_id="attack",
            conversation_id="conversation",
            objective="Draft objective",
            created_at=now,
            updated_at=now,
        ),
        messages=ConversationMessagesResponse(conversation_id="conversation", messages=[]),
    )
    return service


@pytest.fixture
def save_payload() -> dict[str, object]:
    return {
        "save_id": str(uuid4()),
        "destination": "new_attack",
        "objective": "Draft objective",
        "operator": "client-operator",
        "messages": [{"role": "user", "pieces": [{"original_value": "Draft prompt"}]}],
    }


@pytest.mark.parametrize("email", [None, "SignedIn.User@example.com"])
def test_save_conversation_success_and_operator(
    *, save_service: MagicMock, save_payload: dict[str, object], email: str | None
) -> None:
    application = FastAPI()
    application.include_router(router, prefix="/api")

    @application.middleware("http")
    async def identity_async(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if email is not None:
            request.state.user = AuthenticatedUser(oid="user", name="Signed-in user", email=email, groups=[])
        return await call_next(request)

    with (
        patch("pyrit.backend.routes.attacks.get_attack_service", return_value=save_service),
        TestClient(application) as client,
    ):
        response = client.post("/api/attacks/save-conversation", json=save_payload)
    assert response.status_code == 200
    assert response.json() == save_service.save_conversation_async.return_value.model_dump(mode="json")
    save_service.save_conversation_async.assert_awaited_once()
    request = save_service.save_conversation_async.call_args.kwargs["request"]
    assert request.operator == ("signedin.user" if email else "client-operator")
    assert request.objective == "Draft objective"
    assert request.messages[0].pieces[0].original_value == "Draft prompt"
    assert save_payload["operator"] == "client-operator"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (PermissionError("Only the owner can save to this attack"), 403),
        (AttackStateConflictError("History changed"), 409),
        (ValueError("The target does not support audio_path"), 422),
    ],
)
def test_save_conversation_maps_service_errors(
    *, save_service: MagicMock, save_payload: dict[str, object], error: Exception, status: int
) -> None:
    application = FastAPI()
    application.include_router(router, prefix="/api")
    save_service.save_conversation_async.side_effect = error
    with (
        patch("pyrit.backend.routes.attacks.get_attack_service", return_value=save_service),
        TestClient(application) as client,
    ):
        response = client.post("/api/attacks/save-conversation", json=save_payload)
    assert response.status_code == status
    assert response.json()["detail"] == str(error)
    save_service.save_conversation_async.assert_awaited_once()
