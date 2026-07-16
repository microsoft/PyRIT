# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import copy
import logging
import os
import tempfile
import traceback
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyrit.exceptions import (
    ComponentRole,
    EmptyResponseException,
    execution_context,
    get_execution_context,
)
from pyrit.memory import CentralMemory, MemoryInterface, set_message_piece_sha256_async
from pyrit.models import (
    ComponentIdentifier,
    Conversation,
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_normalizer import ConverterConfiguration, NormalizerRequest
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.batch_helper import batch_task_async

logger = logging.getLogger(__name__)


class PromptNormalizer:
    """
    Handles normalization and processing of prompts before they are sent to targets.
    """

    _memory: MemoryInterface | None = None

    @property
    def memory(self) -> MemoryInterface:
        """
        The memory instance.

        Raises:
            RuntimeError: If memory is not initialized.
        """
        if self._memory is None:
            raise RuntimeError("Memory is not initialized")
        return self._memory

    def __init__(self, start_token: str = "⟪", end_token: str = "⟫") -> None:
        """
        Initialize the PromptNormalizer.

        start_token and end_token are used to delineate which part of a prompt is converted.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._start_token = start_token
        self._end_token = end_token
        self.id = str(uuid4())

    async def send_prompt_async(
        self,
        *,
        message: Message,
        target: PromptTarget,
        conversation_id: str | None = None,
        request_converter_configurations: list[ConverterConfiguration] | None = None,
        response_converter_configurations: list[ConverterConfiguration] | None = None,
    ) -> Message:
        """
        Send a single request to a target.

        Args:
            message (Message): The message to be sent.
            target (PromptTarget): The target to which the prompt is sent.
            conversation_id (str, optional): The ID of the conversation. Defaults to None.
            request_converter_configurations (list[ConverterConfiguration], optional): Configurations for
                converting the request. Defaults to an empty list.
            response_converter_configurations (list[ConverterConfiguration], optional): Configurations for
                converting the response. Defaults to an empty list.

        Returns:
            Message: The response received from the target.

        Raises:
            Exception: If an error occurs during the request processing.
            ValueError: If the message pieces are not part of the same sequence.
        """
        # Validates that the MessagePieces in the Message are part of the same sequence
        request_converter_configurations = request_converter_configurations or []
        response_converter_configurations = response_converter_configurations or []
        if len({piece.sequence for piece in message.message_pieces}) > 1:
            raise ValueError("All MessagePieces in the Message must have the same sequence.")

        # Prepare the request by updating conversation ID
        request = copy.deepcopy(message)
        conversation_id = conversation_id if conversation_id else str(uuid4())
        target_identifier = target.get_identifier()
        self.memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )

        for piece in request.message_pieces:
            piece.conversation_id = conversation_id

        # Apply request converters
        await self.convert_values_async(converter_configurations=request_converter_configurations, message=request)

        await self._calc_hash_async(request=request)

        responses = None

        try:
            responses = await target.send_prompt_async(message=request)
            self.memory.add_message_to_memory(request=request)
        except EmptyResponseException:
            # Empty responses are retried, but we don't want them to stop execution
            self.memory.add_message_to_memory(request=request)

            responses = [
                construct_response_from_request(
                    request=request.message_pieces[0],
                    response_text_pieces=[""],
                    response_type="text",
                    error="empty",
                )
            ]

        except Exception as ex:
            # Ensure request to memory before processing exception
            self.memory.add_message_to_memory(request=request)

            error_response = construct_response_from_request(
                request=request.message_pieces[0],
                response_text_pieces=[f"{ex}\n{repr(ex)}\n{traceback.format_exc()}"],
                response_type="error",
                error="processing",
            )

            await self._calc_hash_async(request=error_response)
            self.memory.add_message_to_memory(request=error_response)
            cid = request.message_pieces[0].conversation_id if request and request.message_pieces else None
            raise Exception(f"Error sending prompt with conversation ID: {cid}") from ex

        # handling empty responses message list and None responses
        if not responses or not any(responses):
            # An empty list is valid for write-only targets (e.g., TextTarget)
            # that don't produce responses. Return the request as-is.
            if responses is not None and len(responses) == 0:
                return request
            empty_response = construct_response_from_request(
                request=request.message_pieces[0],
                response_text_pieces=[""],
                response_type="text",
                error="empty",
            )
            await self._calc_hash_async(request=empty_response)
            self.memory.add_message_to_memory(request=empty_response)
            return empty_response

        # Process all response messages (targets return list[Message])
        # Only apply response converters to the last message (final response)
        # Intermediate messages are tool calls/outputs that don't need conversion
        for i, resp in enumerate(responses):
            # A response belongs to the conversation it answers. Real targets already stamp this
            # (via construct_response_from_request), matching the request pieces stamped above;
            # enforcing it here keeps the persisted conversation coherent regardless of target.
            for piece in resp.message_pieces:
                piece.conversation_id = conversation_id
            is_last = i == len(responses) - 1
            if is_last:
                await self.convert_values_async(
                    converter_configurations=response_converter_configurations, message=resp
                )
            await self._calc_hash_async(request=resp)
            self.memory.add_message_to_memory(request=resp)

        # Return the last response for backward compatibility
        return responses[-1]

    async def send_prompt_batch_to_target_async(
        self,
        *,
        requests: list[NormalizerRequest],
        target: PromptTarget,
        labels: dict[str, str] | None = None,
        batch_size: int = 10,
    ) -> list[Message]:
        """
        Send a batch of prompts to the target asynchronously.

        Args:
            requests (list[NormalizerRequest]): A list of NormalizerRequest objects to be sent.
            target (PromptTarget): The target to which the prompts are sent.
            labels (dict[str, str] | None, optional): A dictionary of labels to attach to each request's
                message pieces. Defaults to None.
            batch_size (int, optional): The number of prompts to include in each batch. Defaults to 10.

        Returns:
            list[Message]: A list of Message objects representing the responses
                received for each prompt.
        """
        if labels:
            for request in requests:
                for piece in request.message.message_pieces:
                    piece.labels = labels

        batch_items: list[list[Any]] = [
            [request.message for request in requests],
            [request.request_converter_configurations for request in requests],
            [request.response_converter_configurations for request in requests],
            [request.conversation_id for request in requests],
        ]

        batch_item_keys = [
            "message",
            "request_converter_configurations",
            "response_converter_configurations",
            "conversation_id",
        ]

        return await batch_task_async(
            prompt_target=target,
            batch_size=batch_size,
            items_to_batch=batch_items,
            task_func=self.send_prompt_async,
            task_arguments=batch_item_keys,
            target=target,
        )

    async def convert_values_async(
        self,
        converter_configurations: list[ConverterConfiguration],
        message: Message,
    ) -> None:
        """
        Apply converter configurations to message pieces.

        Args:
            converter_configurations (list[ConverterConfiguration]): List of configurations specifying
                which converters to apply and to which message pieces.
            message (Message): The message containing pieces to be converted.

        Raises:
            Exception: Any exception from converters propagates with execution context for error tracing.
        """
        for converter_configuration in converter_configurations:
            for piece_index, piece in enumerate(message.message_pieces):
                indexes = converter_configuration.indexes_to_apply
                data_types = converter_configuration.prompt_data_types_to_apply

                if indexes and piece_index not in indexes:
                    continue
                if data_types and piece.converted_value_data_type not in data_types:
                    continue

                piece.converter_identifiers.extend(
                    [converter.get_identifier() for converter in converter_configuration.converters]
                )

                converted_text = piece.converted_value
                converted_text_data_type = piece.converted_value_data_type

                for converter in converter_configuration.converters:
                    # Inherit attack context from outer execution context (set by attack strategy)
                    outer_context = get_execution_context()

                    try:
                        with execution_context(
                            component_role=ComponentRole.CONVERTER,
                            attack_strategy_name=outer_context.attack_strategy_name if outer_context else None,
                            attack_identifier=outer_context.attack_identifier if outer_context else None,
                            component_identifier=converter.get_identifier(),
                            objective_target_conversation_id=(
                                outer_context.objective_target_conversation_id if outer_context else None
                            ),
                        ):
                            converter_result = await converter.convert_tokens_async(
                                prompt=converted_text,
                                input_type=converted_text_data_type,
                                start_token=self._start_token,
                                end_token=self._end_token,
                            )
                        converted_text = converter_result.output_text
                        converted_text_data_type = converter_result.output_type
                    except Exception:
                        # Let the exception propagate - execution context will add converter details
                        raise

                piece.converted_value = converted_text
                piece.converted_value_data_type = converted_text_data_type

    async def convert_audio_async(
        self,
        *,
        raw_pcm: bytes,
        converter_configurations: list[ConverterConfiguration],
        sample_rate_hz: int,
        num_channels: int,
        sample_width_bytes: int,
    ) -> bytes:
        """
        Apply converters to raw PCM audio and return the converted PCM.

        Wraps the input PCM in a temporary WAV file, builds a single-piece
        ``audio_path`` ``Message``, runs ``convert_values``, then reads the
        converted file back as raw PCM. The caller's PCM format is preserved
        end-to-end; converters that change the format trigger a ``ValueError``
        on read-back.

        Args:
            raw_pcm (bytes): Raw PCM audio samples (no WAV header).
            converter_configurations (list[ConverterConfiguration]):
                Converters to apply. If empty, ``raw_pcm`` is returned unchanged
                and no temp file is written.
            sample_rate_hz (int): Sample rate of the PCM in Hz.
            num_channels (int): Channel count (1 for mono, 2 for stereo).
            sample_width_bytes (int): Bytes per sample (2 for PCM16).

        Returns:
            bytes: The converted raw PCM, matching the input format.

        Raises:
            ValueError: If the converted audio has a different sample rate,
                channel count, or sample width than the input.
        """
        if not converter_configurations:
            return raw_pcm

        input_path = _write_pcm_to_temp_wav(
            raw_pcm=raw_pcm,
            sample_rate_hz=sample_rate_hz,
            num_channels=num_channels,
            sample_width_bytes=sample_width_bytes,
        )
        try:
            piece = MessagePiece(
                role="user",
                original_value=input_path,
                original_value_data_type="audio_path",
                converted_value=input_path,
                converted_value_data_type="audio_path",
            )
            message = Message(message_pieces=[piece])
            await self.convert_values_async(
                converter_configurations=converter_configurations,
                message=message,
            )
            actual_rate, actual_channels, actual_width, converted_pcm = _read_pcm_from_wav(piece.converted_value)
            if (actual_rate, actual_channels, actual_width) != (
                sample_rate_hz,
                num_channels,
                sample_width_bytes,
            ):
                raise ValueError(
                    "Converted audio format mismatch: expected "
                    f"channels={num_channels} sampwidth={sample_width_bytes} "
                    f"rate={sample_rate_hz}, got channels={actual_channels} "
                    f"sampwidth={actual_width} rate={actual_rate}."
                )
            return converted_pcm
        finally:
            Path(input_path).unlink(missing_ok=True)

    async def _calc_hash_async(self, request: Message) -> None:
        """Add a request to the memory."""
        tasks = [asyncio.create_task(set_message_piece_sha256_async(piece)) for piece in request.message_pieces]
        await asyncio.gather(*tasks)

    async def hash_and_persist_message_async(self, *, message: Message) -> None:
        """
        Hash and persist a Message to memory.

        Use when a target assembles a Message outside the ``send_prompt_async`` flow
        (e.g. streaming sessions that yield per-turn Messages directly). Register the
        conversation once via ``MemoryInterface.add_conversation_to_memory`` before
        persisting its messages.

        Args:
            message (Message): The message to hash and persist.
        """
        await self._calc_hash_async(request=message)
        self.memory.add_message_to_memory(request=message)

    async def add_prepended_conversation_to_memory_async(
        self,
        conversation_id: str,
        should_convert: bool = True,
        converter_configurations: list[ConverterConfiguration] | None = None,
        prepended_conversation: list[Message] | None = None,
        target_identifier: ComponentIdentifier | None = None,
    ) -> list[Message] | None:
        """
        Process the prepended conversation by converting it if needed and adding it to memory.

        Args:
            conversation_id (str): The conversation ID to use for the message pieces
            should_convert (bool): Whether to convert the prepended conversation
            converter_configurations (list[ConverterConfiguration] | None): Configurations for converting the
                request
            prepended_conversation (list[Message] | None): The conversation to prepend
            target_identifier (ComponentIdentifier | None): The target the conversation is held
                with, if known. Recorded once per conversation.

        Returns:
            list[Message] | None: The processed prepended conversation
        """
        if not prepended_conversation:
            return None

        # Create a deep copy of the prepended conversation to avoid modifying the original
        prepended_conversation = copy.deepcopy(prepended_conversation)
        self.memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )

        for request in prepended_conversation:
            if should_convert and converter_configurations:
                await self.convert_values_async(message=request, converter_configurations=converter_configurations)
            for piece in request.message_pieces:
                piece.conversation_id = conversation_id

                # if the piece is retrieved from somewhere else, it needs to be unique
                # and if not, this won't hurt anything
                piece.id = uuid4()

            self.memory.add_message_to_memory(request=request)

        return prepended_conversation


def _write_pcm_to_temp_wav(
    *,
    raw_pcm: bytes,
    sample_rate_hz: int,
    num_channels: int,
    sample_width_bytes: int,
) -> str:
    """Return the path of a new temp WAV file containing the given PCM."""
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wav_out:
        wav_out.setnchannels(num_channels)
        wav_out.setsampwidth(sample_width_bytes)
        wav_out.setframerate(sample_rate_hz)
        wav_out.writeframes(raw_pcm)
    return path


def _read_pcm_from_wav(wav_path: str) -> tuple[int, int, int, bytes]:
    """Return (sample_rate_hz, num_channels, sample_width_bytes, pcm_bytes) from a WAV file."""
    with wave.open(wav_path, "rb") as wav_in:
        return (
            wav_in.getframerate(),
            wav_in.getnchannels(),
            wav_in.getsampwidth(),
            wav_in.readframes(wav_in.getnframes()),
        )
