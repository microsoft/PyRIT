# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the shared synchronous manual-message owner."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Generator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pyrit.backend.mappers.attack_mappers import pyrit_messages_to_dto_async
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    ConverterConfigurationRequest,
    MessagePieceRequest,
)
from pyrit.backend.models.message_sends import (
    MessageSendFailureStage,
    MessageSendRequest,
    MessageSendState,
    MessageSendStatus,
    RequestConverterMode,
)
from pyrit.backend.services.converter_service import ConverterService
from pyrit.backend.services.manual_send_scheduler import (
    ManualSendConflictError,
    ManualSendQueueFullError,
    ManualSendScheduler,
    get_manual_send_scheduler,
)
from pyrit.backend.services.media_url_import import MediaDownload
from pyrit.backend.services.message_send_service import (
    MessageSendNotFoundError,
    MessageSendService,
    resolve_applied_converter_identifiers,
)
from pyrit.backend.services.target_service import TargetService
from pyrit.common.attack_result_scope import get_current_attack_result_id
from pyrit.common.utils import to_sha256
from pyrit.converter import Base64Converter, Converter, ConverterResult
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackResult,
    ComponentIdentifier,
    Conversation,
    ConversationReference,
    ConversationType,
    Message,
    MessagePiece,
    PromptDataType,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from unit.async_utils import wait_for_completion_async
from unit.backend.mocks import (
    _make_matching_target_mock,
    _settle_send_async,
    make_attack_result,
    make_mock_memory,
    message_send_lifecycle_async,
)
from unit.mocks import MockPromptTarget


@pytest.fixture
def mock_memory(patch_central_database: MagicMock) -> Iterator[MagicMock]:
    memory = make_mock_memory()
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        yield memory


def _stored_media(value: str, *, root: Path) -> str:
    """Expand ``media:<name>`` to an ``/api/media`` reference and ``stored:<name>`` to its stored path."""
    kind, _, name = value.partition(":")
    stored_path = root / "prompt-memory-entries" / name
    if kind == "media":
        return f"/api/media?path={stored_path}"
    return str(stored_path) if kind == "stored" else value


@pytest.fixture
def message_send_service(mock_memory: MagicMock) -> MessageSendService:
    return MessageSendService(scheduler=ManualSendScheduler())


@pytest.fixture
def send_dependencies(mock_memory: MagicMock) -> Iterator[tuple[MagicMock, AsyncMock]]:
    ar = make_attack_result(conversation_id="main", attack_result_id="attack")
    ar.related_conversations = {
        ConversationReference(conversation_id=name, conversation_type=ConversationType.PRUNED)
        for name in ["second", "third"]
    }
    mock_memory.get_attack_results_async.return_value = [ar]
    target_service = MagicMock(spec=TargetService)
    converter_service = MagicMock(spec=ConverterService)
    send = AsyncMock(spec=PromptNormalizer.send_prompt_async)
    with (
        patch("pyrit.backend.services.message_send_service.get_target_service", return_value=target_service),
        patch("pyrit.backend.services.message_send_service.get_converter_service", return_value=converter_service),
        patch.object(PromptNormalizer, "send_prompt_async", new=send),
    ):
        target_service.get_target_object.return_value = _make_matching_target_mock()
        converter_service.get_converter_objects_for_ids.return_value = [Base64Converter()]
        yield target_service, send


@pytest.fixture
async def real_send_context(
    *, sqlite_instance: SQLiteMemory, patch_central_database: MagicMock
) -> Iterator[tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter]]:
    target = MockPromptTarget()
    converter = Base64Converter()
    ar = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="test",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack", class_module="pyrit.backend", objective_target=target.get_identifier()
            )
        ),
    )
    (await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar]))
    with (
        patch("pyrit.backend.services.message_send_service.get_target_service") as target_service,
        patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service,
    ):
        target_service.return_value.get_target_object.return_value = target
        converter_service.return_value.get_converter_objects_for_ids.return_value = [converter]
        yield MessageSendService(scheduler=ManualSendScheduler()), ar, target, converter


def _request(*, conversation_id: str = "main", send: bool = True) -> AddMessageRequest:
    return AddMessageRequest(
        pieces=[MessagePieceRequest(original_value="Hello")],
        target_conversation_id=conversation_id,
        target_registry_name="target" if send else None,
        send=send,
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_manual_send_exposes_and_persists_ownership_async(
    *,
    sqlite_instance: SQLiteMemory,
    real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
) -> None:
    service, attack, target, _ = real_send_context
    received: list[str | None] = []
    original_send = target._send_prompt_to_target_async

    async def record_async(*, normalized_conversation: list[Message]) -> list[Message]:
        received.append(get_current_attack_result_id())
        return await original_send(normalized_conversation=normalized_conversation)

    with patch.object(target, "_send_prompt_to_target_async", side_effect=record_async):
        await service.add_message_async(
            attack_result_id=attack.attack_result_id,
            request=_request(conversation_id=attack.conversation_id),
        )
    assert received == [attack.attack_result_id]
    assert get_current_attack_result_id() is None
    [owned] = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id)
    assert owned.conversation_id == attack.conversation_id
    assert owned.target_identifier == target.get_identifier()
    pieces = await sqlite_instance.get_message_pieces_async(attack_result_id=attack.attack_result_id)
    assert [piece.role for piece in pieces] == ["user", "assistant"]


@contextmanager
def _observe_operation(scheduler: ManualSendScheduler) -> Generator[asyncio.Event, None, None]:
    waiting = asyncio.Event()
    operation = scheduler.operation_async

    @asynccontextmanager
    async def observe_async() -> AsyncGenerator[None, None]:
        waiting.set()
        async with operation():
            yield

    with patch.object(scheduler, "operation_async", side_effect=observe_async):
        yield waiting


async def _send_message_and_get_update_fields(
    *,
    message_send_service: MessageSendService,
    mock_memory: MagicMock,
    attack_result_id: str,
    request: AddMessageRequest,
    attack_result: AttackResult,
    converter_identifiers: list[ComponentIdentifier],
) -> dict[str, Any]:
    """
    Send a message with common mocks and return the attack result update fields.

    Args:
        message_send_service: The service under test.
        mock_memory: The mocked memory instance used by the service.
        attack_result_id: The attack result identifier passed to the service.
        request: The message request to send.
        attack_result: The attack result returned by memory.
        converter_identifiers: The identifiers returned by resolved converters.

    Returns:
        dict[str, Any]: The fields used to update the attack result.
    """
    mock_memory.get_attack_results_async.return_value = [attack_result]
    mock_memory.get_message_pieces_async.return_value = []

    converter_objects: list[MagicMock] = []
    for identifier in converter_identifiers:
        converter = MagicMock()
        converter.get_identifier.return_value = identifier
        converter_objects.append(converter)

    with (
        patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_converter_service,
        patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_service,
        patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_class,
    ):
        mock_converter_service = MagicMock()
        mock_converter_service.get_converter_objects_for_ids.return_value = converter_objects
        mock_get_converter_service.return_value = mock_converter_service
        mock_get_target_service.return_value.get_target_object.return_value = _make_matching_target_mock()
        mock_normalizer_class.return_value.send_prompt_async = AsyncMock()

        await message_send_service.add_message_async(attack_result_id=attack_result_id, request=request)
    return mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]


def _make_round_robin_identifier(
    *,
    second_model_name: str = "e2e-dummy-model",
    weights: tuple[int, int] = (1, 1),
) -> ComponentIdentifier:
    """Create a composite target identifier with no root endpoint or model."""
    return ComponentIdentifier(
        class_name="RoundRobinTarget",
        class_module="pyrit.prompt_target.round_robin_target",
        params={"weights": list(weights)},
        children={
            "targets": [
                ComponentIdentifier(
                    class_name="TextTarget",
                    class_module="pyrit.prompt_target",
                    params={"model_name": "e2e-dummy-model"},
                ),
                ComponentIdentifier(
                    class_name="TextTarget",
                    class_module="pyrit.prompt_target",
                    params={"model_name": second_model_name},
                ),
            ]
        },
    )


@pytest.mark.usefixtures("patch_central_database")
class TestAddMessage:
    """Synchronous sending and converter contracts moved with their owner."""

    async def test_add_message_raises_for_nonexistent_attack(self, message_send_service, mock_memory) -> None:
        """Test that add_message raises ValueError for nonexistent attack."""
        mock_memory.get_attack_results_async.return_value = []

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="not found"):
            await message_send_service.add_message_async(attack_result_id="nonexistent", request=request)
        assert not message_send_service._scheduler._conversations

    async def test_add_message_raises_when_send_without_registry_name(self, message_send_service, mock_memory) -> None:
        """Test that add_message raises ValueError when send=True but target_registry_name missing."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=True,
        )

        with pytest.raises(ValueError, match="target_registry_name is required when send=True"):
            await message_send_service.add_message_async(attack_result_id="test-id", request=request)
        assert not message_send_service._scheduler._conversations

    async def test_add_message_with_send_sends_via_normalizer(self, message_send_service, mock_memory) -> None:
        """Test that add_message with send=True sends message via normalizer."""
        ar = make_attack_result(conversation_id="test-id")
        response_piece = MessagePiece(
            role="assistant",
            original_value="Response",
            original_value_data_type="text",
            converted_value="Response",
            converted_value_data_type="text",
            conversation_id="test-id",
            sequence=1,
        )
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.side_effect = [[], [response_piece]]
        mock_memory.get_conversation_messages_async.return_value = []

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

            mock_normalizer.send_prompt_async.assert_called_once()
            update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
            assert update_fields["last_response_id"] == str(response_piece.id)

    async def test_add_message_with_send_raises_when_target_not_found(self, message_send_service, mock_memory) -> None:
        """Test that add_message with send=True raises when target object not found."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        with patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc:
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = None
            mock_get_target_svc.return_value = mock_target_svc

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            with pytest.raises(ValueError, match="Target object .* not found"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_reraises_when_send_fails_without_stored_error_piece(
        self, message_send_service, mock_memory
    ) -> None:
        """If the send fails but no error piece was stored, the exception propagates (real error)."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []  # no error piece ever stored
        mock_memory.get_conversation_messages_async.return_value = []

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock(side_effect=RuntimeError("boom"))
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            with pytest.raises(RuntimeError, match="boom"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_with_legacy_converter_ids_warns_and_preserves_behavior(
        self, message_send_service, mock_memory
    ) -> None:
        """Test that legacy converter IDs warn and remain an unrestricted pipeline."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            first_converter = MagicMock()
            first_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="FirstConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            second_converter = MagicMock()
            second_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="SecondConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            mock_conv_svc = MagicMock()
            mock_conv_svc.get_converter_objects_for_ids.return_value = [first_converter, second_converter]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                converter_ids=["first", "second"],
                target_registry_name="test-target",
            )

            with pytest.warns(DeprecationWarning, match="AddMessageRequest.converter_ids is deprecated"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)

            configurations = mock_normalizer.send_prompt_async.call_args.kwargs["request_converter_configurations"]
            assert [configuration.converters for configuration in configurations] == [
                [first_converter],
                [second_converter],
            ]
            assert all(configuration.indexes_to_apply is None for configuration in configurations)
            mock_conv_svc.get_converter_objects_for_ids.assert_called_once_with(converter_ids=["first", "second"])

    def test_empty_legacy_converter_ids_allow_store_only_request(self) -> None:
        """Test that an empty legacy converter list remains a no-op when send is false."""
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            converter_ids=[],
        )

        assert request.converter_ids == []

    def test_empty_legacy_converter_ids_warn_and_use_structured_configuration(self, message_send_service) -> None:
        """Test that an empty legacy list does not override a structured configuration."""
        converter = MagicMock()
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            converter_ids=[],
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["structured"])],
        )

        with patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_converter_service:
            mock_get_converter_service.return_value.get_converter_objects_for_ids.return_value = [converter]

            with pytest.warns(DeprecationWarning, match="AddMessageRequest.converter_ids is deprecated"):
                configurations = message_send_service._resolve_request_converter_configs(request=request)

        assert len(configurations) == 1
        assert configurations[0].converters == [converter]
        mock_get_converter_service.return_value.get_converter_objects_for_ids.assert_called_once_with(
            converter_ids=["structured"]
        )

    async def test_add_message_preserves_converter_configuration_targeting(
        self, message_send_service, mock_memory
    ) -> None:
        """Test that request and response converter targeting reaches the normalizer."""
        mock_memory.results_path = "https://account.blob.core.windows.net/results"
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_conv_svc = MagicMock()
            first_request_converter = MagicMock()
            first_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="FirstRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            second_request_converter = MagicMock()
            second_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="SecondRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            third_request_converter = MagicMock()
            third_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="ThirdRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("image_path",), "supported_output_types": ("image_path",)},
            )
            response_converter = MagicMock()
            response_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="ResponseConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            converters_by_ids = {
                ("request-1", "request-2"): [first_request_converter, second_request_converter],
                ("request-3",): [third_request_converter],
                ("response-1",): [response_converter],
            }
            mock_conv_svc.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: converters_by_ids[
                tuple(converter_ids)
            ]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[
                    MessagePieceRequest(original_value="Hello"),
                    MessagePieceRequest(
                        data_type="image_path",
                        original_value="https://account.blob.core.windows.net/results/prompt-memory-entries/image.png",
                    ),
                ],
                target_conversation_id="test-id",
                send=True,
                request_converter_configurations=[
                    ConverterConfigurationRequest(
                        converter_ids=["request-1", "request-2"],
                        indexes_to_apply=[0],
                        prompt_data_types_to_apply=["text"],
                    ),
                    ConverterConfigurationRequest(
                        converter_ids=["request-3"],
                        indexes_to_apply=[1],
                        prompt_data_types_to_apply=["image_path"],
                    ),
                ],
                response_converter_configurations=[
                    ConverterConfigurationRequest(
                        converter_ids=["response-1"],
                        indexes_to_apply=[1],
                        prompt_data_types_to_apply=["text"],
                    )
                ],
                target_registry_name="test-target",
            )

            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

            call_kwargs = mock_normalizer.send_prompt_async.call_args.kwargs
            request_configs = call_kwargs["request_converter_configurations"]
            assert request_configs[0].converters == [first_request_converter, second_request_converter]
            assert request_configs[0].indexes_to_apply == [0]
            assert request_configs[0].prompt_data_types_to_apply == ["text"]
            assert request_configs[1].converters == [third_request_converter]
            assert request_configs[1].indexes_to_apply == [1]
            assert request_configs[1].prompt_data_types_to_apply == ["image_path"]
            response_config = call_kwargs["response_converter_configurations"][0]
            assert response_config.converters == [response_converter]
            assert response_config.indexes_to_apply == [1]
            assert response_config.prompt_data_types_to_apply == ["text"]

            update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
            updated_atomic = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
            updated_attack = updated_atomic.attack_technique.attack
            assert [converter.class_name for converter in updated_attack.request_converters] == [
                "FirstRequestConverter",
                "SecondRequestConverter",
                "ThirdRequestConverter",
            ]
            assert [converter.class_name for converter in updated_attack.response_converters] == ["ResponseConverter"]
            assert mock_conv_svc.get_converter_objects_for_ids.call_count == 3

    async def test_add_message_resolves_converters_before_writing(self, message_send_service, mock_memory) -> None:
        """Test that an unknown converter fails before message or attack writes."""
        ar = make_attack_result(conversation_id="test-id", has_target=False)
        mock_memory.get_attack_results_async.return_value = [ar]
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["missing"])],
        )

        with patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_service:
            mock_get_service.return_value.get_converter_objects_for_ids.side_effect = ValueError(
                "Converter instance 'missing' not found"
            )

            with pytest.raises(ValueError, match="Converter instance 'missing' not found"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)

        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()
        mock_memory.update_attack_result_by_id_async.assert_not_called()
        assert not message_send_service._scheduler._conversations

    async def test_add_message_bumps_timestamp(self, message_send_service, mock_memory) -> None:
        "Should bump the timestamp recency column via update_attack_result_async (no metadata write)."
        ar = make_attack_result(conversation_id="test-id")
        ar.metadata = {"created_at": "2026-01-01T00:00:00+00:00"}
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        await message_send_service.add_message_async(attack_result_id="test-id", request=request)

        mock_memory.update_attack_result_by_id_async.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id_async.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        update_fields = call_kwargs["update_fields"]
        assert isinstance(update_fields["timestamp"], datetime)
        assert "attack_metadata" not in update_fields

    async def test_preconverted_piece_does_not_disable_other_piece_converters(
        self, message_send_service, mock_memory
    ) -> None:
        """Test that only the client-preconverted piece is excluded from conversion."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        mock_converter = MagicMock()
        mock_converter.get_identifier.return_value = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_conv_svc = MagicMock()
            mock_conv_svc.get_converter_objects_for_ids.return_value = [mock_converter]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[
                    MessagePieceRequest(
                        original_value="Hello", converted_value="SGVsbG8=", applied_converter_ids=["conv-1"]
                    ),
                    MessagePieceRequest(original_value="World"),
                ],
                send=True,
                target_conversation_id="test-id",
                request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["conv-1"])],
                response_converter_configurations=[ConverterConfigurationRequest(converter_ids=["conv-1"])],
                target_registry_name="test-target",
            )

            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

            call_kwargs = mock_normalizer.send_prompt_async.call_args[1]
            request_configurations = call_kwargs["request_converter_configurations"]
            assert len(request_configurations) == 1
            assert request_configurations[0].indexes_to_apply == [1]
            sent_pieces = call_kwargs["message"].message_pieces
            assert sent_pieces[0].original_value == "Hello"
            assert sent_pieces[0].converted_value == "SGVsbG8="
            assert [identifier.class_name for identifier in sent_pieces[0].converter_identifiers] == ["Base64Converter"]
            assert sent_pieces[1].converter_identifiers == []
            assert len(call_kwargs["response_converter_configurations"]) == 1
            update_call = mock_memory.update_attack_result_by_id_async.call_args[1]
            assert "atomic_attack_identifier" in update_call["update_fields"]

    def test_preconverted_piece_omits_configuration_with_no_eligible_indexes(self, message_send_service) -> None:
        """Test that an empty filtered selector is omitted instead of becoming unrestricted."""
        configuration = ConverterConfiguration(converters=[MagicMock()], indexes_to_apply=[0])

        result = message_send_service._exclude_preconverted_piece_indexes(
            configurations=[configuration],
            preconverted_indexes={0},
            piece_count=2,
        )

        assert result == []

    async def test_rejects_unrelated_conversation_id(self, message_send_service, mock_memory):
        """Writing to a conversation_id that doesn't belong to the attack should raise ValueError."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="Hello")],
            send=False,
            target_conversation_id="unrelated-conv",
        )

        with pytest.raises(ValueError, match="not part of attack"):
            await message_send_service.add_message_async(attack_result_id="ar-attack-1", request=request)


