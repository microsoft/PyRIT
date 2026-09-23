# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the shared synchronous manual-message owner."""

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    ConverterConfigurationRequest,
    MessagePieceRequest,
)
from pyrit.backend.services.message_send_service import MessageSendService
from pyrit.models import AtomicAttackIdentifier, AttackResult, ComponentIdentifier, MessagePiece
from pyrit.prompt_normalizer import ConverterConfiguration
from unit.backend.mocks import _make_matching_target_mock, make_attack_result, make_mock_memory


@pytest.fixture
def mock_memory() -> MagicMock:
    return make_mock_memory()


@pytest.fixture
def message_send_service(mock_memory: MagicMock) -> MessageSendService:
    return MessageSendService(memory=mock_memory)


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
    mock_memory.get_attack_results.return_value = [attack_result]
    mock_memory.get_message_pieces.return_value = []

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
    return mock_memory.update_attack_result_by_id.call_args.kwargs["update_fields"]


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
        mock_memory.get_attack_results.return_value = []

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="not found"):
            await message_send_service.add_message_async(attack_result_id="nonexistent", request=request)

    async def test_add_message_raises_when_send_without_registry_name(self, message_send_service, mock_memory) -> None:
        """Test that add_message raises ValueError when send=True but target_registry_name missing."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=True,
        )

        with pytest.raises(ValueError, match="target_registry_name is required when send=True"):
            await message_send_service.add_message_async(attack_result_id="test-id", request=request)

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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.side_effect = [[], [response_piece]]
        mock_memory.get_conversation_messages.return_value = []

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
            update_fields = mock_memory.update_attack_result_by_id.call_args.kwargs["update_fields"]
            assert update_fields["last_response_id"] == str(response_piece.id)

    async def test_add_message_with_send_raises_when_target_not_found(self, message_send_service, mock_memory) -> None:
        """Test that add_message with send=True raises when target object not found."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []  # no error piece ever stored
        mock_memory.get_conversation_messages.return_value = []

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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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
                        original_value="https://example.com/image.png",
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

            update_fields = mock_memory.update_attack_result_by_id.call_args.kwargs["update_fields"]
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
        mock_memory.get_attack_results.return_value = [ar]
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

        mock_memory.add_conversation_to_memory.assert_not_called()
        mock_memory.add_message_pieces_to_memory.assert_not_called()
        mock_memory.update_attack_result_by_id.assert_not_called()

    async def test_add_message_bumps_timestamp(self, message_send_service, mock_memory) -> None:
        """Should bump the timestamp recency column via update_attack_result (no metadata write)."""
        ar = make_attack_result(conversation_id="test-id")
        ar.metadata = {"created_at": "2026-01-01T00:00:00+00:00"}
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        await message_send_service.add_message_async(attack_result_id="test-id", request=request)

        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        update_fields = call_kwargs["update_fields"]
        assert isinstance(update_fields["timestamp"], datetime)
        assert "attack_metadata" not in update_fields

    async def test_preconverted_piece_does_not_disable_other_piece_converters(
        self, message_send_service, mock_memory
    ) -> None:
        """Test that only the client-preconverted piece is excluded from conversion."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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
                    MessagePieceRequest(original_value="Hello", converted_value="SGVsbG8="),
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
            assert len(call_kwargs["response_converter_configurations"]) == 1
            update_call = mock_memory.update_attack_result_by_id.call_args[1]
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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

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

    async def test_image_piece_is_saved_to_file(self, message_send_service) -> None:
        """Base64 image data should be saved to disk and value replaced with file path."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",  # base64 for "imagedata"
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
        ) as factory_mock:
            await MessageSendService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

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

    async def test_http_url_is_kept_as_is(self, message_send_service) -> None:
        """HTTPS blob URLs should not be re-persisted."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="https://myblob.blob.core.windows.net/images/photo.png?sv=2024",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == ("https://myblob.blob.core.windows.net/images/photo.png?sv=2024")
        assert request.pieces[0].converted_value == request.pieces[0].original_value

    async def test_media_reference_is_resolved_without_persistence(self, message_send_service) -> None:
        """Local media URLs are converted back to their decoded file paths."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="/api/media?path=%2Ftmp%2Fimage.png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "/tmp/image.png"
        assert request.pieces[0].converted_value == "/tmp/image.png"
        factory.assert_not_called()

    async def test_existing_file_is_kept_without_persistence(self, message_send_service, tmp_path: Path) -> None:
        """An existing path remains the canonical original and converted value."""
        media_path = tmp_path / "image.png"
        media_path.write_bytes(b"image")
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="image_path", original_value=str(media_path))],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.message_send_service.data_serializer_factory") as factory:
            await MessageSendService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == str(media_path)
        assert request.pieces[0].converted_value == str(media_path)
        factory.assert_not_called()

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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

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

    async def test_allows_matching_target(self, message_send_service, mock_memory) -> None:
        """Should NOT raise when request target matches attack target."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="round-robin",
        )

        with patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc:
            mock_get_target_svc.return_value.get_target_object.return_value = request_target

            message_send_service._validate_target_match(attack_identifier=attack_identifier, request=request)

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
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="round-robin",
        )

        with patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc:
            mock_get_target_svc.return_value.get_target_object.return_value = request_target

            with pytest.raises(ValueError, match="Target mismatch"):
                message_send_service._validate_target_match(attack_identifier=attack_identifier, request=request)


class TestResolveVideoRemixMetadata:
    """Tests for _resolve_video_remix_metadata."""

    def test_resolves_video_id_from_original_piece(self, message_send_service, mock_memory):
        """When a video_path piece has original_prompt_id, resolve video_id onto text piece."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"video_id": "vid-abc-123"}
        mock_memory.get_message_pieces.return_value = [original_piece]

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

        message_send_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata == {"video_id": "vid-abc-123"}
        assert request.pieces[1].prompt_metadata == {"video_id": "vid-abc-123"}

    def test_no_op_without_video_pieces(self, message_send_service):
        """Should do nothing when there are no video_path pieces."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[MessagePieceRequest(original_value="just text", data_type="text")],
        )

        message_send_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata is None

    def test_no_op_when_video_id_already_set(self, message_send_service, mock_memory):
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

        message_send_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata == {"video_id": "existing-id"}
        mock_memory.get_message_pieces.assert_not_called()

    def test_no_op_without_original_prompt_id(self, message_send_service, mock_memory):
        """Should not crash when video_path piece has no original_prompt_id."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix", data_type="text"),
                MessagePieceRequest(original_value="/path/to/video.mp4", data_type="video_path"),
            ],
        )

        message_send_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata is None
        mock_memory.get_message_pieces.assert_not_called()

    def test_no_op_when_original_piece_has_no_video_id(self, message_send_service, mock_memory):
        """Should not set metadata when original piece has no video_id."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"other_key": "value"}
        mock_memory.get_message_pieces.return_value = [original_piece]

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

        message_send_service._resolve_video_remix_metadata(request)

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
