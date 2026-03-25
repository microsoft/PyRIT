# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend API routes.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.attacks import (
    AddMessageResponse,
    AttackListResponse,
    AttackSummary,
    ConversationMessagesResponse,
    CreateAttackResponse,
    Message,
    MessagePiece,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.converters import (
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewResponse,
    CreateConverterResponse,
    PreviewStep,
)
from pyrit.backend.models.targets import (
    TargetInstance,
    TargetListResponse,
)
from pyrit.backend.routes.labels import get_label_options


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# ============================================================================
# Attack Routes Tests
# ============================================================================


class TestAttackRoutes:
    """Tests for attack API routes."""

    def test_list_attacks_returns_empty_list(self, client: TestClient) -> None:
        """Test that list attacks returns empty list initially."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []

    def test_list_attacks_with_filters(self, client: TestClient) -> None:
        """Test that list attacks accepts filter parameters."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=10, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get(
                "/api/attacks",
                params={"attack_type": "CrescendoAttack", "outcome": "success", "limit": 10},
            )

            assert response.status_code == status.HTTP_200_OK
            mock_service.list_attacks_async.assert_called_once_with(
                attack_type="CrescendoAttack",
                converter_types=None,
                outcome="success",
                labels=None,
                min_turns=None,
                max_turns=None,
                limit=10,
                cursor=None,
            )

    def test_create_attack_success(self, client: TestClient) -> None:
        """Test successful attack creation."""
        now = datetime.now(timezone.utc)

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_attack_async = AsyncMock(
                return_value=CreateAttackResponse(
                    attack_result_id="ar-attack-1",
                    conversation_id="attack-1",
                    created_at=now,
                )
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks",
                json={"target_registry_name": "target-1", "name": "Test Attack"},
            )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["conversation_id"] == "attack-1"

    def test_create_attack_target_not_found(self, client: TestClient) -> None:
        """Test attack creation with non-existent target."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_attack_async = AsyncMock(side_effect=ValueError("Target not found"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks",
                json={"target_registry_name": "nonexistent"},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_attack_success(self, client: TestClient) -> None:
        """Test getting an attack by ID."""
        now = datetime.now(timezone.utc)

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_attack_async = AsyncMock(
                return_value=AttackSummary(
                    attack_result_id="ar-attack-1",
                    conversation_id="attack-1",
                    attack_type="TestAttack",
                    outcome=None,
                    last_message_preview=None,
                    message_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/attack-1")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["conversation_id"] == "attack-1"

    def test_get_attack_not_found(self, client: TestClient) -> None:
        """Test getting a non-existent attack."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_attack_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_attack_success(self, client: TestClient) -> None:
        """Test updating an attack's outcome."""
        now = datetime.now(timezone.utc)

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_attack_async = AsyncMock(
                return_value=AttackSummary(
                    attack_result_id="ar-attack-1",
                    conversation_id="attack-1",
                    attack_type="TestAttack",
                    outcome="success",
                    last_message_preview=None,
                    message_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            mock_get_service.return_value = mock_service

            response = client.patch(
                "/api/attacks/attack-1",
                json={"outcome": "success"},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["outcome"] == "success"

    def test_add_message_success(self, client: TestClient) -> None:
        """Test adding a message to an attack."""
        now = datetime.now(timezone.utc)

        attack_summary = AttackSummary(
            attack_result_id="ar-attack-1",
            conversation_id="attack-1",
            attack_type="TestAttack",
            outcome=None,
            last_message_preview=None,
            message_count=2,
            created_at=now,
            updated_at=now,
        )

        attack_messages = ConversationMessagesResponse(
            conversation_id="attack-1",
            messages=[
                Message(
                    turn_number=1,
                    role="user",
                    pieces=[
                        MessagePiece(
                            piece_id="piece-1",
                            converted_value="Hello",
                        )
                    ],
                    created_at=now,
                ),
                Message(
                    turn_number=2,
                    role="assistant",
                    pieces=[
                        MessagePiece(
                            piece_id="piece-2",
                            converted_value="Hi there!",
                        )
                    ],
                    created_at=now,
                ),
            ],
        )

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.add_message_async = AsyncMock(
                return_value=AddMessageResponse(
                    attack=attack_summary,
                    messages=attack_messages,
                )
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/attack-1/messages",
                json={"pieces": [{"original_value": "Hello"}], "target_conversation_id": "attack-1"},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data["messages"]["messages"]) == 2

    def test_update_attack_not_found(self, client: TestClient) -> None:
        """Test updating a non-existent attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_attack_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.patch(
                "/api/attacks/nonexistent",
                json={"outcome": "success"},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_message_attack_not_found(self, client: TestClient) -> None:
        """Test adding message to non-existent attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.add_message_async = AsyncMock(side_effect=ValueError("Attack 'nonexistent' not found"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/nonexistent/messages",
                json={"pieces": [{"original_value": "Hello"}], "target_conversation_id": "nonexistent"},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_message_target_not_found(self, client: TestClient) -> None:
        """Test adding message when target object not found returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.add_message_async = AsyncMock(side_effect=ValueError("Target object for 'target-1' not found"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/attack-1/messages",
                json={"pieces": [{"original_value": "Hello"}], "target_conversation_id": "attack-1"},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_message_bad_request(self, client: TestClient) -> None:
        """Test adding message with invalid request returns 400."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.add_message_async = AsyncMock(side_effect=ValueError("Invalid message format"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/attack-1/messages",
                json={"pieces": [{"original_value": "Hello"}], "target_conversation_id": "attack-1"},
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_add_message_internal_error(self, client: TestClient) -> None:
        """Test adding message when internal error occurs returns 500."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.add_message_async = AsyncMock(side_effect=RuntimeError("Unexpected internal error"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/attack-1/messages",
                json={"pieces": [{"original_value": "Hello"}], "target_conversation_id": "attack-1"},
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_get_conversation_messages_success(self, client: TestClient) -> None:
        """Test getting attack messages."""
        now = datetime.now(timezone.utc)

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_conversation_messages_async = AsyncMock(
                return_value=ConversationMessagesResponse(
                    conversation_id="attack-1",
                    messages=[
                        Message(
                            turn_number=1,
                            role="user",
                            pieces=[MessagePiece(piece_id="p1", converted_value="Hello")],
                            created_at=now,
                        )
                    ],
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/attack-1/messages", params={"conversation_id": "attack-1"})

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["conversation_id"] == "attack-1"
            assert len(data["messages"]) == 1

    def test_get_conversation_messages_not_found(self, client: TestClient) -> None:
        """Test getting messages for non-existent attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_conversation_messages_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/nonexistent/messages", params={"conversation_id": "nonexistent"})

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_conversation_messages_invalid_conversation_returns_400(self, client: TestClient) -> None:
        """Test getting messages for invalid conversation_id returns 400."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_conversation_messages_async = AsyncMock(
                side_effect=ValueError("conversation does not belong to this attack")
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/attack-1/messages", params={"conversation_id": "wrong-conv"})

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_attacks_with_labels(self, client: TestClient) -> None:
        """Test listing attacks with label filters."""
        now = datetime.now(timezone.utc)

        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[
                        AttackSummary(
                            attack_result_id="ar-attack-1",
                            conversation_id="attack-1",
                            attack_type="TestAttack",
                            outcome=None,
                            last_message_preview=None,
                            message_count=0,
                            labels={"env": "prod"},
                            created_at=now,
                            updated_at=now,
                        )
                    ],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?label=env:prod&label=team:red")

            assert response.status_code == status.HTTP_200_OK
            # Verify labels were parsed and passed to service
            mock_service.list_attacks_async.assert_called_once()
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["labels"] == {"env": "prod", "team": "red"}

    def test_get_attack_options(self, client: TestClient) -> None:
        """Test getting attack type options from attack results."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_attack_options_async = AsyncMock(return_value=["CrescendoAttack", "ManualAttack"])
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/attack-options")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["attack_types"] == ["CrescendoAttack", "ManualAttack"]

    def test_get_converter_options(self, client: TestClient) -> None:
        """Test getting converter options from attack results."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_converter_options_async = AsyncMock(return_value=["Base64Converter", "ROT13Converter"])
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/converter-options")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["converter_types"] == ["Base64Converter", "ROT13Converter"]

    def test_parse_labels_skips_param_without_colon(self, client: TestClient) -> None:
        """Test that _parse_labels skips label params that have no colon."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?label=nocolon&label=env:prod")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            # Only the valid label should be parsed
            assert call_kwargs["labels"] == {"env": "prod"}

    def test_parse_labels_all_invalid_returns_none(self, client: TestClient) -> None:
        """Test that _parse_labels returns None when all params lack colons."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?label=nocolon&label=alsonocolon")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["labels"] is None

    def test_parse_labels_value_with_extra_colons(self, client: TestClient) -> None:
        """Test that _parse_labels handles values containing colons (split on first only)."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?label=url:http://example.com:8080")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["labels"] == {"url": "http://example.com:8080"}

    def test_parse_labels_passes_keys_through_without_normalization(self, client: TestClient) -> None:
        """Test that label keys are passed through as-is (DB stores canonical keys after migration)."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?label=operator:alice&label=operation:redteam")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["labels"] == {"operator": "alice", "operation": "redteam"}

    def test_list_attacks_forwards_converter_types_param(self, client: TestClient) -> None:
        """Test that converter_types query params are forwarded to service."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?converter_types=Base64&converter_types=ROT13")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["converter_types"] == ["Base64", "ROT13"]

    def test_list_attacks_normalizes_empty_converter_types(self, client: TestClient) -> None:
        """Test that empty converter_types strings are stripped so [] means no-converter attacks."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_attacks_async = AsyncMock(
                return_value=AttackListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=20, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks?converter_types=")

            assert response.status_code == status.HTTP_200_OK
            call_kwargs = mock_service.list_attacks_async.call_args[1]
            assert call_kwargs["converter_types"] == []

    def test_get_conversations_success(self, client: TestClient) -> None:
        """Test getting attack conversations returns service response."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_conversations_async = AsyncMock(
                return_value={
                    "attack_result_id": "ar-attack-1",
                    "main_conversation_id": "attack-1",
                    "conversations": [
                        {
                            "conversation_id": "attack-1",
                            "message_count": 2,
                            "last_message_preview": "hello",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ],
                }
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/ar-attack-1/conversations")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["attack_result_id"] == "ar-attack-1"
            assert data["main_conversation_id"] == "attack-1"

    def test_get_conversations_not_found(self, client: TestClient) -> None:
        """Test getting conversations for missing attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_conversations_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/attacks/missing/conversations")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_related_conversation_success(self, client: TestClient) -> None:
        """Test creating related conversation returns 201 response."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_related_conversation_async = AsyncMock(
                return_value={
                    "conversation_id": "branch-1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )
            mock_get_service.return_value = mock_service

            response = client.post("/api/attacks/ar-attack-1/conversations", json={})

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["conversation_id"] == "branch-1"

    def test_create_related_conversation_not_found(self, client: TestClient) -> None:
        """Test creating related conversation for missing attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_related_conversation_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.post("/api/attacks/missing/conversations", json={})

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_main_conversation_success(self, client: TestClient) -> None:
        """Test changing main conversation returns service response."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_main_conversation_async = AsyncMock(
                return_value={
                    "attack_result_id": "ar-attack-1",
                    "conversation_id": "branch-1",
                    "updated_at": "2026-03-06T00:00:00+00:00",
                }
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/ar-attack-1/update-main-conversation",
                json={"conversation_id": "branch-1"},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["conversation_id"] == "branch-1"

    def test_update_main_conversation_bad_request(self, client: TestClient) -> None:
        """Test changing main conversation with invalid conversation returns 400."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_main_conversation_async = AsyncMock(side_effect=ValueError("invalid conversation"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/ar-attack-1/update-main-conversation",
                json={"conversation_id": "missing-conv"},
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_main_conversation_not_found(self, client: TestClient) -> None:
        """Test changing main conversation for missing attack returns 404."""
        with patch("pyrit.backend.routes.attacks.get_attack_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_main_conversation_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/attacks/missing/update-main-conversation",
                json={"conversation_id": "branch-1"},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Target Routes Tests
# ============================================================================


class TestTargetRoutes:
    """Tests for target API routes."""

    def test_list_targets_returns_empty_list(self, client: TestClient) -> None:
        """Test that list targets returns empty list initially."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_targets_async = AsyncMock(
                return_value=TargetListResponse(
                    items=[],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/targets")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []
            assert data["pagination"]["has_more"] is False

    def test_create_target_success(self, client: TestClient) -> None:
        """Test successful target creation."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_target_async = AsyncMock(
                return_value=TargetInstance(
                    target_registry_name="target-1",
                    target_type="TextTarget",
                )
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/targets",
                json={"type": "TextTarget", "params": {}},
            )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["target_registry_name"] == "target-1"

    def test_create_target_invalid_type(self, client: TestClient) -> None:
        """Test target creation with invalid type."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_target_async = AsyncMock(side_effect=ValueError("Target type not found"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/targets",
                json={"type": "InvalidTarget", "params": {}},
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_target_internal_error(self, client: TestClient) -> None:
        """Test target creation with internal error returns 500."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_target_async = AsyncMock(side_effect=RuntimeError("Unexpected error"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/targets",
                json={"type": "TextTarget", "params": {}},
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_get_target_success(self, client: TestClient) -> None:
        """Test getting a target by ID."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_target_async = AsyncMock(
                return_value=TargetInstance(
                    target_registry_name="target-1",
                    target_type="TextTarget",
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/targets/target-1")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["target_registry_name"] == "target-1"

    def test_get_target_not_found(self, client: TestClient) -> None:
        """Test getting a non-existent target."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_target_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/targets/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_targets_includes_target_specific_params(self, client: TestClient) -> None:
        """Test that target_specific_params (e.g. reasoning_effort) are included in list response."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_targets_async = AsyncMock(
                return_value=TargetListResponse(
                    items=[
                        TargetInstance(
                            target_registry_name="azure_responses",
                            target_type="OpenAIResponseTarget",
                            endpoint="https://api.openai.com",
                            model_name="o3",
                            temperature=1.0,
                            target_specific_params={
                                "reasoning_effort": "high",
                                "reasoning_summary": "auto",
                                "max_output_tokens": 4096,
                            },
                        )
                    ],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/targets")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            target = data["items"][0]
            assert target["target_specific_params"]["reasoning_effort"] == "high"
            assert target["target_specific_params"]["reasoning_summary"] == "auto"
            assert target["target_specific_params"]["max_output_tokens"] == 4096

    def test_get_target_includes_target_specific_params(self, client: TestClient) -> None:
        """Test that target_specific_params are included in single-target response."""
        with patch("pyrit.backend.routes.targets.get_target_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_target_async = AsyncMock(
                return_value=TargetInstance(
                    target_registry_name="azure_chat",
                    target_type="OpenAIChatTarget",
                    endpoint="https://api.openai.com",
                    model_name="gpt-4",
                    temperature=0.7,
                    target_specific_params={
                        "frequency_penalty": 0.5,
                        "presence_penalty": 0.3,
                        "seed": 42,
                    },
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/targets/azure_chat")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["target_specific_params"]["frequency_penalty"] == 0.5
            assert data["target_specific_params"]["presence_penalty"] == 0.3
            assert data["target_specific_params"]["seed"] == 42


# ============================================================================
# Converter Routes Tests
# ============================================================================


class TestConverterRoutes:
    """Tests for converter API routes."""

    def test_list_converters(self, client: TestClient) -> None:
        """Test listing converter instances."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_converters_async = AsyncMock(return_value=ConverterInstanceListResponse(items=[]))
            mock_get_service.return_value = mock_service

            response = client.get("/api/converters")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []

    def test_create_converter_success(self, client: TestClient) -> None:
        """Test successful converter instance creation."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_converter_async = AsyncMock(
                return_value=CreateConverterResponse(
                    converter_id="conv-1",
                    converter_type="Base64Converter",
                    display_name="My Base64",
                )
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters",
                json={"type": "Base64Converter", "display_name": "My Base64", "params": {}},
            )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["converter_id"] == "conv-1"

    def test_create_converter_invalid_type(self, client: TestClient) -> None:
        """Test converter creation with invalid type."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_converter_async = AsyncMock(side_effect=ValueError("Converter type not found"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters",
                json={"type": "InvalidConverter", "params": {}},
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_converter_internal_error(self, client: TestClient) -> None:
        """Test converter creation with internal error returns 500."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_converter_async = AsyncMock(side_effect=RuntimeError("Unexpected error"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters",
                json={"type": "Base64Converter", "params": {}},
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_get_converter_success(self, client: TestClient) -> None:
        """Test getting a converter instance by ID."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_converter_async = AsyncMock(
                return_value=ConverterInstance(
                    converter_id="conv-1",
                    converter_type="Base64Converter",
                    display_name=None,
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/converters/conv-1")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["converter_id"] == "conv-1"

    def test_get_converter_not_found(self, client: TestClient) -> None:
        """Test getting a non-existent converter instance."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_converter_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/converters/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_preview_conversion_success(self, client: TestClient) -> None:
        """Test previewing a conversion."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.preview_conversion_async = AsyncMock(
                return_value=ConverterPreviewResponse(
                    original_value="test",
                    original_value_data_type="text",
                    converted_value="dGVzdA==",
                    converted_value_data_type="text",
                    steps=[
                        PreviewStep(
                            converter_id="conv-1",
                            converter_type="Base64Converter",
                            input_value="test",
                            input_data_type="text",
                            output_value="dGVzdA==",
                            output_data_type="text",
                        )
                    ],
                )
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters/preview",
                json={
                    "original_value": "test",
                    "original_value_data_type": "text",
                    "converter_ids": ["conv-1"],
                },
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["converted_value"] == "dGVzdA=="
            assert len(data["steps"]) == 1

    def test_preview_conversion_bad_request(self, client: TestClient) -> None:
        """Test preview conversion with invalid converter ID returns 400."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.preview_conversion_async = AsyncMock(
                side_effect=ValueError("Converter instance 'nonexistent' not found")
            )
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters/preview",
                json={
                    "original_value": "test",
                    "original_value_data_type": "text",
                    "converter_ids": ["nonexistent"],
                },
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_conversion_internal_error(self, client: TestClient) -> None:
        """Test preview conversion with internal error returns 500."""
        with patch("pyrit.backend.routes.converters.get_converter_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.preview_conversion_async = AsyncMock(side_effect=RuntimeError("Converter execution failed"))
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/converters/preview",
                json={
                    "original_value": "test",
                    "original_value_data_type": "text",
                    "converter_ids": ["conv-1"],
                },
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# ============================================================================
# Version Routes Tests
# ============================================================================


class TestVersionRoutes:
    """Tests for version API routes."""

    def test_get_version(self, client: TestClient) -> None:
        """Test getting version information."""
        response = client.get("/api/version")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "version" in data
        assert "display" in data

    def test_get_version_with_build_info(self, client: TestClient) -> None:
        """Test getting version with build info from Docker."""
        # Create a temp file to simulate Docker build info
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "source": "git",
                    "commit": "abc123",
                    "modified": False,
                    "display": "1.0.0-test",
                },
                f,
            )
            temp_path = f.name

        try:
            with patch("pyrit.backend.routes.version.Path") as mock_path_class:
                mock_path_instance = MagicMock()
                mock_path_instance.exists.return_value = True
                mock_path_class.return_value = mock_path_instance

                # Mock open to return our temp file content
                with patch("builtins.open", create=True) as mock_open:
                    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(
                        {
                            "source": "git",
                            "commit": "abc123",
                            "modified": False,
                            "display": "1.0.0-test",
                        }
                    )

                    response = client.get("/api/version")

            assert response.status_code == status.HTTP_200_OK
        finally:
            os.unlink(temp_path)

    def test_get_version_build_info_load_failure(self, client: TestClient) -> None:
        """Test getting version when build_info.json exists but fails to load."""
        with patch("pyrit.backend.routes.version.Path") as mock_path_class:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_class.return_value = mock_path_instance

            with patch("builtins.open", side_effect=OSError("permission denied")):
                response = client.get("/api/version")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Falls back to default values when load fails
        assert "version" in data
        assert data["source"] is None
        assert data["commit"] is None


# ============================================================================
# Health Routes Tests
# ============================================================================


class TestHealthRoutes:
    """Tests for health check API routes."""

    def test_health_check(self, client: TestClient) -> None:
        """Test health check endpoint returns ok."""
        response = client.get("/api/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"


# ============================================================================
# Labels Routes Tests
# ============================================================================


class TestLabelsRoutes:
    """Tests for labels API routes."""

    def test_get_labels_for_attacks(self, client: TestClient) -> None:
        """Test getting labels from attack results."""
        with patch("pyrit.backend.routes.labels.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_unique_attack_labels.return_value = {"env": ["prod"], "team": ["red"]}
            mock_memory_class.get_memory_instance.return_value = mock_memory

            response = client.get("/api/labels?source=attacks")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["source"] == "attacks"
            assert data["labels"] == {"env": ["prod"], "team": ["red"]}
            mock_memory.get_unique_attack_labels.assert_called_once()

    def test_get_labels_empty(self, client: TestClient) -> None:
        """Test getting labels when no attack results exist."""
        with patch("pyrit.backend.routes.labels.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_unique_attack_labels.return_value = {}
            mock_memory_class.get_memory_instance.return_value = mock_memory

            response = client.get("/api/labels?source=attacks")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["source"] == "attacks"
            assert data["labels"] == {}

    def test_get_labels_multiple_values(self, client: TestClient) -> None:
        """Test getting labels with multiple values per key."""
        with patch("pyrit.backend.routes.labels.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_unique_attack_labels.return_value = {
                "env": ["prod", "staging"],
                "team": ["blue"],
            }
            mock_memory_class.get_memory_instance.return_value = mock_memory

            response = client.get("/api/labels")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert set(data["labels"]["env"]) == {"prod", "staging"}
            assert data["labels"]["team"] == ["blue"]

    def test_get_labels_returns_keys_without_normalization(self, client: TestClient) -> None:
        """Test that label keys are returned as-is from the DB (canonical after migration)."""
        with patch("pyrit.backend.routes.labels.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_unique_attack_labels.return_value = {
                "operator": ["alice", "bob"],
                "operation": ["hunt", "scan"],
            }
            mock_memory_class.get_memory_instance.return_value = mock_memory

            response = client.get("/api/labels")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert set(data["labels"]["operator"]) == {"alice", "bob"}
            assert set(data["labels"]["operation"]) == {"hunt", "scan"}

    @pytest.mark.asyncio
    async def test_get_label_options_unsupported_source_returns_empty_labels(self) -> None:
        """Test that get_label_options returns empty labels for unsupported source types."""
        with patch("pyrit.backend.routes.labels.CentralMemory"):
            # Call the function directly with a non-"attacks" source to cover the else branch.
            # The Literal["attacks"] type hint prevents this via the API, but the function
            # handles it gracefully.
            result = await get_label_options(source="other")  # type: ignore[arg-type]

        assert result.source == "other"
        assert result.labels == {}