@pytest.mark.usefixtures("patch_central_database")
class TestPersistBase64Pieces:
    """Tests for _persist_base64_pieces_async helper."""

    @pytest.mark.parametrize(
        ("original_type", "original_value", "converted_type", "converted_value", "expected_types", "extensions"),
        [
            (
                "text",
                "source",
                "image_path",
                "data:image/png;base64,cHJldmlldw==",
                ["image_path"],
                [".png"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "text",
                "Exact description",
                ["image_path"],
                [".png"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "audio_path",
                "data:audio/wav;base64,cHJldmlldw==",
                ["image_path", "audio_path"],
                [".png", ".wav"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "image_path",
                "data:image/jpeg;base64,cHJldmlldw==",
                ["image_path", "image_path"],
                [".png", ".jpg"],
            ),
        ],
    )
    async def test_persists_original_and_converted_media_independently_async(
        self,
        *,
        original_type: PromptDataType,
        original_value: str,
        converted_type: PromptDataType,
        converted_value: str,
        expected_types: list[PromptDataType],
        extensions: list[str],
    ) -> None:
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type=original_type,
                    original_value=original_value,
                    converted_value=converted_value,
                    converted_value_data_type=converted_type,
                    mime_type="image/png" if original_type == "image_path" else "text/plain",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        serializers = [MagicMock(value=f"saved-{index}{extension}") for index, extension in enumerate(extensions)]
        for serializer in serializers:
            serializer.save_b64_image_async = AsyncMock()

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory", side_effect=serializers
        ) as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert [call.kwargs["data_type"] for call in factory.call_args_list] == expected_types
        assert [call.kwargs["extension"] for call in factory.call_args_list] == extensions
        piece = request.pieces[0]
        assert piece.data_type == original_type
        assert piece.converted_value_data_type == converted_type
        assert piece.original_value == (serializers[0].value if original_type == "image_path" else original_value)
        assert piece.converted_value == (converted_value if converted_type == "text" else serializers[-1].value)
        for serializer in serializers:
            serializer.save_b64_image_async.assert_awaited_once()

    @pytest.mark.parametrize("stored_in_blob", [False, True])
    async def test_converted_media_references_are_not_repersisted_async(
        self, *, mock_memory: MagicMock, tmp_path: Path, stored_in_blob: bool
    ) -> None:
        stored_path = (tmp_path / "prompt-memory-entries" / "preview.png").resolve()
        if stored_in_blob:
            mock_memory.results_path = "https://account.blob.core.windows.net/results"
            expected_value = f"{mock_memory.results_path}/prompt-memory-entries/preview.png"
            converted_value = f"{expected_value}?sv=1"
        else:
            mock_memory.results_path = str(tmp_path)
            converted_value, expected_value = f"/api/media?path={stored_path}", str(stored_path)
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value="source",
                    converted_value=converted_value,
                    converted_value_data_type="image_path",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "source"
        assert request.pieces[0].converted_value == expected_value
        factory.assert_not_called()

    async def test_converted_media_outside_results_is_rejected_async(
        self, *, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        mock_memory.results_path = str(tmp_path)
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value="source",
                    converted_value="/api/media?path=/etc/hostname",
                    converted_value_data_type="image_path",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="results directory"):
            await MessageSendService._persist_base64_pieces_async(request)

    async def test_imported_url_is_stored_before_sending_async(self, *, tmp_path: Path) -> None:
        stored = str(tmp_path / "prompt-memory-entries" / "imported.png")
        serializer = MagicMock(value=stored)
        serializer.save_data_async = AsyncMock()
        download = MediaDownload(content=b"PNG", content_type="image/png", final_url="https://example.com/cat")
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type="image_path", original_value="https://example.com/cat?sig=secret", import_url=True
                )
            ],
            send=True,
            target_conversation_id="test-id",
        )

        with (
            patch(
                "pyrit.backend.services.media_persistence.download_media_url_async", AsyncMock(return_value=download)
            ) as download_mock,
            patch("pyrit.backend.services.message_send_service.data_serializer_factory", return_value=serializer),
        ):
            await MessageSendService._persist_base64_pieces_async(request)

        download_mock.assert_awaited_once_with(url="https://example.com/cat?sig=secret")
        piece = request.pieces[0]
        assert (piece.data_type, piece.original_value) == ("image_path", stored)
        assert "sig=secret" not in piece.model_dump_json()

    async def test_identical_original_and_converted_media_saved_once_async(self) -> None:
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,c291cmNl",
                    converted_value="data:image/png;base64,c291cmNl",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        serializer = MagicMock(value="saved.png")
        serializer.save_b64_image_async = AsyncMock()
        with patch("pyrit.backend.services.message_send_service.data_serializer_factory", return_value=serializer):
            await MessageSendService._persist_base64_pieces_async(request)

        serializer.save_b64_image_async.assert_awaited_once()
        assert request.pieces[0].original_value == "saved.png"
        assert request.pieces[0].converted_value == "saved.png"

    async def test_converted_media_failure_preserves_both_request_values_async(self) -> None:
        piece = MessagePieceRequest(
            data_type="image_path",
            original_value="data:image/png;base64,c291cmNl",
            converted_value="data:image/png;base64,cHJldmlldw==",
        )
        request = AddMessageRequest(pieces=[piece], send=False, target_conversation_id="test-id")
        before = piece.model_dump()
        original_serializer = MagicMock(value="source.png")
        original_serializer.save_b64_image_async = AsyncMock()
        converted_serializer = MagicMock()
        converted_serializer.save_b64_image_async = AsyncMock(side_effect=OSError("preview save failed"))
        with (
            patch(
                "pyrit.backend.services.message_send_service.data_serializer_factory",
                side_effect=[original_serializer, converted_serializer],
            ),
            pytest.raises(OSError, match="preview save failed"),
        ):
            await MessageSendService._persist_base64_pieces_async(request)

        assert piece.model_dump() == before

    async def test_text_pieces_are_unchanged(self, message_send_service) -> None:
        """Text pieces should not be modified."""
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="hello")],
            send=False,
            target_conversation_id="test-id",
        )
        await MessageSendService._persist_base64_pieces_async(request)
        assert request.pieces[0].original_value == "hello"

    @pytest.mark.parametrize(
        "converted_value", [None, "https://account.blob.core.windows.net/results/prompt-memory-entries/converted.png"]
    )
    async def test_image_piece_is_saved_to_file(
        self, *, message_send_service: MessageSendService, mock_memory: MagicMock, converted_value: str | None
    ) -> None:
        """Base64 image data should be saved to disk and value replaced with file path."""
        mock_memory.results_path = "https://account.blob.core.windows.net/results"
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",  # base64 for "imagedata"
                    mime_type="image/png",
                    converted_value=converted_value,
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await MessageSendService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"
        assert request.pieces[0].converted_value == (converted_value or "/saved/image.png")

    async def test_mixed_pieces_only_persists_non_text(self, message_send_service) -> None:
        """Only non-text pieces should be persisted; text pieces stay untouched."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(data_type="text", original_value="describe this"),
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="base64data",
                    mime_type="image/jpeg",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/photo.jpg"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "describe this"
        assert request.pieces[1].original_value == "/saved/photo.jpg"

    async def test_unknown_mime_type_uses_bin_extension(self, message_send_service) -> None:
        """When mime_type is missing, .bin should be used as fallback extension."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="binary_path",
                    original_value="base64data",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/file.bin"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await MessageSendService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="binary_path",
            extension=".bin",
        )

    async def test_data_uri_prefix_is_stripped_before_saving(self, message_send_service) -> None:
        """Data URIs (data:<mime>;base64,...) should be stripped to raw base64 before saving."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,aW1hZ2VkYXRh",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await MessageSendService._persist_base64_pieces_async(request)

        # Should receive only the base64 payload, not the data URI prefix
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_data_uri_mime_type_supplies_extension_when_mime_type_missing(self, message_send_service) -> None:
        """Data URI media type should prevent image uploads from falling back to blocked .bin files."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,aW1hZ2VkYXRh",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await MessageSendService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_path_data_type_supplies_extension_when_mime_type_missing(self, message_send_service) -> None:
        """Raw image base64 without MIME metadata should still use a media-serving extension."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.message_send_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await MessageSendService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_http_url_is_kept_without_query(self, message_send_service, mock_memory: MagicMock) -> None:
        """HTTPS blob URLs inside the results container are kept as references, without their query string."""
        mock_memory.results_path = "https://myblob.blob.core.windows.net/results"
        blob_url = "https://myblob.blob.core.windows.net/results/prompt-memory-entries/images/photo.png"
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value=f"{blob_url}?sv=2024&sig=secret",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == blob_url
        assert request.pieces[0].converted_value == request.pieces[0].original_value

    async def test_media_reference_is_resolved_without_persistence(
        self, message_send_service, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        """Local media URLs are converted back to their canonical file paths."""
        mock_memory.results_path = str(tmp_path)
        stored_path = (tmp_path / "prompt-memory-entries" / "image.png").resolve()
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value=f"/api/media?path={stored_path}",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == str(stored_path)
        assert request.pieces[0].converted_value == str(stored_path)
        factory.assert_not_called()

    async def test_existing_file_is_kept_without_persistence(
        self, message_send_service, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        """An existing stored file remains the canonical original and converted value."""
        mock_memory.results_path = str(tmp_path)
        media_path = tmp_path / "prompt-memory-entries" / "images" / "image.png"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"image")
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="image_path", original_value=str(media_path))],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == str(media_path.resolve())
        assert request.pieces[0].converted_value == str(media_path.resolve())
        factory.assert_not_called()

    async def test_existing_file_outside_results_is_rejected(
        self, message_send_service, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        """Files outside the results media folders cannot be used as message media."""
        mock_memory.results_path = str(tmp_path / "results")
        outside_path = tmp_path / "outside.png"
        outside_path.write_bytes(b"image")
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="image_path", original_value=str(outside_path))],
            send=False,
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="outside the allowed results directory"):
            await MessageSendService._persist_base64_pieces_async(request)

    async def test_non_path_data_types_are_skipped(self, message_send_service) -> None:
        """Non *_path types like reasoning, url, function_call should not be decoded."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(data_type="reasoning", original_value="thinking step"),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "thinking step"

    async def test_long_base64_audio_does_not_crash(self, message_send_service) -> None:
        """Base64 audio data longer than OS path limits should be saved, not crash with OSError."""
        # Simulate a base64-encoded WAV file (>4096 chars, exceeds Linux filename limit of 255)
        long_b64 = "UklGRiQ" + "A" * 5000  # fake WAV header + padding
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="audio_path",
                    original_value=long_b64,
                    mime_type="audio/wav",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as mock_factory:
            mock_serializer = AsyncMock()
            mock_serializer.value = "/tmp/saved_audio.wav"
            mock_factory.return_value = mock_serializer

            await MessageSendService._persist_base64_pieces_async(request)

            mock_factory.assert_called_once()
            mock_serializer.save_b64_image_async.assert_called_once_with(data=long_b64)
            assert request.pieces[0].original_value == "/tmp/saved_audio.wav"

    async def test_persistence_failure_does_not_partially_mutate_piece(self, message_send_service) -> None:
        """A failed save leaves both request values unchanged."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )
        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock(side_effect=OSError("save failed"))

        with (
            patch(
                "pyrit.backend.services.message_send_service.data_serializer_factory",
                return_value=mock_serializer,
            ),
            pytest.raises(OSError, match="save failed"),
        ):
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "aW1hZ2VkYXRh"
        assert request.pieces[0].converted_value is None


class TestAddMessageGuards:
    """Tests for target-mismatch and operator-mismatch guards in add_message_async."""

    async def test_rejects_mismatched_target(self, message_send_service, mock_memory) -> None:
        """Should raise ValueError when request target differs from attack target."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        # Create a mock target with a different class_name
        wrong_target = MagicMock()
        wrong_target.get_identifier.return_value = ComponentIdentifier(
            class_name="DifferentTarget",
            class_module="pyrit.prompt_target",
        )

        with patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc:
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = wrong_target
            mock_get_target_svc.return_value = mock_target_svc

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="wrong-target",
            )

            with pytest.raises(ValueError, match="Target mismatch"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)

    @pytest.mark.parametrize("has_target", [False, True])
    async def test_allows_matching_target(
        self, *, message_send_service: MessageSendService, mock_memory: MagicMock, has_target: bool
    ) -> None:
        """Should NOT raise when request target matches attack target."""
        ar = make_attack_result(conversation_id="test-id", has_target=has_target)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            await message_send_service.add_message_async(attack_result_id="test-id", request=request)
            mock_normalizer.send_prompt_async.assert_awaited_once()

    def test_allows_matching_round_robin_target(self, message_send_service) -> None:
        """Equivalent composite identifiers should pass target validation."""
        stored_target_id = _make_round_robin_identifier()
        request_target = MagicMock()
        request_target.get_identifier.return_value = _make_round_robin_identifier()
        attack_identifier = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.executor.attack",
            children={"objective_target": stored_target_id},
        )
        message_send_service._validate_target_match(attack_identifier=attack_identifier, target=request_target)

    @pytest.mark.parametrize(
        ("second_model_name", "weights"),
        [
            ("different-model", (1, 1)),
            ("e2e-dummy-model", (2, 1)),
        ],
        ids=["inner-target", "weights"],
    )
    def test_rejects_incompatible_round_robin_target(
        self,
        message_send_service,
        second_model_name: str,
        weights: tuple[int, int],
    ) -> None:
        """Composite differences should be rejected despite identical nullable root fields."""
        stored_target_id = _make_round_robin_identifier()
        request_target = MagicMock()
        request_target.get_identifier.return_value = _make_round_robin_identifier(
            second_model_name=second_model_name,
            weights=weights,
        )
        attack_identifier = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.executor.attack",
            children={"objective_target": stored_target_id},
        )
        with pytest.raises(ValueError, match="Target mismatch"):
            message_send_service._validate_target_match(attack_identifier=attack_identifier, target=request_target)


class TestResolveVideoRemixMetadata:
    "Tests for _resolve_video_remix_metadata_async."

    async def test_resolves_video_id_from_original_piece(self, message_send_service, mock_memory):
        """When a video_path piece has original_prompt_id, resolve video_id onto text piece."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"video_id": "vid-abc-123"}
        mock_memory.get_message_pieces_async.return_value = [original_piece]

        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix this video", data_type="text"),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        (await message_send_service._resolve_video_remix_metadata_async(request))

        assert request.pieces[0].prompt_metadata == {"video_id": "vid-abc-123"}
        assert request.pieces[1].prompt_metadata == {"video_id": "vid-abc-123"}

    async def test_no_op_without_video_pieces(self, message_send_service):
        """Should do nothing when there are no video_path pieces."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[MessagePieceRequest(original_value="just text", data_type="text")],
        )

        (await message_send_service._resolve_video_remix_metadata_async(request))

        assert request.pieces[0].prompt_metadata is None

    async def test_no_op_when_video_id_already_set(self, message_send_service, mock_memory):
        """Should not overwrite existing video_id on text piece."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(
                    original_value="remix",
                    data_type="text",
                    prompt_metadata={"video_id": "existing-id"},
                ),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        (await message_send_service._resolve_video_remix_metadata_async(request))

        assert request.pieces[0].prompt_metadata == {"video_id": "existing-id"}
        mock_memory.get_message_pieces_async.assert_not_called()

    async def test_no_op_without_original_prompt_id(self, message_send_service, mock_memory):
        """Should not crash when video_path piece has no original_prompt_id."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix", data_type="text"),
                MessagePieceRequest(original_value="/path/to/video.mp4", data_type="video_path"),
            ],
        )

        (await message_send_service._resolve_video_remix_metadata_async(request))

        assert request.pieces[0].prompt_metadata is None
        mock_memory.get_message_pieces_async.assert_not_called()

    async def test_no_op_when_original_piece_has_no_video_id(self, message_send_service, mock_memory):
        """Should not set metadata when original piece has no video_id."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"other_key": "value"}
        mock_memory.get_message_pieces_async.return_value = [original_piece]

        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix", data_type="text"),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        (await message_send_service._resolve_video_remix_metadata_async(request))

        assert request.pieces[0].prompt_metadata is None


@pytest.mark.usefixtures("patch_central_database")
class TestConverterMetadata:
    """Converter metadata merge contracts moved with their owner."""

    async def test_add_message_merges_converter_identifiers_without_duplicates(self, message_send_service, mock_memory):
        """Should merge new converter identifiers with existing attack identifiers by hash."""
        existing_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        duplicate_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        # Rebuild the atomic_attack_identifier to include an existing converter child
        technique = ar.get_attack_strategy_identifier()
        ar.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={
                    "objective_target": technique.get_child("objective_target") if technique else None,
                    "request_converters": [existing_converter],
                },
            ),
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1", "c-2"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            message_send_service=message_send_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[duplicate_converter, new_converter],
        )
        # Converters are now stored inside atomic_attack_identifier -> attack_technique -> attack
        atomic_id = update_fields["atomic_attack_identifier"]
        attack_id = atomic_id["children"]["attack_technique"]["children"]["attack"]
        persisted_identifiers = attack_id["children"]["request_converters"]
        persisted_classes = [identifier["class_name"] for identifier in persisted_identifiers]

        assert persisted_classes.count("ExistingConverter") == 1
        assert persisted_classes.count("NewConverter") == 1
        # The removed attack_identifier column should not be written.
        assert "attack_identifier" not in update_fields

    async def test_converter_merge_with_flat_atomic_identifier(self, message_send_service, mock_memory):
        """Should merge converters via fallback path when atomic_attack_identifier has no attack_technique child."""
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        # Build a flat atomic identifier (no attack_technique nesting — legacy shape)
        attack_id = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.backend",
            children={
                "objective_target": ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target"),
            },
        )
        ar = make_attack_result(conversation_id="flat-1")
        ar.atomic_attack_identifier = ComponentIdentifier(
            class_name="AtomicAttack",
            class_module="pyrit.scenario.core.atomic_attack",
            children={"attack": attack_id},
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="flat-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            message_send_service=message_send_service,
            mock_memory=mock_memory,
            attack_result_id="flat-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[new_converter],
        )
        assert "atomic_attack_identifier" in update_fields
        assert "attack_identifier" not in update_fields
        # Flat fallback: converter should be under atomic -> attack -> children
        atomic_id = update_fields["atomic_attack_identifier"]
        attack_child = atomic_id["children"]["attack"]
        persisted_converters = attack_child["children"]["request_converters"]
        assert len(persisted_converters) == 1
        assert persisted_converters[0]["class_name"] == "NewConverter"

    async def test_converter_merge_all_duplicates_does_not_rewrite_identifier(self, message_send_service, mock_memory):
        """When every new converter is already present, the identifier is left untouched."""
        existing_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        duplicate_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        technique = ar.get_attack_strategy_identifier()
        ar.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={
                    "objective_target": technique.get_child("objective_target") if technique else None,
                    "request_converters": [existing_converter],
                },
            ),
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            message_send_service=message_send_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[duplicate_converter],
        )
        assert "atomic_attack_identifier" not in update_fields

    async def test_converter_merge_preserves_sibling_children_hash(self, message_send_service, mock_memory):
        """Merging a converter must not disturb sibling children (objective_target keeps its hash)."""
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        technique = ar.get_attack_strategy_identifier()
        objective_target = technique.get_child("objective_target") if technique else None
        assert objective_target is not None
        original_target_hash = objective_target.hash

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            message_send_service=message_send_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[new_converter],
        )
        rebuilt = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
        rebuilt_attack = rebuilt.get_child("attack_technique").get_child("attack")
        assert rebuilt_attack.get_child("objective_target").hash == original_target_hash
        merged_converter_classes = [c.class_name for c in rebuilt_attack.get_child_list("request_converters")]
        assert merged_converter_classes == ["NewConverter"]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.timeout(10)
class TestConcurrentMessages:
    def test_default_service_instances_share_scheduler(self, mock_memory: MagicMock) -> None:
        first = MessageSendService()
        second = MessageSendService()
        assert first._scheduler is second._scheduler is get_manual_send_scheduler()
        assert first._memory is second._memory is PromptNormalizer()._memory is mock_memory

    async def test_common_execution_and_admission_limits_async(
        self, *, mock_memory: MagicMock, send_dependencies: tuple[MagicMock, AsyncMock]
    ) -> None:
        _, send = send_dependencies
        scheduler = ManualSendScheduler(max_concurrency=2, max_operations=3)
        services = [MessageSendService(scheduler=scheduler) for _ in range(2)]
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_async(**_: Any) -> None:
            if send.await_count == 2:
                started.set()
            await release.wait()

        send.side_effect = hold_async
        tasks = [
            asyncio.create_task(
                service.add_message_async(attack_result_id="attack", request=_request(conversation_id=cid))
            )
            for service, cid in zip(services, ["main", "second"], strict=True)
        ]
        try:
            await started.wait()
            with _observe_operation(scheduler) as waiting:
                tasks.append(
                    asyncio.create_task(
                        services[0].add_message_async(
                            attack_result_id="attack", request=_request(conversation_id="third")
                        )
                    )
                )
                await waiting.wait()
            with pytest.raises(ManualSendQueueFullError):
                await services[1].add_message_async(
                    attack_result_id="attack", request=_request(conversation_id="overflow")
                )
            assert send.await_count == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert send.await_count == 3
        assert not scheduler._conversations

    @pytest.mark.parametrize("active_send", [False, True])
    @pytest.mark.parametrize("incoming_send", [False, True])
    async def test_store_only_and_send_share_conversation_ownership_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        active_send: bool,
        incoming_send: bool,
    ) -> None:
        started = asyncio.Event()

        async def prepare_async(_: AddMessageRequest) -> None:
            started.set()
            await asyncio.Event().wait()

        peer = MessageSendService(scheduler=message_send_service._scheduler)
        with patch.object(message_send_service, "_persist_base64_pieces_async", side_effect=prepare_async):
            active = asyncio.create_task(
                message_send_service.add_message_async(attack_result_id="attack", request=_request(send=active_send))
            )
            try:
                await started.wait()
                with pytest.raises(ManualSendConflictError):
                    await peer.add_message_async(attack_result_id="attack", request=_request(send=incoming_send))
                mock_memory.add_message_pieces_to_memory_async.assert_not_called()
            finally:
                active.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await active
        await peer.add_message_async(attack_result_id="attack", request=_request(send=False))
        assert not peer._scheduler._conversations

    async def test_queued_cancellation_releases_conversation_before_dispatch_async(
        self, *, mock_memory: MagicMock, send_dependencies: tuple[MagicMock, AsyncMock]
    ) -> None:
        _, send = send_dependencies
        scheduler = ManualSendScheduler(max_concurrency=1, max_operations=2)
        service = MessageSendService(scheduler=scheduler)
        async with scheduler.operation_async():
            with _observe_operation(scheduler) as waiting:
                queued = asyncio.create_task(service.add_message_async(attack_result_id="attack", request=_request()))
                await waiting.wait()
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            send.assert_not_awaited()
            assert not scheduler._conversations
        await service.add_message_async(attack_result_id="attack", request=_request())
        send.assert_awaited_once()

    async def test_queued_send_uses_the_validated_target_async(
        self, *, mock_memory: MagicMock, send_dependencies: tuple[MagicMock, AsyncMock]
    ) -> None:
        target_service, send = send_dependencies
        target = target_service.get_target_object.return_value
        scheduler = ManualSendScheduler(max_concurrency=1)
        service = MessageSendService(scheduler=scheduler)
        async with scheduler.operation_async():
            with _observe_operation(scheduler) as waiting:
                queued = asyncio.create_task(service.add_message_async(attack_result_id="attack", request=_request()))
                await waiting.wait()
            target_service.get_target_object.return_value = None
        await queued
        assert send.call_args.kwargs["target"] is target
        target_service.get_target_object.assert_called_once()

    @pytest.mark.parametrize("kind", ["rpm", "request", "response"])
    async def test_rate_limited_and_converter_sends_allow_unrelated_sends_async(
        self, *, mock_memory: MagicMock, send_dependencies: tuple[MagicMock, AsyncMock], kind: str
    ) -> None:
        target_service, send = send_dependencies
        scheduler = ManualSendScheduler(max_concurrency=2, max_operations=3)
        service = MessageSendService(scheduler=scheduler)
        first_started, release_first = asyncio.Event(), asyncio.Event()
        order: list[str] = []
        first = _request()
        if kind == "rpm":
            limited = _make_matching_target_mock()
            limited._max_requests_per_minute = 30
            ordinary = target_service.get_target_object.return_value
            target_service.get_target_object.side_effect = lambda *, target_registry_name: (
                limited if target_registry_name == "limited" else ordinary
            )
            first.target_registry_name = "limited"
        else:
            setattr(first, f"{kind}_converter_configurations", [ConverterConfigurationRequest(converter_ids=["c"])])

        async def hold_async(*, conversation_id: str, **_: Any) -> None:
            order.append(conversation_id)
            if conversation_id == "main":
                first_started.set()
                await release_first.wait()

        send.side_effect = hold_async
        active = asyncio.create_task(service.add_message_async(attack_result_id="attack", request=first))
        try:
            await first_started.wait()
            await asyncio.wait_for(
                service.add_message_async(attack_result_id="attack", request=_request(conversation_id="second")),
                timeout=3,
            )
            assert order == ["main", "second"]
            assert not active.done()
        finally:
            release_first.set()
            await active

    @pytest.mark.timeout(90)
    async def test_target_pacing_does_not_block_another_target_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        service, ar, target, _ = real_send_context
        target._max_requests_per_minute = 30
        other_target = MockPromptTarget(rpm=60)
        other_attack = make_attack_result(
            conversation_id=str(uuid.uuid4()), attack_result_id=str(uuid.uuid4()), has_target=False
        )
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[other_attack])
        waiting, release = asyncio.Event(), asyncio.Event()
        metadata_started, release_metadata = asyncio.Event(), asyncio.Event()
        delays: list[float] = []
        update_attack_result = sqlite_instance.update_attack_result_by_id_async

        async def pace_async(delay: float) -> None:
            delays.append(delay)
            if delay == 2:
                waiting.set()
                await release.wait()
            else:
                assert delay == 1

        async def update_attack_result_async(*, attack_result_id: str, update_fields: dict[str, Any]) -> bool:
            if attack_result_id == other_attack.attack_result_id:
                metadata_started.set()
                await release_metadata.wait()
            return await update_attack_result(attack_result_id=attack_result_id, update_fields=update_fields)

        other_request = _request(conversation_id=other_attack.conversation_id)
        other_request.target_registry_name = "other"
        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as registry,
            patch("pyrit.prompt_target.common.utils.asyncio.sleep", side_effect=pace_async),
            patch.object(sqlite_instance, "update_attack_result_by_id_async", side_effect=update_attack_result_async),
        ):
            registry.return_value.get_target_object.side_effect = lambda *, target_registry_name: (
                other_target if target_registry_name == "other" else target
            )
            active = asyncio.create_task(
                service.add_message_async(
                    attack_result_id=ar.attack_result_id, request=_request(conversation_id=ar.conversation_id)
                )
            )
            tasks = [active]
            try:
                await asyncio.wait_for(waiting.wait(), timeout=30)
                independent = asyncio.create_task(
                    service.add_message_async(attack_result_id=other_attack.attack_result_id, request=other_request)
                )
                tasks.append(independent)
                await asyncio.wait_for(metadata_started.wait(), timeout=30)
                assert delays == [2.0, 1.0]
                assert target.prompt_sent == []
                assert other_target.prompt_sent == ["Hello"]
                assert not active.done()
                assert not independent.done()
                release_metadata.set()
                await wait_for_completion_async(future=independent)
                assert not active.done()
                release.set()
                await wait_for_completion_async(future=active)
            finally:
                release.set()
                release_metadata.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        assert target.prompt_sent == ["Hello"]

    @pytest.mark.parametrize("stage", ["request", "response"])
    async def test_converter_protection_is_per_instance_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        stage: str,
    ) -> None:
        service, ar, target, shared = real_send_context
        scheduler = service._scheduler
        peer = MessageSendService(scheduler=scheduler)
        branch_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=ar.attack_result_id,
            conversations=[
                Conversation(conversation_id=cid, target_identifier=target.get_identifier()) for cid in branch_ids
            ],
            message_pieces=[],
        )
        independent = Base64Converter()
        assert shared.get_identifier().hash == independent.get_identifier().hash
        converters = {"shared": shared, "independent": independent}
        requests = [_request(conversation_id=cid) for cid in [ar.conversation_id, *branch_ids]]
        for request, name in zip(requests, ["shared", "shared", "independent"], strict=True):
            setattr(request, f"{stage}_converter_configurations", [ConverterConfigurationRequest(converter_ids=[name])])

        started, shared_waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        convert = shared.convert_async
        guard = scheduler.conversion_async
        calls = 0
        attempts = 0

        async def convert_async(*, prompt: str, input_type: PromptDataType) -> ConverterResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return await convert(prompt=prompt, input_type=input_type)

        @asynccontextmanager
        async def observe_guard_async(converter: Converter) -> AsyncGenerator[None, None]:
            nonlocal attempts
            if converter is shared:
                attempts += 1
                if attempts == 2:
                    shared_waiting.set()
            async with guard(converter):
                yield

        with (
            patch("pyrit.backend.services.message_send_service.get_converter_service") as registry,
            patch.object(shared, "convert_async", side_effect=convert_async),
            patch.object(scheduler, "conversion_async", side_effect=observe_guard_async),
        ):
            registry.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                converters[name] for name in converter_ids
            ]
            tasks = [
                asyncio.create_task(
                    service.add_message_async(attack_result_id=ar.attack_result_id, request=requests[0])
                )
            ]
            try:
                await started.wait()
                tasks.append(
                    asyncio.create_task(
                        peer.add_message_async(attack_result_id=ar.attack_result_id, request=requests[1])
                    )
                )
                await shared_waiting.wait()
                await asyncio.wait_for(
                    peer.add_message_async(attack_result_id=ar.attack_result_id, request=requests[2]), timeout=3
                )
                assert calls == 1
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                await asyncio.gather(*tasks)
        assert calls == 2
        assert not scheduler._converters
        assert not scheduler._conversations

    @pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
    async def test_dispatch_failure_releases_ownership_without_reusing_old_errors_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        error: BaseException,
    ) -> None:
        _, send = send_dependencies
        mock_memory.get_message_pieces_async.return_value = [
            MessagePiece(
                role="assistant", original_value="old error", response_error="processing", conversation_id="main"
            )
        ]
        send.side_effect = error
        with pytest.raises(type(error)):
            await message_send_service.add_message_async(attack_result_id="attack", request=_request())
        send.assert_awaited_once()
        mock_memory.update_attack_result_by_id_async.assert_not_called()
        assert not message_send_service._scheduler._conversations
        send.side_effect = None
        await message_send_service.add_message_async(attack_result_id="attack", request=_request())

    @pytest.mark.parametrize("write_method", ["add_message_pieces_to_memory_async", "update_attack_result_by_id_async"])
    async def test_cancellation_joins_memory_write_before_releasing_ownership_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        write_method: str,
    ) -> None:
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def hold_write_async(**_: Any) -> None:
            started.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            finished.set()

        with patch.object(mock_memory, write_method, side_effect=hold_write_async):
            active = asyncio.create_task(
                message_send_service.add_message_async(attack_result_id="attack", request=_request(send=False))
            )
            try:
                await started.wait()
                for _ in range(2):
                    active.cancel()
                    await asyncio.sleep(0)
                    assert not active.done()
                    if write_method == "update_attack_result_by_id_async":
                        assert message_send_service._scheduler._metadata_updates == {"attack"}
                with pytest.raises(ManualSendConflictError):
                    await message_send_service.add_message_async(
                        attack_result_id="attack", request=_request(send=False)
                    )
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await active
        assert finished.is_set()
        assert not message_send_service._scheduler._conversations
        assert not message_send_service._scheduler._metadata_updates
        await message_send_service.add_message_async(attack_result_id="attack", request=_request(send=False))

    @pytest.mark.parametrize("write_method", ["add_message_pieces_to_memory_async", "update_attack_result_by_id_async"])
    async def test_memory_failure_releases_ownership_and_capacity_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        write_method: str,
    ) -> None:
        with (
            patch.object(mock_memory, write_method, side_effect=RuntimeError("write failed")),
            pytest.raises(RuntimeError, match="write failed"),
        ):
            await message_send_service.add_message_async(attack_result_id="attack", request=_request(send=False))
        assert not message_send_service._scheduler._conversations
        assert not message_send_service._scheduler._metadata_updates
        await message_send_service.add_message_async(attack_result_id="attack", request=_request(send=False))

    async def test_disappearing_attack_reports_finalization_error_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
    ) -> None:
        _, send = send_dependencies
        mock_memory.get_attack_results_async.side_effect = [mock_memory.get_attack_results_async.return_value, []]
        with pytest.raises(ValueError, match="not found after message send"):
            await message_send_service.add_message_async(attack_result_id="attack", request=_request())
        send.assert_awaited_once()
        mock_memory.update_attack_result_by_id_async.assert_not_called()
        assert not message_send_service._scheduler._conversations

    async def test_queued_converter_updates_merge_current_metadata_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        _, ar, target, first_converter = real_send_context
        conversation_id = str(uuid.uuid4())
        await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=ar.attack_result_id,
            conversations=[Conversation(conversation_id=conversation_id, target_identifier=target.get_identifier())],
            message_pieces=[],
        )
        scheduler = ManualSendScheduler(max_concurrency=1)
        services = [MessageSendService(scheduler=scheduler) for _ in range(2)]
        first_started, release = asyncio.Event(), asyncio.Event()
        converters = {"first": first_converter, "second": Base64Converter(encoding_func="b32encode")}

        async def hold_first_async(*, conversation_id: str, **_: Any) -> None:
            if conversation_id == ar.conversation_id:
                first_started.set()
                await release.wait()

        requests = [_request(conversation_id=ar.conversation_id), _request(conversation_id=conversation_id)]
        for request, name in zip(requests, converters, strict=True):
            request.request_converter_configurations = [ConverterConfigurationRequest(converter_ids=[name])]
            request.response_converter_configurations = [ConverterConfigurationRequest(converter_ids=[name])]
        with (
            patch("pyrit.backend.services.message_send_service.get_converter_service") as registry,
            patch.object(PromptNormalizer, "send_prompt_async", side_effect=hold_first_async),
        ):
            registry.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                converters[name] for name in converter_ids
            ]
            tasks = [
                asyncio.create_task(
                    services[0].add_message_async(attack_result_id=ar.attack_result_id, request=requests[0])
                )
            ]
            try:
                await first_started.wait()
                with _observe_operation(scheduler) as waiting:
                    tasks.append(
                        asyncio.create_task(
                            services[1].add_message_async(attack_result_id=ar.attack_result_id, request=requests[1])
                        )
                    )
                    await waiting.wait()
            finally:
                release.set()
                await asyncio.gather(*tasks)
        stored = await sqlite_instance.get_attack_results_async(attack_result_ids=[ar.attack_result_id])
        strategy = stored[0].get_attack_strategy_identifier()
        assert strategy is not None
        for pipeline in ["request_converters", "response_converters"]:
            assert [item.params["encoding_func"] for item in strategy.get_child_list(pipeline)] == [
                "b64encode",
                "b32encode",
            ]
        assert stored[0].get_active_conversation_ids() == {ar.conversation_id, conversation_id}
        stored_target = strategy.get_child("objective_target")
        assert stored_target is not None
        assert stored_target.hash == target.get_identifier().hash

    @pytest.mark.parametrize("preconverted", [False, True])
    async def test_parallel_sends_serialize_complete_metadata_updates_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        preconverted: bool,
    ) -> None:
        first, ar, target, first_converter = real_send_context
        scheduler = first._scheduler
        second = MessageSendService(scheduler=scheduler)
        branch_id = str(uuid.uuid4())
        await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=ar.attack_result_id,
            conversations=[Conversation(conversation_id=branch_id, target_identifier=target.get_identifier())],
            message_pieces=[],
        )
        requests = [_request(conversation_id=ar.conversation_id), _request(conversation_id=branch_id)]
        converters = {"first": first_converter, "second": Base64Converter(encoding_func="b32encode")}
        if preconverted:
            for request, name in zip(requests, converters, strict=True):
                request.pieces[0].converted_value = f"{name} preview"
                request.pieces[0].applied_converter_ids = [name]

        second_target_started, first_write_started = asyncio.Event(), asyncio.Event()
        second_metadata_attempted, release_first_write = asyncio.Event(), asyncio.Event()
        writes: list[dict[str, Any]] = []
        attempts = 0
        send = target._send_prompt_to_target_async
        update = sqlite_instance.update_attack_result_by_id_async
        guard = scheduler.metadata_update_async

        async def overlap_targets_async(*, normalized_conversation: list[Message]) -> list[Message]:
            conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
            if conversation_id == ar.conversation_id:
                await second_target_started.wait()
            else:
                second_target_started.set()
                await first_write_started.wait()
            return await send(normalized_conversation=normalized_conversation)

        @asynccontextmanager
        async def observe_metadata_async(*, attack_result_id: str) -> AsyncGenerator[None, None]:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                second_metadata_attempted.set()
            async with guard(attack_result_id=attack_result_id):
                yield

        async def delay_first_write_async(*, attack_result_id: str, update_fields: dict[str, Any]) -> None:
            writes.append(update_fields)
            if len(writes) == 1:
                first_write_started.set()
                await asyncio.wait_for(release_first_write.wait(), timeout=5)
            await update(attack_result_id=attack_result_id, update_fields=update_fields)

        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=overlap_targets_async),
            patch.object(sqlite_instance, "update_attack_result_by_id_async", side_effect=delay_first_write_async),
            patch.object(scheduler, "metadata_update_async", side_effect=observe_metadata_async),
            patch("pyrit.backend.services.message_send_service.get_converter_service") as registry,
        ):
            registry.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                converters[name] for name in converter_ids
            ]
            tasks = [
                asyncio.create_task(service.add_message_async(attack_result_id=ar.attack_result_id, request=request))
                for service, request in zip([first, second], requests, strict=True)
            ]
            try:
                await second_metadata_attempted.wait()
                assert all(not task.done() for task in tasks)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.1)
                assert len(writes) == 1
                assert not tasks[1].done()
            finally:
                release_first_write.set()
                await asyncio.gather(*tasks)

        assert len(writes) == 2
        assert writes[0]["timestamp"] <= writes[1]["timestamp"]
        stored = await sqlite_instance.get_attack_results_async(attack_result_ids=[ar.attack_result_id])
        assert stored[0].timestamp == writes[1]["timestamp"]
        assert stored[0].last_response is not None
        assert stored[0].last_response.conversation_id == branch_id
        assert str(stored[0].last_response.id) == writes[1]["last_response_id"]
        if preconverted:
            strategy = stored[0].get_attack_strategy_identifier()
            assert strategy is not None
            assert [item.params["encoding_func"] for item in strategy.get_child_list("request_converters")] == [
                "b64encode",
                "b32encode",
            ]
        assert not scheduler._metadata_updates
        assert not scheduler._conversations


@pytest.mark.usefixtures("patch_central_database")
class TestNormalizerPersistence:
    async def test_obscure_markers_preserve_common_reply_syntax_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        service, attack, target, _ = real_send_context
        start_token, end_token = "<|pyrit_start_8f3a|>", "<|pyrit_end_8f3a|>"
        request_value = f"Decode: {start_token}{start_token}test{end_token}{end_token}"
        reply_value = f"echo x >> log\n>>> print('hello')\nSelected: {start_token}test{end_token}"
        request = AddMessageRequest(
            target_conversation_id=attack.conversation_id,
            target_registry_name="target",
            pieces=[MessagePieceRequest(original_value=request_value)],
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
            response_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
            start_token=start_token,
            end_token=end_token,
        )

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            return [
                MessagePiece(
                    role="assistant",
                    original_value=reply_value,
                    conversation_id=normalized_conversation[-1].get_piece().conversation_id,
                ).to_message()
            ]

        with patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async):
            await service.add_message_async(attack_result_id=attack.attack_result_id, request=request)

        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=attack.conversation_id)
        assert len(messages) == 2
        assert messages[0].get_piece().original_value == request_value
        assert messages[0].get_value() == f"Decode: {start_token}dGVzdA=={end_token}"
        assert messages[1].get_piece().original_value == reply_value
        assert messages[1].get_value() == "echo x >> log\n>>> print('hello')\nSelected: dGVzdA=="

    async def test_custom_markers_request_response_and_preconverted_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        service, attack, target, _ = real_send_context
        original = "keep ⟪literal⟫ <<<<test>>>> / <<<<test2>>>>"
        request = AddMessageRequest(
            target_conversation_id=attack.conversation_id,
            target_registry_name="target",
            pieces=[
                MessagePieceRequest(original_value="original", converted_value="literal <<already converted>>"),
                MessagePieceRequest(original_value=original),
            ],
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
            response_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
            start_token="<<",
            end_token=">>",
        )

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            return [
                MessagePiece(
                    role="assistant",
                    original_value="reply <<test>>",
                    conversation_id=normalized_conversation[-1].get_piece().conversation_id,
                ).to_message()
            ]

        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async),
            patch.object(target, "send_prompt_async", wraps=target.send_prompt_async) as send,
        ):
            await service.add_message_async(attack_result_id=attack.attack_result_id, request=request)
        sent = send.call_args.kwargs["message"]
        assert [piece.converted_value for piece in sent.message_pieces] == [
            "literal <<already converted>>",
            "keep ⟪literal⟫ <<dGVzdA==>> / <<dGVzdDI=>>",
        ]
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=attack.conversation_id)
        assert messages[0].message_pieces[1].original_value == original
        assert messages[1].get_value() == "reply dGVzdA=="

    @pytest.mark.parametrize("field", ["start_token", "end_token"])
    def test_add_message_rejects_empty_markers(self, *, field: str) -> None:
        with pytest.raises(ValidationError, match=field):
            AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="test")],
                target_conversation_id="main",
                **{field: ""},
            )

    async def test_multipart_preconverted_lineage_and_response_conversion_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        service, ar, target, _ = real_send_context
        source_id = str(uuid.uuid4())
        source = [
            MessagePiece(role="user", original_value=value, conversation_id=source_id) for value in ["Hello", "World"]
        ]
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=source)
        request = AddMessageRequest(
            target_conversation_id=ar.conversation_id,
            target_registry_name="target",
            pieces=[
                MessagePieceRequest(
                    original_value="Hello", converted_value="already converted", original_prompt_id=str(source[0].id)
                ),
                MessagePieceRequest(original_value="World", original_prompt_id=str(source[1].id)),
            ],
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
            response_converter_configurations=[ConverterConfigurationRequest(converter_ids=["base64"])],
        )
        with patch.object(target, "send_prompt_async", wraps=target.send_prompt_async) as send:
            await service.add_message_async(attack_result_id=ar.attack_result_id, request=request)
        send.assert_awaited_once()
        assert [piece.converted_value for piece in send.call_args.kwargs["message"].message_pieces] == [
            "already converted",
            "V29ybGQ=",
        ]
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=ar.conversation_id)
        assert len(messages) == 2
        assert [piece.original_value for piece in messages[0].message_pieces] == ["Hello", "World"]
        assert [piece.original_prompt_id for piece in messages[0].message_pieces] == [piece.id for piece in source]
        assert all(piece.id != original.id for piece, original in zip(messages[0].message_pieces, source, strict=True))
        assert messages[0].sequence == 0
        assert messages[1].sequence == 1
        assert messages[1].message_pieces[0].original_value == "default"
        assert messages[1].message_pieces[0].converted_value == "ZGVmYXVsdA=="
        stored = await sqlite_instance.get_attack_results_async(attack_result_ids=[ar.attack_result_id])
        assert service._memory is PromptNormalizer()._memory is sqlite_instance
        assert len(stored) == 1
        assert stored[0].last_response is not None
        assert stored[0].last_response.id == messages[1].message_pieces[0].id

    @pytest.mark.parametrize("failure", ["request-converter", "target", "response-converter", "write-only"])
    async def test_normalizer_owns_persistence_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        failure: str,
    ) -> None:
        service, ar, target, converter = real_send_context
        request = _request(conversation_id=ar.conversation_id)
        if failure == "request-converter":
            request.request_converter_configurations = [ConverterConfigurationRequest(converter_ids=["base64"])]
        elif failure == "response-converter":
            request.response_converter_configurations = [ConverterConfigurationRequest(converter_ids=["base64"])]
        component = target if failure in {"target", "write-only"} else converter
        method = "_send_prompt_to_target_async" if failure in {"target", "write-only"} else "convert_async"
        with patch.object(
            component, method, return_value=[], side_effect=None if failure == "write-only" else RuntimeError(failure)
        ):
            if failure in {"target", "write-only"}:
                await service.add_message_async(attack_result_id=ar.attack_result_id, request=request)
            else:
                with pytest.raises(RuntimeError, match=failure):
                    await service.add_message_async(attack_result_id=ar.attack_result_id, request=request)
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=ar.conversation_id)
        assert len(messages) == {"request-converter": 0, "target": 2, "response-converter": 1, "write-only": 1}[failure]
        if failure == "target":
            assert messages[-1].message_pieces[0].response_error == "processing"
            assert "RuntimeError: target" in messages[-1].message_pieces[0].converted_value
            stored = await sqlite_instance.get_attack_results_async(attack_result_ids=[ar.attack_result_id])
            assert stored[0].last_response is not None
            assert stored[0].last_response.id == messages[-1].message_pieces[0].id
        else:
            assert not any(piece.has_error() for message in messages for piece in message.message_pieces)
        assert not service._scheduler._conversations


@pytest.mark.usefixtures("patch_central_database")
class TestExactPreviewSend:
    """Exact applied values reach the target without rerunning preview converters."""

    @pytest.mark.parametrize("send", [True, False])
    async def test_type_changing_execution_provenance_is_preserved_async(
        self, *, message_send_service: MessageSendService, mock_memory: MagicMock, send: bool
    ) -> None:
        mock_memory.get_attack_results_async.return_value = [make_attack_result(conversation_id="test-id")]
        converters = {}
        for name, input_type, output_type, output in [
            ("ToImage", "text", "image_path", "preview.png"),
            ("ToText", "image_path", "text", "caption"),
        ]:
            converter = MagicMock(spec=Converter)
            converter.get_identifier.return_value = ComponentIdentifier(
                class_name=name,
                class_module="pyrit.converter",
                params={"supported_input_types": (input_type,), "supported_output_types": (output_type,)},
            )
            converter.convert_tokens_async = AsyncMock(
                return_value=ConverterResult(output_text=output, output_type=output_type)
            )
            converters[name] = converter
        ids = ["ToImage", "ToText", "ToImage", "ToText"]
        configurations = [
            ConverterConfiguration(
                converters=[converters[name]],
                prompt_data_types_to_apply=["text" if name == "ToImage" else "image_path"],
            )
            for name in ids
        ]
        preview = Message(message_pieces=[MessagePiece(role="user", original_value="source")])
        await PromptNormalizer().convert_values_async(converter_configurations=configurations, message=preview)
        expected = [identifier.class_name for identifier in preview.message_pieces[0].converter_identifiers]
        assert expected == ids
        for converter in converters.values():
            converter.convert_tokens_async.reset_mock()
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value="source",
                    converted_value="edited caption",
                    converted_value_data_type="text",
                    applied_converter_ids=ids,
                )
            ],
            send=send,
            target_conversation_id="test-id",
            target_registry_name="test-target",
            request_converter_configurations=[
                ConverterConfigurationRequest(
                    converter_ids=[name],
                    prompt_data_types_to_apply=["text" if name == "ToImage" else "image_path"],
                )
                for name in ids
            ]
            if send
            else None,
        )
        target = _make_matching_target_mock()
        target.send_prompt_async = AsyncMock(return_value=[])
        with (
            patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service,
            patch("pyrit.backend.services.message_send_service.get_target_service") as target_service,
            patch.object(PromptNormalizer, "_calc_hash_async", new_callable=AsyncMock),
        ):
            converter_service.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                converters[name] for name in converter_ids
            ]
            target_service.return_value.get_target_object.return_value = target
            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

        if send:
            piece = target.send_prompt_async.call_args.kwargs["message"].message_pieces[0]
        else:
            piece = mock_memory.add_message_pieces_to_memory_async.call_args.kwargs["message_pieces"][0]
            target.send_prompt_async.assert_not_awaited()
        assert piece.converted_value == "edited caption"
        assert [identifier.class_name for identifier in piece.converter_identifiers] == expected
        for converter in converters.values():
            converter.convert_tokens_async.assert_not_awaited()
        update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
        identifier = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
        assert [converter.class_name for converter in identifier.attack_technique.attack.request_converters] == [
            "ToImage",
            "ToText",
        ]

    async def test_unknown_applied_converter_rejected_before_sending_async(
        self, *, message_send_service: MessageSendService, mock_memory: MagicMock
    ) -> None:
        mock_memory.get_attack_results_async.return_value = [make_attack_result(conversation_id="test-id")]
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value="source", converted_value="preview", applied_converter_ids=["unknown"]
                )
            ],
            target_registry_name="test-target",
            target_conversation_id="test-id",
        )
        with (
            patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service,
            patch("pyrit.backend.services.message_send_service.get_target_service") as target_service,
            patch.object(message_send_service, "_send_and_store_message_async", new_callable=AsyncMock) as send,
        ):
            target_service.return_value.get_target_object.return_value = _make_matching_target_mock()
            converter_service.return_value.get_converter_objects_for_ids.side_effect = ValueError(
                "Converter instance 'unknown' not found"
            )
            with pytest.raises(ValueError, match="unknown"):
                await message_send_service.add_message_async(attack_result_id="test-id", request=request)
        send.assert_not_awaited()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    @pytest.mark.parametrize("original_value", ["Original source", ""])
    @pytest.mark.parametrize("has_converter_pipeline", [True, False])
    async def test_exact_preview_provenance_survives_memory_and_response_mapping_async(
        self,
        *,
        sqlite_instance: SQLiteMemory,
        original_value: str,
        has_converter_pipeline: bool,
    ) -> None:
        conversation_id = str(uuid.uuid4())
        original_id = str(uuid.uuid4())
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value=original_value,
                    converted_value="Exact edited preview",
                    converted_value_data_type="text",
                    applied_converter_ids=["preview", "preview"] if has_converter_pipeline else [],
                    original_prompt_id=original_id,
                    prompt_metadata={"preview": "applied"},
                )
            ],
            target_conversation_id=conversation_id,
            target_registry_name="test-target",
        )
        converter = MagicMock(spec=Converter)
        converter.get_identifier.return_value = ComponentIdentifier(
            class_name="RegisteredPreviewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        converter.convert_tokens_async = AsyncMock(side_effect=AssertionError("Preview must not rerun"))
        configurations = [ConverterConfiguration(converters=[converter, converter])] if has_converter_pipeline else []
        target = _make_matching_target_mock()
        target.send_prompt_async = AsyncMock(return_value=[])

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as target_service,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service,
        ):
            target_service.return_value.get_target_object.return_value = target
            converter_service.return_value.get_converter_objects_for_ids.return_value = [converter, converter]
            await MessageSendService()._send_and_store_message_async(
                conversation_id=conversation_id,
                target=target,
                request=request,
                sequence=0,
                request_converter_configurations=configurations,
                response_converter_configurations=[],
                preconverted_indexes={0},
                applied_converter_identifiers=resolve_applied_converter_identifiers(request.pieces),
            )

        converter.convert_tokens_async.assert_not_awaited()
        sent_piece = target.send_prompt_async.call_args.kwargs["message"].message_pieces[0]
        assert sent_piece.original_value == original_value
        assert sent_piece.converted_value == "Exact edited preview"
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
        assert len(pieces) == 1
        piece = pieces[0]
        assert piece.original_value == original_value
        assert piece.converted_value == "Exact edited preview"
        assert piece.original_prompt_id == uuid.UUID(original_id)
        assert piece.prompt_metadata == {"preview": "applied"}
        assert piece.original_value_sha256 == to_sha256(original_value)
        assert piece.converted_value_sha256 == to_sha256("Exact edited preview")
        views = await pyrit_messages_to_dto_async([Message(message_pieces=pieces)])
        result = views[0].message_pieces[0].model_dump(mode="json")
        assert result["original_value"] == original_value
        assert result["converted_value"] == "Exact edited preview"
        assert result["converted_value_data_type"] == "text"
        expected_converter_names = (
            ["RegisteredPreviewConverter", "RegisteredPreviewConverter"] if has_converter_pipeline else []
        )
        assert len(result["converter_identifiers"]) == len(expected_converter_names)
        assert [identifier.class_name for identifier in piece.converter_identifiers] == expected_converter_names

    @pytest.mark.parametrize(
        ("original_type", "original_value", "converted_type", "converted_value", "expected_original", "expected_final"),
        [
            ("text", "source", "text", "", "source", ""),
            ("text", "", "text", "Edited preview", "", "Edited preview"),
            ("text", "source", "image_path", "media:preview.png", "source", "stored:preview.png"),
            (
                "image_path",
                "media:source.png",
                "text",
                "Exact description",
                "stored:source.png",
                "Exact description",
            ),
            (
                "image_path",
                "media:source.png",
                "audio_path",
                "media:preview.wav",
                "stored:source.png",
                "stored:preview.wav",
            ),
        ],
    )
    async def test_send_preserves_exact_preview_and_converts_other_piece_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        tmp_path: Path,
        original_type: PromptDataType,
        original_value: str,
        converted_type: PromptDataType,
        converted_value: str,
        expected_original: str,
        expected_final: str,
    ) -> None:
        root = tmp_path.resolve()
        mock_memory.results_path = str(root)
        original_value, converted_value = (
            _stored_media(original_value, root=root),
            _stored_media(converted_value, root=root),
        )
        expected_original, expected_final = (
            _stored_media(expected_original, root=root),
            _stored_media(expected_final, root=root),
        )
        mock_memory.get_attack_results_async.return_value = [make_attack_result(conversation_id="test-id")]
        preview_converter = MagicMock(spec=Converter)
        preview_converter.get_identifier.return_value = ComponentIdentifier(
            class_name="PreviewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": (original_type,), "supported_output_types": (converted_type,)},
        )
        preview_converter.convert_tokens_async = AsyncMock(side_effect=AssertionError("Preview must not run again"))
        live_converter = MagicMock(spec=Converter)
        live_converter.get_identifier.return_value = ComponentIdentifier(
            class_name="LiveConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        live_converter.convert_tokens_async = AsyncMock(
            return_value=ConverterResult(output_text="Live conversion", output_type="text")
        )
        converters = {"preview": preview_converter, "live": live_converter}
        target = _make_matching_target_mock()
        target.send_prompt_async = AsyncMock(return_value=[])
        original_id = str(uuid.uuid4())
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type=original_type,
                    original_value=original_value,
                    converted_value=converted_value,
                    converted_value_data_type=converted_type,
                    applied_converter_ids=["preview", "preview"],
                    prompt_metadata={"source": "editing-pane"},
                    original_prompt_id=original_id,
                ),
                MessagePieceRequest(original_value="Unconverted"),
            ],
            target_registry_name="test-target",
            target_conversation_id="test-id",
            request_converter_configurations=[
                ConverterConfigurationRequest(
                    converter_ids=["preview", "preview"],
                    indexes_to_apply=[0],
                    prompt_data_types_to_apply=[original_type],
                ),
                ConverterConfigurationRequest(converter_ids=["live"], indexes_to_apply=[1]),
            ],
        )

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as target_service,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service,
            patch.object(PromptNormalizer, "_calc_hash_async", new_callable=AsyncMock),
        ):
            target_service.return_value.get_target_object.return_value = target
            converter_service.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                converters[converter_id] for converter_id in converter_ids
            ]

            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

        preview_converter.convert_tokens_async.assert_not_awaited()
        live_converter.convert_tokens_async.assert_awaited_once()
        target.send_prompt_async.assert_awaited_once()
        message = target.send_prompt_async.call_args.kwargs["message"]
        preview, live = message.message_pieces
        assert preview.original_value == expected_original
        assert preview.original_value_data_type == original_type
        assert preview.converted_value == expected_final
        assert preview.converted_value_data_type == converted_type
        assert preview.prompt_metadata == {"source": "editing-pane"}
        assert preview.original_prompt_id == uuid.UUID(original_id)
        assert [identifier.class_name for identifier in preview.converter_identifiers] == [
            "PreviewConverter",
            "PreviewConverter",
        ]
        assert live.original_value == "Unconverted"
        assert live.converted_value == "Live conversion"
        assert [identifier.class_name for identifier in live.converter_identifiers] == ["LiveConverter"]
        assert mock_memory.add_message_to_memory_async.call_args.kwargs["request"] is message
        update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
        identifier = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
        assert [converter.class_name for converter in identifier.attack_technique.attack.request_converters] == [
            "PreviewConverter",
            "LiveConverter",
        ]

    def test_preconverted_provenance_preserves_explicit_execution_order(self) -> None:
        first = MagicMock(spec=Converter)
        first.get_identifier.return_value = ComponentIdentifier(class_name="First", class_module="test")
        second = MagicMock(spec=Converter)
        second.get_identifier.return_value = ComponentIdentifier(class_name="Second", class_module="test")
        piece = MessagePieceRequest(
            original_value="source",
            data_type="text",
            converted_value="preview.png",
            converted_value_data_type="image_path",
            applied_converter_ids=["first", "second", "first"],
        )
        with patch("pyrit.backend.services.message_send_service.get_converter_service") as converter_service:
            converter_service.return_value.get_converter_objects_for_ids.return_value = [first, second, first]
            resolved = resolve_applied_converter_identifiers([piece, MessagePieceRequest(original_value="other")])

        assert [identifier.class_name for identifier in resolved[0]] == ["First", "Second", "First"]
        assert 1 not in resolved
        assert converter_service.return_value.get_converter_objects_for_ids.call_args.kwargs["converter_ids"] == [
            "first",
            "second",
            "first",
        ]


def _submission(
    *, conversation_id: str = "main", submission_id: str = "submission", count: int = 1
) -> MessageSendRequest:
    return MessageSendRequest(
        **_request(conversation_id=conversation_id).model_dump(), submission_id=submission_id, count=count
    )


@pytest.mark.timeout(20)
@pytest.mark.usefixtures("patch_central_database")
class TestAsyncMessageSend:
    @pytest.mark.parametrize(
        "terminal_state", [MessageSendState.COMPLETED, MessageSendState.FAILED, MessageSendState.INTERRUPTED]
    )
    async def test_settle_send_waits_past_finalizing_async(self, terminal_state: MessageSendState) -> None:
        service = MagicMock(spec=MessageSendService)
        accepted = MessageSendStatus(send_id="send", attack_result_id="attack", conversation_id="main")
        finalizing = accepted.model_copy(update={"state": MessageSendState.FINALIZING})
        terminal = accepted.model_copy(update={"state": terminal_state})
        service.get_status_async.side_effect = [finalizing, terminal]

        result = await _settle_send_async(service=service, status=accepted)

        assert result.state == terminal_state
        assert service.get_status_async.await_count == 2
        assert all(
            call.kwargs == {"attack_result_id": "attack", "send_id": "send", "wait_ms": 1000}
            for call in service.get_status_async.await_args_list
        )

    @pytest.mark.parametrize("failure", ["assertion", "timeout"])
    async def test_send_lifecycle_drains_pending_work_on_test_failure_async(
        self,
        *,
        message_send_service: MessageSendService,
        send_dependencies: tuple[MagicMock, AsyncMock],
        failure: str,
    ) -> None:
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def hold_send_async(**_: Any) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        send_dependencies[1].side_effect = hold_send_async
        error = AssertionError if failure == "assertion" else TimeoutError
        status: MessageSendStatus | None = None
        task: asyncio.Task[None] | None = None
        with pytest.raises(error):
            async with message_send_lifecycle_async(message_send_service):
                status = await message_send_service.submit_async(attack_result_id="attack", request=_submission())
                await started.wait()
                task = message_send_service._sends[status.send_id].task
                assert task is not None
                if failure == "assertion":
                    raise AssertionError("controlled test failure")
                try:
                    await _settle_send_async(service=message_send_service, status=status, timeout_seconds=0.01)
                except TimeoutError:
                    assert not task.done()
                    assert not cancelled.is_set()
                    raise

        assert task is not None
        assert status is not None
        assert task.done()
        assert cancelled.is_set()
        assert not message_send_service._scheduler._conversations
        assert (
            await message_send_service.get_status_async(attack_result_id="attack", send_id=status.send_id)
        ).state == MessageSendState.INTERRUPTED

    async def test_failure_is_not_terminal_until_evidence_reads_and_finalization_finish_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
    ) -> None:
        reading, release = asyncio.Event(), asyncio.Event()
        calls = 0
        stored_error = MessagePiece(role="assistant", original_value="failure", response_error="processing")

        async def read_pieces_async(**_: Any) -> list[MessagePiece]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return []
            if calls == 2:
                reading.set()
                await release.wait()
            return [stored_error]

        async def fail_async(**kwargs: Any) -> None:
            kwargs["send_context"].mark_target_invoked()
            raise RuntimeError("target failed")

        send_dependencies[1].side_effect = fail_async
        with patch.object(mock_memory, "get_message_pieces_async", side_effect=read_pieces_async):
            status = await message_send_service.submit_async(attack_result_id="attack", request=_submission())
            try:
                await reading.wait()
                during = await message_send_service.get_status_async(
                    attack_result_id="attack", send_id=status.send_id, wait_ms=1
                )
                assert during.state == MessageSendState.SENDING
                assert during.failure_stage == MessageSendFailureStage.SENDING
                assert message_send_service._scheduler._conversations == {"main"}
            finally:
                release.set()
                final = await _settle_send_async(service=message_send_service, status=status)
        assert final.state == MessageSendState.FAILED
        assert not message_send_service._scheduler._conversations

    async def test_shutdown_waiting_for_validation_survives_cancellation_without_dispatch_async(
        self,
        *,
        message_send_service: MessageSendService,
        send_dependencies: tuple[MagicMock, AsyncMock],
    ) -> None:
        validating, release = asyncio.Event(), asyncio.Event()
        validate = message_send_service._validate_message_async

        async def hold_validation_async(**kwargs: Any) -> Any:
            validated = await validate(**kwargs)
            validating.set()
            await release.wait()
            return validated

        with patch.object(message_send_service, "_validate_message_async", side_effect=hold_validation_async):
            submission = asyncio.create_task(
                message_send_service.submit_async(attack_result_id="attack", request=_submission())
            )
            await validating.wait()
            shutdown = asyncio.create_task(message_send_service.shutdown_async())
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            try:
                assert not shutdown.done()
            finally:
                release.set()
                await asyncio.gather(submission, shutdown, return_exceptions=True)
                await message_send_service.shutdown_async()
        with pytest.raises(ManualSendQueueFullError, match="shutting down"):
            submission.result()
        send_dependencies[1].assert_not_awaited()
        assert not message_send_service._scheduler._conversations

    @pytest.mark.parametrize("counts", [[1] * 64, [10] * 6 + [3, 1]])
    async def test_default_budget_admits_64_conversations_with_only_four_dispatching_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        counts: list[int],
    ) -> None:
        ar = mock_memory.get_attack_results_async.return_value[0]
        ar.related_conversations = {
            ConversationReference(conversation_id=f"conversation-{index}", conversation_type=ConversationType.PRUNED)
            for index in range(64)
        }

        started = asyncio.Event()

        async def hold_async(**_: Any) -> None:
            if send_dependencies[1].await_count == 4:
                started.set()
            await asyncio.Event().wait()

        send_dependencies[1].side_effect = hold_async
        try:
            for index, count in enumerate(counts):
                await message_send_service.submit_async(
                    attack_result_id="attack",
                    request=_submission(conversation_id=f"conversation-{index}", submission_id=str(index), count=count),
                )
            assert len(message_send_service._scheduler._conversations) == 64
            await started.wait()
            assert send_dependencies[1].await_count == 4
            with pytest.raises(ManualSendQueueFullError):
                await message_send_service.submit_async(attack_result_id="attack", request=_submission())
        finally:
            await message_send_service.shutdown_async()
        assert not message_send_service._scheduler._conversations

    @pytest.mark.parametrize("count", [1, 3])
    async def test_cancelled_admission_releases_ownership_without_creating_a_handle_async(
        self, *, message_send_service: MessageSendService, send_dependencies: tuple[MagicMock, AsyncMock], count: int
    ) -> None:
        validating = asyncio.Event()

        async def hold_validation_async(**_: Any) -> None:
            validating.set()
            await asyncio.Event().wait()

        with patch.object(message_send_service, "_validate_message_async", side_effect=hold_validation_async):
            submission = asyncio.create_task(
                message_send_service.submit_async(attack_result_id="attack", request=_submission(count=count))
            )
            await validating.wait()
            submission.cancel()
            with pytest.raises(asyncio.CancelledError):
                await submission
        assert not message_send_service._scheduler._conversations
        assert not message_send_service._sends
        assert not message_send_service._submissions
        send_dependencies[1].assert_not_awaited()

    @pytest.mark.parametrize("count", [1, 3])
    async def test_accepts_before_target_finishes_and_status_cancellation_does_not_cancel_send_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        count: int,
    ) -> None:
        service, ar, target, _ = real_send_context
        started, release = asyncio.Event(), asyncio.Event()
        send = target._send_prompt_to_target_async

        async def hold_async(*, normalized_conversation: list[Message]) -> list[Message]:
            started.set()
            await release.wait()
            return await send(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=hold_async) as provider:
            status = await service.submit_async(
                attack_result_id=ar.attack_result_id,
                request=_submission(conversation_id=ar.conversation_id, count=count),
            )
            assert status.state == MessageSendState.QUEUED
            await started.wait()
            read = asyncio.create_task(
                service.get_status_async(attack_result_id=ar.attack_result_id, send_id=status.send_id, wait_ms=1000)
            )
            await asyncio.sleep(0)
            read.cancel()
            with pytest.raises(asyncio.CancelledError):
                await read
            current = await service.get_status_async(
                attack_result_id=ar.attack_result_id, send_id=status.send_id, wait_ms=1
            )
            assert current.state == MessageSendState.SENDING
            assert ar.conversation_id in service._scheduler._conversations
            assert len(service._scheduler._conversations) == count
            release.set()
            current = await _settle_send_async(service=service, status=status)
            assert current.state == MessageSendState.COMPLETED
            assert current.request_turn_number == 0
            assert provider.await_count == count
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=ar.conversation_id)
        assert [piece.role for piece in pieces] == ["user", "assistant"]
        assert not service._scheduler._conversations
        next_send = await service.submit_async(
            attack_result_id=ar.attack_result_id,
            request=_submission(conversation_id=ar.conversation_id, submission_id="next"),
        )
        next_send = await _settle_send_async(service=service, status=next_send)
        assert next_send.request_turn_number == 2
        current.state = MessageSendState.FAILED
        assert (
            await service.get_status_async(attack_result_id=ar.attack_result_id, send_id=status.send_id)
        ).state == MessageSendState.COMPLETED

    @pytest.mark.parametrize("invalid", ["attack", "conversation", "target", "converter"])
    async def test_validation_does_not_accept_or_write_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        invalid: str,
    ) -> None:
        target_service, send = send_dependencies
        request = _submission()
        if invalid == "attack":
            mock_memory.get_attack_results_async.return_value = []
        elif invalid == "conversation":
            request.target_conversation_id = "foreign"
        elif invalid == "target":
            target_service.get_target_object.return_value = None
        else:
            request.pieces[0].applied_converter_ids = ["missing"]
        with patch(
            "pyrit.backend.services.message_send_service.resolve_applied_converter_identifiers",
            side_effect=ValueError("Converter not found") if invalid == "converter" else None,
            return_value={},
        ):
            with pytest.raises(ValueError):
                await message_send_service.submit_async(attack_result_id="attack", request=request)
        assert not message_send_service._sends
        assert not message_send_service._submissions
        assert not message_send_service._scheduler._conversations
        send.assert_not_awaited()
        mock_memory.add_message_to_memory_async.assert_not_awaited()
        mock_memory.update_attack_result_by_id_async.assert_not_awaited()

    @pytest.mark.parametrize("count", [1, 3])
    @pytest.mark.parametrize(
        "ordered_field",
        ["pieces", "request", "response", "applied", "pipelines", "count", "mode", "start-token", "end-token"],
    )
    async def test_retained_submission_deduplication_preserves_order_async(
        self,
        *,
        message_send_service: MessageSendService,
        send_dependencies: tuple[MagicMock, AsyncMock],
        ordered_field: str,
        count: int,
    ) -> None:
        request = _submission(count=count)
        request.pieces.append(MessagePieceRequest(original_value="second"))
        request.request_converter_configurations = [
            ConverterConfigurationRequest(converter_ids=["first", "second"]),
            ConverterConfigurationRequest(converter_ids=["second"]),
        ]
        request.response_converter_configurations = [ConverterConfigurationRequest(converter_ids=["first", "second"])]
        request.pieces[0].applied_converter_ids = ["first", "second"]
        status = await message_send_service.submit_async(attack_result_id="attack", request=request)
        duplicate = await message_send_service.submit_async(attack_result_id="attack", request=request)
        assert duplicate.send_id == status.send_id
        await _settle_send_async(service=message_send_service, status=status)
        duplicate = await message_send_service.submit_async(attack_result_id="attack", request=request)
        assert duplicate.send_id == status.send_id
        changed = request.model_copy(deep=True)
        if ordered_field == "pieces":
            changed.pieces.reverse()
        elif ordered_field == "request":
            changed.request_converter_configurations[0].converter_ids.reverse()
        elif ordered_field == "response":
            changed.response_converter_configurations[0].converter_ids.reverse()
        elif ordered_field == "pipelines":
            changed.request_converter_configurations.reverse()
        elif ordered_field == "count":
            changed.count += 1
        elif ordered_field == "mode":
            changed.request_converter_mode = RequestConverterMode.PER_BRANCH
        elif ordered_field == "start-token":
            changed.start_token = "<<"
        elif ordered_field == "end-token":
            changed.end_token = ">>"
        else:
            changed.pieces[0].applied_converter_ids.reverse()
        with pytest.raises(ManualSendConflictError, match="different message request"):
            await message_send_service.submit_async(attack_result_id="attack", request=changed)
        assert send_dependencies[1].await_count == count

    async def test_admission_is_shared_with_synchronous_sends_and_context_async(
        self, *, message_send_service: MessageSendService, send_dependencies: tuple[MagicMock, AsyncMock]
    ) -> None:
        scheduler = ManualSendScheduler(max_concurrency=1, max_operations=2)
        message_send_service._scheduler = scheduler
        peer = MessageSendService(scheduler=scheduler)
        async with scheduler.operation_async():
            first = await message_send_service.submit_async(attack_result_id="attack", request=_submission())
            second = await message_send_service.submit_async(
                attack_result_id="attack", request=_submission(conversation_id="second", submission_id="second")
            )
            for send in [True, False]:
                with pytest.raises(ManualSendConflictError):
                    await peer.add_message_async(attack_result_id="attack", request=_request(send=send))
                with pytest.raises(ManualSendQueueFullError):
                    await peer.add_message_async(
                        attack_result_id="attack", request=_request(conversation_id="third", send=send)
                    )
            assert len(scheduler._conversations) == 2
        await _settle_send_async(service=message_send_service, status=first)
        await _settle_send_async(service=message_send_service, status=second)

    @pytest.mark.parametrize("stored_evidence", [False, True])
    async def test_preparation_failure_uses_dispatch_stage_not_saved_messages_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
        stored_evidence: bool,
    ) -> None:
        send_dependencies[1].side_effect = RuntimeError("preparation failed")
        mock_memory.get_message_pieces_async.side_effect = [
            [],
            [MessagePiece(role="assistant", original_value="evidence", response_error="processing")]
            if stored_evidence
            else [],
        ] + [[]]
        status = await message_send_service.submit_async(attack_result_id="attack", request=_submission())
        status = await _settle_send_async(service=message_send_service, status=status)
        assert status.state == MessageSendState.FAILED
        assert status.failure_stage == MessageSendFailureStage.PREPARATION
        assert status.error

    @pytest.mark.parametrize("count", [1, 3])
    @pytest.mark.parametrize("failure", ["conversion", "normalization", "validation", "target", "metadata"])
    async def test_real_sqlite_failure_stages_preserve_evidence_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        failure: str,
        count: int,
    ) -> None:
        service, ar, target, converter = real_send_context
        request = _submission(conversation_id=ar.conversation_id, count=count)
        request.request_converter_configurations = [ConverterConfigurationRequest(converter_ids=["base64"])]
        owner, method = {
            "conversion": (converter, "convert_async"),
            "normalization": (target, "_get_normalized_conversation_async"),
            "validation": (target, "_validate_request"),
            "target": (target, "_send_prompt_to_target_async"),
            "metadata": (sqlite_instance, "update_attack_result_by_id_async"),
        }[failure]
        with patch.object(owner, method, side_effect=RuntimeError("controlled failure")):
            status = await service.submit_async(attack_result_id=ar.attack_result_id, request=request)
            status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.FAILED
        assert (
            status.failure_stage
            == {
                "conversion": MessageSendFailureStage.PREPARATION,
                "normalization": MessageSendFailureStage.PREPARATION,
                "validation": MessageSendFailureStage.PREPARATION,
                "target": MessageSendFailureStage.SENDING,
                "metadata": MessageSendFailureStage.FINALIZATION,
            }[failure]
        )
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=ar.conversation_id)
        assert len(pieces) == (2 if failure in ("target", "metadata") else 0)
        if failure == "target":
            assert pieces[-1].response_error == "processing"
        assert not service._scheduler._conversations
        assert request.pieces[0].original_value == "Hello"

    @pytest.mark.parametrize("preflight_method", ["_get_normalized_conversation_async", "_validate_request"])
    async def test_preflight_retry_preserves_history_and_provider_boundary_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        preflight_method: str,
    ) -> None:
        service, attack, target, _ = real_send_context
        status = await service.submit_async(
            attack_result_id=attack.attack_result_id, request=_submission(conversation_id=attack.conversation_id)
        )
        assert (await _settle_send_async(service=service, status=status)).state == MessageSendState.COMPLETED
        original = await sqlite_instance.get_message_pieces_async(conversation_id=attack.conversation_id)

        with (
            patch.object(target, preflight_method, side_effect=RuntimeError("preflight failed")),
            patch.object(target, "_send_prompt_to_target_async") as provider,
        ):
            rejected = await service.submit_async(
                attack_result_id=attack.attack_result_id,
                request=_submission(conversation_id=attack.conversation_id, submission_id="rejected"),
            )
            rejected = await _settle_send_async(service=service, status=rejected)
            provider.assert_not_awaited()
        assert rejected.state == MessageSendState.FAILED
        assert rejected.failure_stage == MessageSendFailureStage.PREPARATION
        assert rejected.request_turn_number == 2
        unchanged = await sqlite_instance.get_message_pieces_async(conversation_id=attack.conversation_id)
        assert [piece.id for piece in unchanged] == [piece.id for piece in original]

        send = target._send_prompt_to_target_async
        histories: list[list[str]] = []
        provider_states: list[MessageSendState] = []

        async def observe_provider_async(*, normalized_conversation: list[Message]) -> list[Message]:
            histories.append([message.get_value() for message in normalized_conversation])
            current = await service.get_status_async(attack_result_id=attack.attack_result_id, send_id=retried.send_id)
            provider_states.append(current.state)
            return await send(normalized_conversation=normalized_conversation)

        request = _submission(conversation_id=attack.conversation_id, submission_id="retry")
        request.pieces[0].original_value = "retry"
        with patch.object(target, "_send_prompt_to_target_async", side_effect=observe_provider_async):
            retried = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
            retried = await _settle_send_async(service=service, status=retried)
        assert retried.state == MessageSendState.COMPLETED
        assert retried.request_turn_number == 2
        assert histories == [["Hello", "default", "retry"]]
        assert provider_states == [MessageSendState.SENDING]

        with patch.object(target, "_send_prompt_to_target_async", side_effect=RuntimeError("provider failed")):
            failed = await service.submit_async(
                attack_result_id=attack.attack_result_id,
                request=_submission(conversation_id=attack.conversation_id, submission_id="provider-failure"),
            )
            failed = await _settle_send_async(service=service, status=failed)
        assert failed.state == MessageSendState.FAILED
        assert failed.failure_stage == MessageSendFailureStage.SENDING
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=attack.conversation_id)
        assert len(pieces) == 6
        assert pieces[-1].response_error == "processing"
        assert not service._scheduler._conversations

    async def test_terminal_retention_is_bounded_and_expiring_without_replaying_async(
        self, *, message_send_service: MessageSendService, send_dependencies: tuple[MagicMock, AsyncMock]
    ) -> None:
        statuses = []
        with patch.object(message_send_service, "MAX_TERMINAL_SENDS", 2):
            for index in range(3):
                status = await message_send_service.submit_async(
                    attack_result_id="attack", request=_submission(submission_id=str(index))
                )
                statuses.append(await _settle_send_async(service=message_send_service, status=status))
            assert len(message_send_service._sends) == len(message_send_service._submissions) == 2
            with pytest.raises(MessageSendNotFoundError):
                await message_send_service.get_status_async(attack_result_id="attack", send_id=statuses[0].send_id)
            with pytest.raises(MessageSendNotFoundError):
                await message_send_service.get_status_async(attack_result_id="other", send_id=statuses[-1].send_id)
            with patch.object(message_send_service, "TERMINAL_TTL_SECONDS", 0):
                with pytest.raises(MessageSendNotFoundError, match="do not automatically resend"):
                    await message_send_service.get_status_async(attack_result_id="attack", send_id=statuses[-1].send_id)
        assert not message_send_service._sends
        assert not message_send_service._submissions
        assert send_dependencies[1].await_count == 3

    @pytest.mark.parametrize("count", [1, 3])
    @pytest.mark.parametrize("before_start", [True, False])
    async def test_shutdown_interrupts_accepted_work_and_stops_admission_async(
        self,
        *,
        message_send_service: MessageSendService,
        send_dependencies: tuple[MagicMock, AsyncMock],
        before_start: bool,
        count: int,
    ) -> None:
        started = asyncio.Event()

        async def hold_async(**kwargs: Any) -> None:
            kwargs["send_context"].mark_target_invoked()
            started.set()
            await asyncio.Event().wait()

        send_dependencies[1].side_effect = hold_async
        status = await message_send_service.submit_async(attack_result_id="attack", request=_submission(count=count))
        if not before_start:
            await started.wait()
        await message_send_service.shutdown_async()
        status = await message_send_service.get_status_async(attack_result_id="attack", send_id=status.send_id)
        assert status.state == MessageSendState.INTERRUPTED
        assert status.failure_stage == MessageSendFailureStage.INTERRUPTED
        assert not message_send_service._scheduler._conversations
        for operation in [
            message_send_service.submit_async(attack_result_id="attack", request=_submission()),
            message_send_service.add_message_async(attack_result_id="attack", request=_request(send=False)),
        ]:
            with pytest.raises(ManualSendQueueFullError, match="shutting down"):
                await operation

    async def test_repeated_shutdown_cancellation_joins_metadata_write_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
    ) -> None:
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def hold_write_async(**_: Any) -> None:
            started.set()
            await release.wait()
            finished.set()

        with patch.object(mock_memory, "update_attack_result_by_id_async", side_effect=hold_write_async):
            status = await message_send_service.submit_async(attack_result_id="attack", request=_submission())
            await started.wait()
            assert (
                await message_send_service.get_status_async(attack_result_id="attack", send_id=status.send_id)
            ).state == MessageSendState.FINALIZING
            shutdown = asyncio.create_task(message_send_service.shutdown_async())
            task = message_send_service._sends[status.send_id].task
            assert task is not None
            try:
                await asyncio.sleep(0)
                for _ in range(2):
                    task.cancel()
                    shutdown.cancel()
                    await asyncio.sleep(0)
                    assert not shutdown.done()
                    assert message_send_service._scheduler._conversations == {"main"}
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await shutdown
        assert finished.is_set()
        assert not message_send_service._scheduler._conversations
        assert not message_send_service._scheduler._metadata_updates
        assert (
            await message_send_service.get_status_async(attack_result_id="attack", send_id=status.send_id)
        ).state == MessageSendState.INTERRUPTED


@pytest.mark.timeout(20)
@pytest.mark.usefixtures("patch_central_database")
class TestRepeatedMessageSend:
    async def test_missing_validated_target_fails_before_branch_registration_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        service, attack, target, _ = real_send_context
        request = _submission(conversation_id=attack.conversation_id, count=3)
        validated = await service._validate_message_async(attack_result_id=attack.attack_result_id, request=request)
        validated.target = None
        with (
            patch.object(service, "_validate_message_async", new_callable=AsyncMock, return_value=validated),
            patch.object(service, "_prepare_copies", wraps=service._prepare_copies) as copies,
            patch.object(
                sqlite_instance, "add_conversation_branches_to_attack_async", new_callable=AsyncMock
            ) as register,
            patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as dispatch,
        ):
            status = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
            status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.FAILED
        assert status.failure_stage == MessageSendFailureStage.PREPARATION
        assert status.error == service.FAILURE_MESSAGES[MessageSendFailureStage.PREPARATION]
        assert status.conversations == []
        copies.assert_not_called()
        register.assert_not_awaited()
        dispatch.assert_not_awaited()
        assert await sqlite_instance.get_message_pieces_async() == []
        assert (
            await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id) == []
        )
        [updated] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
        assert updated.get_active_conversation_ids() == {attack.conversation_id}
        assert not service._scheduler._conversations
        [failure] = [
            record
            for record in caplog.records
            if record.name == "pyrit.backend.services.message_send_service" and record.exc_info
        ]
        assert failure.exc_info is not None
        assert isinstance(failure.exc_info[1], ValueError)
        assert str(failure.exc_info[1]) == "Target object for 'target' not found"

    @pytest.mark.parametrize("history_kind", ["empty", "system", "multipart"])
    async def test_snapshot_lineage_and_nested_selected_only_repetition_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        history_kind: str,
    ) -> None:
        service, attack, target, converter = real_send_context
        source_id = attack.conversation_id
        if history_kind != "empty":
            await target.set_system_prompt_async(system_prompt="System history", conversation_id=source_id)
        if history_kind == "multipart":
            await sqlite_instance.add_message_to_memory_async(
                request=Message(
                    message_pieces=[
                        MessagePiece(
                            role="user",
                            original_value=value,
                            converted_value=f"converted {value}",
                            conversation_id=source_id,
                            prompt_metadata={"metadata": value},
                            converter_identifiers=[converter.get_identifier()],
                        )
                        for value in ["first", "second"]
                    ]
                )
            )
            await sqlite_instance.add_message_to_memory_async(
                request=MessagePiece(
                    role="assistant", original_value="History response", conversation_id=source_id
                ).to_message()
            )
        history = await sqlite_instance.get_message_pieces_async(conversation_id=source_id)
        history_ids = {piece.id for piece in history}
        history_by_origin = {piece.original_prompt_id: piece for piece in history}
        observed: dict[str, list[Message]] = {}
        send = target._send_prompt_to_target_async

        async def observe_async(*, normalized_conversation: list[Message]) -> list[Message]:
            current_attack = (
                await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
            )[0]
            assert len(current_attack.get_active_conversation_ids()) == 5
            observed[normalized_conversation[-1].get_piece(0).conversation_id] = normalized_conversation
            return await send(normalized_conversation=normalized_conversation)

        request = _submission(conversation_id=source_id, count=5)
        request.pieces.append(MessagePieceRequest(original_value="next piece", prompt_metadata={"next": "metadata"}))
        with patch.object(target, "_send_prompt_to_target_async", side_effect=observe_async):
            status = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
            status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.COMPLETED
        ids = [progress.conversation_id for progress in status.conversations]
        assert len(ids) == len(set(ids)) == 5
        assert ids[0] == source_id
        assert set(observed) == set(ids)
        all_ids: set[uuid.UUID] = set()
        stored: dict[str, list[MessagePiece]] = {}
        for conversation_id in ids:
            pieces = list(await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id))
            stored[conversation_id] = pieces
            assert len(pieces) == len(history) + 3
            assert not all_ids.intersection(piece.id for piece in pieces)
            all_ids.update(piece.id for piece in pieces)
            assert [piece.original_value for piece in pieces[-3:-1]] == ["Hello", "next piece"]
            assert pieces[-2].prompt_metadata["next"] == "metadata"
            assert [piece.original_prompt_id for piece in pieces[-3:-1]] == [
                piece.original_prompt_id for piece in stored[source_id][-3:-1]
            ]
            for piece in pieces[: len(history)]:
                original = history_by_origin[piece.original_prompt_id]
                assert (piece.sequence, piece.original_value, piece.converted_value) == (
                    original.sequence,
                    original.original_value,
                    original.converted_value,
                )
                assert piece.prompt_metadata == original.prompt_metadata
                assert piece.converter_identifiers == original.converter_identifiers
                assert (piece.id in history_ids) == (conversation_id == source_id)
        owned = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id)
        assert {conversation.conversation_id for conversation in owned} == set(ids)
        assert all(conversation.target_identifier == target.get_identifier() for conversation in owned)

        nested = _submission(conversation_id=ids[1], submission_id="nested", count=3)
        nested.pieces[0].original_value = "Nested prompt"
        status = await service.submit_async(attack_result_id=attack.attack_result_id, request=nested)
        status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.COMPLETED
        [updated] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
        assert len(updated.get_active_conversation_ids()) == 7
        assert updated.conversation_id == source_id
        for progress in status.conversations:
            pieces = await sqlite_instance.get_message_pieces_async(conversation_id=progress.conversation_id)
            assert [piece.original_prompt_id for piece in pieces[:-2]] == [
                piece.original_prompt_id for piece in stored[ids[1]]
            ]
            assert pieces[-2].original_value == "Nested prompt"
        for conversation_id in [ids[0], *ids[2:]]:
            assert (
                await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
                == stored[conversation_id]
            )

    @pytest.mark.parametrize("custom_markers", [False, True])
    @pytest.mark.parametrize("mode", list(RequestConverterMode))
    async def test_request_conversion_scope_order_and_independent_response_conversion_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
        mode: RequestConverterMode,
        custom_markers: bool,
    ) -> None:
        service, attack, _, first = real_send_context
        second, response = Base64Converter(), Base64Converter()
        count = 0

        async def numbered_async(*, prompt: str, input_type: PromptDataType) -> ConverterResult:
            nonlocal count
            count += 1
            return ConverterResult(output_text=f"{prompt}-{count}", output_type=input_type)

        request = _submission(conversation_id=attack.conversation_id, count=3)
        request.request_converter_mode = mode
        prefix = "Keep: " if custom_markers else ""
        request_value = f"{prefix}<<<<Hello>>>>" if custom_markers else "Hello"
        request.pieces[0].original_value = request_value
        if custom_markers:
            request.start_token = "<<"
            request.end_token = ">>"
        request.pieces.append(
            MessagePieceRequest(
                original_value="original preview",
                converted_value="exact preview",
                applied_converter_ids=["preview", "preview"],
            )
        )
        configurations = [ConverterConfiguration(converters=[first, second], indexes_to_apply=[0, 1])]
        with (
            patch.object(service, "_resolve_request_converter_configs", return_value=configurations),
            patch.object(
                service, "_resolve_converter_configs", return_value=[ConverterConfiguration(converters=[response])]
            ),
            patch.object(first, "convert_async", side_effect=numbered_async) as convert_request,
            patch.object(second, "convert_async", wraps=second.convert_async) as encode,
            patch.object(response, "convert_async", wraps=response.convert_async) as convert_response,
            patch("pyrit.backend.services.message_send_service.get_converter_service") as registry,
        ):
            registry.return_value.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: [
                first for _ in converter_ids
            ]
            status = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
            status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.COMPLETED
        assert convert_request.await_count == encode.await_count == (1 if mode == RequestConverterMode.SHARED else 3)
        assert convert_response.await_count == 3
        converted = []
        for progress in status.conversations:
            pieces = await sqlite_instance.get_message_pieces_async(conversation_id=progress.conversation_id)
            converted.append(pieces[0].converted_value)
            assert pieces[0].original_value == request_value
            assert len(pieces[0].converter_identifiers) == 2
            assert (pieces[1].original_value, pieces[1].converted_value) == ("original preview", "exact preview")
            assert len(pieces[1].converter_identifiers) == 2
            assert (pieces[2].original_value, pieces[2].converted_value) == ("default", "ZGVmYXVsdA==")
        expected = (
            {"SGVsbG8tMQ=="}
            if mode == RequestConverterMode.SHARED
            else {
                "SGVsbG8tMQ==",
                "SGVsbG8tMg==",
                "SGVsbG8tMw==",
            }
        )
        assert set(converted) == {f"{prefix}{value}" for value in expected}
        assert request.pieces[0].converted_value is None

    async def test_insufficient_admission_releases_partial_claims_without_accepting_async(
        self,
        *,
        message_send_service: MessageSendService,
        mock_memory: MagicMock,
        send_dependencies: tuple[MagicMock, AsyncMock],
    ) -> None:
        scheduler = ManualSendScheduler(max_concurrency=1, max_operations=3)
        message_send_service._scheduler = scheduler
        with scheduler.reserve(conversation_id="occupied"):
            with pytest.raises(ManualSendQueueFullError):
                await message_send_service.submit_async(attack_result_id="attack", request=_submission(count=3))
            assert scheduler._conversations == {"occupied"}
            assert not message_send_service._sends
            mock_memory.get_attack_results_async.assert_not_awaited()
        status = await message_send_service.submit_async(attack_result_id="attack", request=_submission(count=3))
        assert (
            await _settle_send_async(service=message_send_service, status=status)
        ).state == MessageSendState.COMPLETED

    async def test_partial_conversion_failure_does_not_claim_nothing_was_dispatched_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
    ) -> None:
        service, attack, target, converter = real_send_context
        request = _submission(conversation_id=attack.conversation_id, count=3)
        request.request_converter_mode = RequestConverterMode.PER_BRANCH
        request.request_converter_configurations = [ConverterConfigurationRequest(converter_ids=["base64"])]
        with patch.object(
            converter,
            "convert_async",
            side_effect=[
                ConverterResult(output_text="first", output_type="text"),
                RuntimeError("One conversion failed"),
                ConverterResult(output_text="third", output_type="text"),
            ],
        ):
            status = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
            status = await _settle_send_async(service=service, status=status)
        assert status.state == MessageSendState.FAILED
        assert status.failure_stage == MessageSendFailureStage.SENDING
        assert [progress.failure_stage for progress in status.conversations].count(
            MessageSendFailureStage.PREPARATION
        ) == 1
        assert [progress.state for progress in status.conversations].count(MessageSendState.COMPLETED) == 2
        assert len(target.prompt_sent) == 2

    async def test_atomic_registration_rolls_back_copies_and_membership_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
    ) -> None:
        service, attack, target, _ = real_send_context
        await target.set_system_prompt_async(
            system_prompt="History to preserve", conversation_id=attack.conversation_id
        )
        history = await sqlite_instance.get_message_pieces_async(conversation_id=attack.conversation_id)
        insert = sqlite_instance._add_message_pieces_to_session

        def fail_after_insert(*, session: Session, message_pieces: Sequence[MessagePiece]) -> None:
            insert(session=session, message_pieces=message_pieces)
            raise RuntimeError("Atomic preparation failed")

        with patch.object(sqlite_instance, "_add_message_pieces_to_session", side_effect=fail_after_insert):
            status = await service.submit_async(
                attack_result_id=attack.attack_result_id,
                request=_submission(conversation_id=attack.conversation_id, count=3),
            )
            status = await _settle_send_async(service=service, status=status)
        assert status.failure_stage == MessageSendFailureStage.PREPARATION
        assert status.conversations == []
        assert target.prompt_sent == []
        assert await sqlite_instance.get_message_pieces_async() == history
        assert (
            await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id) == []
        )
        [updated] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
        assert updated.get_active_conversation_ids() == {attack.conversation_id}
        assert not service._scheduler._conversations

    async def test_finalization_owns_each_conversation_and_successful_siblings_can_continue_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
    ) -> None:
        service, attack, target, _ = real_send_context
        finalizing, release_metadata, release_siblings = asyncio.Event(), asyncio.Event(), asyncio.Event()
        continuing, release_continuation = asyncio.Event(), asyncio.Event()
        continued: MessageSendStatus | None = None
        send, finalize = target._send_prompt_to_target_async, service._update_attack_after_message_async
        failed_id: str | None = None

        async def provider_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal failed_id
            conversation_id = normalized_conversation[-1].get_piece().conversation_id
            if normalized_conversation[-1].get_value() == "Continue independently":
                continuing.set()
                await release_continuation.wait()
            if conversation_id != attack.conversation_id:
                failed_id = failed_id or conversation_id
                await release_siblings.wait()
                if conversation_id == failed_id:
                    raise RuntimeError("One sibling failed")
            return await send(normalized_conversation=normalized_conversation)

        async def finalize_async(**kwargs: Any) -> None:
            pieces = await sqlite_instance.get_message_pieces_async(prompt_ids=[kwargs["last_response_id"]])
            if pieces[0].conversation_id == attack.conversation_id:
                finalizing.set()
                await release_metadata.wait()
            await finalize(**kwargs)

        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=provider_async),
            patch.object(service, "_update_attack_after_message_async", side_effect=finalize_async),
        ):
            status = await service.submit_async(
                attack_result_id=attack.attack_result_id,
                request=_submission(conversation_id=attack.conversation_id, count=3),
            )
            try:
                await finalizing.wait()
                during = await service.get_status_async(
                    attack_result_id=attack.attack_result_id, send_id=status.send_id
                )
                assert during.conversations[0].state == MessageSendState.FINALIZING
                with pytest.raises(ManualSendConflictError):
                    await service.submit_async(
                        attack_result_id=attack.attack_result_id,
                        request=_submission(conversation_id=attack.conversation_id, submission_id="too-early"),
                    )
                release_metadata.set()
                async with asyncio.timeout(5):
                    while during.conversations[0].state != MessageSendState.COMPLETED:
                        during = await service.get_status_async(
                            attack_result_id=attack.attack_result_id, send_id=status.send_id, wait_ms=1
                        )
                assert during.state == MessageSendState.SENDING
                assert attack.conversation_id not in service._scheduler._conversations
                continuation = _submission(conversation_id=attack.conversation_id, submission_id="continue")
                continuation.pieces[0].original_value = "Continue independently"
                continued = await service.submit_async(
                    attack_result_id=attack.attack_result_id,
                    request=continuation,
                )
                await continuing.wait()
                release_siblings.set()
                status = await _settle_send_async(service=service, status=status)
                assert service._scheduler._conversations == {attack.conversation_id}
                current = await service.get_status_async(
                    attack_result_id=attack.attack_result_id, send_id=continued.send_id
                )
                assert current.state == MessageSendState.SENDING
            finally:
                release_metadata.set()
                release_siblings.set()
                release_continuation.set()
                status = await _settle_send_async(service=service, status=status)
                if continued:
                    assert (
                        await _settle_send_async(service=service, status=continued)
                    ).state == MessageSendState.COMPLETED
        assert status.state == MessageSendState.FAILED
        assert status.failure_stage == MessageSendFailureStage.SENDING
        assert [progress.state for progress in status.conversations].count(MessageSendState.COMPLETED) == 2
        assert not service._scheduler._conversations

    async def test_shutdown_joins_atomic_registration_and_publishes_committed_ids_async(
        self,
        *,
        real_send_context: tuple[MessageSendService, AttackResult, MockPromptTarget, Base64Converter],
        sqlite_instance: SQLiteMemory,
    ) -> None:
        service, attack, target, _ = real_send_context
        started, release = asyncio.Event(), asyncio.Event()
        register = sqlite_instance.add_conversation_branches_to_attack_async

        async def hold_registration_async(**kwargs: Any) -> bool:
            started.set()
            await release.wait()
            return await register(**kwargs)

        with patch.object(
            sqlite_instance, "add_conversation_branches_to_attack_async", side_effect=hold_registration_async
        ):
            status = await service.submit_async(
                attack_result_id=attack.attack_result_id,
                request=_submission(conversation_id=attack.conversation_id, count=3),
            )
            await started.wait()
            shutdown = asyncio.create_task(service.shutdown_async())
            task = service._sends[status.send_id].task
            assert task is not None
            try:
                await asyncio.sleep(0)
                for _ in range(2):
                    task.cancel()
                    shutdown.cancel()
                    await asyncio.sleep(0)
                    assert not shutdown.done()
                    assert len(service._scheduler._conversations) == 3
                    current = await service.get_status_async(
                        attack_result_id=attack.attack_result_id, send_id=status.send_id
                    )
                    assert current.state == MessageSendState.PREPARING
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await shutdown
        status = await service.get_status_async(attack_result_id=attack.attack_result_id, send_id=status.send_id)
        assert status.state == MessageSendState.INTERRUPTED
        assert len(status.conversations) == 3
        assert all(progress.state == MessageSendState.INTERRUPTED for progress in status.conversations)
        assert (
            len(await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id))
            == 3
        )
        assert target.prompt_sent == []
        assert not service._scheduler._conversations
