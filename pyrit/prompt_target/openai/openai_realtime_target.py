# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import logging
import re
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from openai import AsyncOpenAI

from pyrit.common import forward_init_parameters
from pyrit.common.deprecation import print_deprecation_message
from pyrit.exceptions import (
    pyrit_target_retry,
)
from pyrit.exceptions.exception_classes import ServerErrorException
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentIdentifier, Message, construct_response_from_request
from pyrit.prompt_target.common.realtime_audio import (
    RealtimeTargetResult,
    ServerVadConfig,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai._openai_realtime_event_router import (
    _OpenAIRealtimeEventKind,
    _OpenAIRealtimeEventRouter,
)
from pyrit.prompt_target.openai._openai_realtime_streaming_session import (
    _OpenAIRealtimeStreamingSession,
)
from pyrit.prompt_target.openai.openai_target import OpenAITarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer

logger = logging.getLogger(__name__)

# Voices supported by the OpenAI Realtime API.
# See: https://platform.openai.com/docs/guides/realtime-conversations#voice-options
# For best quality, OpenAI recommends using "marin" or "cedar".
RealTimeVoice = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]


@dataclass
class _RealtimeReceiveState:
    """
    Per-turn state for ``RealtimeTarget.receive_events_async``.

    The turn binds to the first ``response.created`` id it sees, and events tagged with
    any other response id are stale. The soft-finish deadline is fixed by the first
    ``audio.done`` and is never extended by later events.
    """

    #: Seconds to wait for ``response.done`` after ``audio.done`` before soft-finishing.
    GRACE_PERIOD_SEC: ClassVar[float] = 1.0

    audio_buffer: bytearray = field(default_factory=bytearray)
    transcripts: list[str] = field(default_factory=list)
    response_id: str | None = None
    accepted_event_count: int = 0
    completion_deadline: float | None = None

    def accepts(self, *, event_kind: _OpenAIRealtimeEventKind, event_response_id: str | None) -> bool:
        """
        Bind the turn to its response and report whether an event belongs to this turn.

        Args:
            event_kind (_OpenAIRealtimeEventKind): The classified event kind.
            event_response_id (str | None): The response id carried by the event, if any.

        Returns:
            bool: False when the event carries a response id other than the bound one.
        """
        if event_kind is _OpenAIRealtimeEventKind.RESPONSE_CREATED and self.response_id is None:
            self.response_id = event_response_id
            return True
        return event_response_id is None or event_response_id == self.response_id

    def start_completion_grace(self, *, clock: Callable[[], float]) -> None:
        """
        Fix the soft-finish deadline on the first ``audio.done``; later calls leave it unchanged.

        Args:
            clock (Callable[[], float]): Monotonic clock, read only when the deadline is set.
        """
        if self.completion_deadline is None:
            self.completion_deadline = clock() + self.GRACE_PERIOD_SEC

    def owns_response_done(self) -> bool:
        """
        Whether a ``response.done`` belongs to this turn rather than a prior turn's soft-finish.

        Returns:
            bool: True when audio has arrived or other accepted events preceded it.
        """
        return bool(self.audio_buffer) or self.accepted_event_count > 1

    def to_result(self) -> RealtimeTargetResult:
        """
        Build the turn result from the accumulated audio and transcripts.

        Returns:
            RealtimeTargetResult: The received audio bytes and transcript fragments.
        """
        return RealtimeTargetResult(audio_bytes=bytes(self.audio_buffer), transcripts=self.transcripts)


class RealtimeTarget(OpenAITarget):
    """
    A prompt target for Azure OpenAI Realtime API.

    This class enables real-time audio communication with OpenAI models, supporting
    voice input and output with configurable voice options.

    Read more at https://learn.microsoft.com/en-us/azure/ai-services/openai/realtime-audio-reference
    and https://platform.openai.com/docs/guides/realtime-websocket
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_streaming_audio=True,
            input_modalities=frozenset(
                {
                    frozenset(["text"]),
                    frozenset(["text", "audio_path"]),
                }
            ),
            output_modalities=frozenset(
                {
                    frozenset(["text"]),
                    frozenset(["audio_path"]),
                    frozenset(["text", "audio_path"]),
                }
            ),
        )
    )

    #: PCM sample rate in Hz negotiated by the OpenAI Realtime protocol. Single source
    #: of truth for both atomic (send_text/send_audio) and streaming session paths.
    SAMPLE_RATE_HZ: ClassVar[int] = 24000

    @forward_init_parameters
    def __init__(
        self,
        *,
        voice: RealTimeVoice | None = None,
        existing_convo: dict[str, Any] | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the Realtime target with specified parameters.

        Args:
            model_name (str, Optional): The name of the model (or deployment name in Azure).
                If no value is provided, the OPENAI_REALTIME_MODEL environment variable will be used.
            endpoint (str, Optional): The target URL for the OpenAI service.
                Defaults to the `OPENAI_REALTIME_ENDPOINT` environment variable.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Defaults to the `OPENAI_REALTIME_API_KEY` environment variable.
            headers (str, Optional): Headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            voice (literal str, Optional): The voice to use. Defaults to None.
                the only supported voices by the AzureOpenAI Realtime API are "alloy", "echo", and "shimmer".
            existing_convo (dict[str, websockets.WebSocketClientProtocol], Optional): Existing conversations.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(custom_configuration=custom_configuration, **kwargs)

        self.voice = voice
        self._existing_conversation = existing_convo if existing_convo is not None else {}
        self._realtime_client: AsyncOpenAI | None = None

    def open_streaming_session(
        self,
        *,
        audio_chunks: "AsyncIterator[bytes]",
        prompt_normalizer: "PromptNormalizer",
        conversation_id: str | None = None,
        request_converter_configurations: "list[ConverterConfiguration] | None" = None,
        response_converter_configurations: "list[ConverterConfiguration] | None" = None,
        prepended_conversation: list[Message] | None = None,
        server_vad: bool | ServerVadConfig = True,
        persist_prepended_conversation: bool = True,
    ) -> "_OpenAIRealtimeStreamingSession":
        """
        Open a new server-VAD streaming session bound to this target.

        Args:
            audio_chunks: Async iterator yielding PCM16 mono bytes at the target's
                ``SAMPLE_RATE_HZ`` rate.
            prompt_normalizer: Normalizer used to apply converters and persist messages.
            conversation_id: Conversation id for this session. Auto-generated when omitted.
            request_converter_configurations: Converters applied to each committed user turn
                before swap-and-respond.
            response_converter_configurations: Converters applied to each assistant turn
                before persistence.
            prepended_conversation: Optional conversation history. The leading system
                message becomes session instructions.
            server_vad: Server-side voice activity detection. ``True`` (default) enables
                VAD with default tuning. Pass a ``ServerVadConfig`` for custom tuning, or
                ``False`` to disable (sending streaming config will then raise).
            persist_prepended_conversation: When ``True`` (default), the session writes
                ``prepended_conversation`` to memory itself. Pass ``False`` when the
                caller already persisted the prepended conversation (e.g. via
                ``ConversationManager.initialize_context_async``) to avoid double-writes.

        Returns:
            A fresh ``_OpenAIRealtimeStreamingSession``. Drive it by iterating
            ``await session.run_async()``; one assistant ``Message`` is yielded per
            VAD-committed turn, and the matching user message is persisted to memory
            (but not yielded). The session owns its websocket connection + dispatcher
            for the duration of ``run_async``.
        """
        return _OpenAIRealtimeStreamingSession(
            target=self,
            audio_chunks=audio_chunks,
            prompt_normalizer=prompt_normalizer,
            conversation_id=conversation_id,
            request_converter_configurations=request_converter_configurations,
            response_converter_configurations=response_converter_configurations,
            prepended_conversation=prepended_conversation,
            server_vad=server_vad,
            persist_prepended_conversation=persist_prepended_conversation,
        )

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "OPENAI_REALTIME_MODEL"
        self.endpoint_environment_variable = "OPENAI_REALTIME_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_REALTIME_API_KEY"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/realtime", "/v1/realtime"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "wss://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "wss://api.openai.com/v1",
        }

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with Realtime API-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "voice": self.voice,
            },
        )

    def _validate_url_for_target(self, endpoint_url: str) -> None:
        """
        Validate URL for Realtime API with websocket-specific checks.

        Args:
            endpoint_url: The endpoint URL to validate.
        """
        # Convert https to wss for validation (this is expected for websockets)
        check_url = endpoint_url.replace("https://", "wss://") if endpoint_url.startswith("https://") else endpoint_url

        # Check for proper scheme
        if not check_url.startswith("wss://"):
            logger.warning(
                f"Realtime endpoint should use 'wss://' or 'https://' scheme, got: {endpoint_url}. "
                "The endpoint may not work correctly."
            )
            return

        # Call parent validation with the wss URL
        super()._validate_url_for_target(check_url)

    def _warn_if_irregular_realtime_endpoint(self, endpoint: str) -> None:
        """
        Warns if the endpoint URL does not match expected patterns.

        Args:
            endpoint: The endpoint URL to validate
        """
        # Expected patterns for realtime endpoints:
        # Azure old format: wss://resource.openai.azure.com/openai/realtime?api-version=...
        # Azure new format: wss://resource.openai.azure.com/openai/v1
        # Platform OpenAI: wss://api.openai.com/v1
        # Also accept https:// versions that will be converted to wss://

        # Check for proper scheme (wss:// or https://)
        if not endpoint.startswith(("wss://", "https://")):
            logger.warning(
                f"Realtime endpoint should start with 'wss://' or 'https://', got: {endpoint}. "
                "This may cause connection issues."
            )
            return

        # Pattern for Azure endpoints
        azure_pattern = re.compile(
            r"^(wss|https)://[a-zA-Z0-9\-]+\.openai\.azure\.com/"
            r"(openai/(deployments/[^/]+/)?realtime(\?api-version=[^/]+)?|openai/v1|v1)$"
        )

        # Pattern for Platform OpenAI
        platform_pattern = re.compile(r"^(wss|https)://api\.openai\.com/(v1(/realtime)?|realtime)$")

        if not azure_pattern.match(endpoint) and not platform_pattern.match(endpoint):
            logger.warning(
                f"Realtime endpoint URL does not match expected Azure or Platform OpenAI patterns: {endpoint}. "
                "Expected formats: 'wss://resource.openai.azure.com/openai/v1' or 'wss://api.openai.com/v1'"
            )

    def _get_openai_client(self) -> AsyncOpenAI:
        """
        Create or return the AsyncOpenAI client configured for Realtime API.
        Uses the Azure GA approach with websocket_base_url.

        Returns:
            AsyncOpenAI: Configured AsyncOpenAI client for Realtime API.
        """
        if self._realtime_client is None:
            # Convert https:// to wss:// for websocket connections if needed
            websocket_base_url = (
                self._endpoint.replace("https://", "wss://")
                if self._endpoint.startswith("https://")
                else self._endpoint
            )

            logger.info(f"Creating realtime client with websocket_base_url: {websocket_base_url}")

            self._realtime_client = AsyncOpenAI(
                websocket_base_url=websocket_base_url,
                api_key=self._api_key,
            )

        return self._realtime_client

    def _set_system_prompt_and_config_vars(
        self, system_prompt: str, *, server_vad: ServerVadConfig | None = None
    ) -> dict[str, Any]:
        """
        Create session configuration for OpenAI client.
        Uses the Azure GA format with nested audio config.

        Args:
            system_prompt: The system prompt to use in the session configuration.
            server_vad: When provided, emits a ``turn_detection`` block tuned by this
                config. The atomic path always omits it (server VAD is a streaming-only
                concept); the streaming session passes its resolved VAD here.

        Returns:
            dict: Session configuration dictionary.
        """
        session_config = {
            "type": "realtime",
            "instructions": system_prompt,
            "output_modalities": ["audio"],  # Use only audio modality
            "audio": {
                "input": {
                    "transcription": {
                        "model": "whisper-1",
                    },
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.SAMPLE_RATE_HZ,
                    },
                },
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.SAMPLE_RATE_HZ,
                    }
                },
            },
        }

        if server_vad is not None:
            session_config["audio"]["input"]["turn_detection"] = {  # type: ignore[ty:invalid-assignment]
                "type": "server_vad",
                "threshold": server_vad.threshold,
                "prefix_padding_ms": server_vad.prefix_padding_ms,
                "silence_duration_ms": server_vad.silence_duration_ms,
                "create_response": True,
                "interrupt_response": True,
            }

        if self.voice:
            session_config["audio"]["output"]["voice"] = self.voice  # type: ignore[ty:invalid-assignment]

        return session_config

    async def send_config_async(self, *, conversation_id: str, conversation: list[Message] | None = None) -> None:
        """
        Send the session configuration using OpenAI client.

        Args:
            conversation_id (str): Conversation ID
            conversation (list[Message] | None): The conversation history to extract the system
                prompt from. This is useful if the conversation has already been normalized and we want
                to use the normalized conversation. If None, the conversation is fetched from memory.
                Defaults to None.
        """
        # Extract system prompt from conversation history. Use the conversation passed in if available,
        # otherwise fetch from memory.
        resolved_conversation = (
            conversation
            if conversation is not None
            else list(await self._memory.get_conversation_messages_async(conversation_id=conversation_id))
        )
        system_prompt = self._get_system_prompt_from_conversation(conversation=resolved_conversation)
        config_variables = self._set_system_prompt_and_config_vars(system_prompt=system_prompt)

        connection = self._get_connection(conversation_id=conversation_id)
        await connection.session.update(session=config_variables)
        logger.info("Session configuration sent")

    def _get_system_prompt_from_conversation(self, *, conversation: list[Message]) -> str:
        """
        Retrieve the system prompt from conversation history.

        Args:
            conversation (list[Message]): The conversation messages to search.

        Returns:
            str: The system prompt from conversation history, or a default if none found
        """
        # Look for a system message at the beginning of the conversation
        if conversation and len(conversation) > 0:
            first_message = conversation[0]
            if first_message.message_pieces and first_message.message_pieces[0].api_role == "system":
                return first_message.message_pieces[0].converted_value

        # Return default system prompt if none found in conversation
        return "You are a helpful AI assistant"

    @pyrit_target_retry
    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously send a message to the OpenAI realtime target.

        Dispatches to the atomic send_audio / send_text path based on the
        request's data type. Streaming attacks bypass this entry point and drive
        the connection through ``_OpenAIRealtimeStreamingSession`` instead.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response from the prompt target.

        Raises:
            ValueError: If the message piece type is unsupported.
        """
        message = normalized_conversation[-1]
        conversation_id = message.message_pieces[0].conversation_id
        request = message.message_pieces[0]

        if not conversation_id:
            raise ValueError("RealtimeTarget requires a conversation_id on the message being sent.")

        if conversation_id not in self._existing_conversation:
            connection = await self._connect_async(conversation_id=conversation_id)
            self._existing_conversation[conversation_id] = connection

            try:
                # Only send config when creating a new connection
                await self.send_config_async(conversation_id=conversation_id, conversation=normalized_conversation)
                # Give the server a moment to process the session update
                await asyncio.sleep(0.5)
            except BaseException:
                await self.reset_conversation_async(conversation_id=conversation_id)
                raise

        response_type = request.converted_value_data_type

        # Order of messages sent varies based on the data format of the prompt
        if response_type == "audio_path":
            output_audio_path, result = await self.send_audio_async(
                filename=request.converted_value,
                conversation_id=conversation_id,
            )

        elif response_type == "text":
            output_audio_path, result = await self.send_text_async(
                text=request.converted_value,
                conversation_id=conversation_id,
            )
        else:
            raise ValueError(f"Unsupported response type: {response_type}")

        text_response_piece = construct_response_from_request(
            request=request, response_text_pieces=[result.flatten_transcripts()], response_type="text"
        ).message_pieces[0]

        audio_response_piece = construct_response_from_request(
            request=request, response_text_pieces=[output_audio_path], response_type="audio_path"
        ).message_pieces[0]

        if result.interrupted:
            text_response_piece.prompt_metadata["interrupted"] = True
            audio_response_piece.prompt_metadata["interrupted"] = True

        response_entry = Message(message_pieces=[text_response_piece, audio_response_piece])
        return [response_entry]

    async def cleanup_target_async(self) -> None:
        """
        Disconnects from the Realtime API connections.

        Closes every connection cached in ``_existing_conversation`` and the
        shared ``AsyncOpenAI`` client, swallowing per-connection errors so a
        single bad close does not block the rest. Safe to call multiple times.
        """
        for conversation_id, connection in list(self._existing_conversation.items()):
            if connection:
                try:
                    await connection.close()
                    logger.info(f"Disconnected from {self._endpoint} with conversation ID: {conversation_id}")
                except Exception as e:
                    logger.warning(f"Error closing connection for {conversation_id}: {e}")
        self._existing_conversation = {}

        if self._realtime_client:
            try:
                await self._realtime_client.close()
            except Exception as e:
                logger.warning(f"Error closing realtime client: {e}")
            self._realtime_client = None

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Disconnects from the Realtime API for a specific conversation.

        Closes the cached connection for ``conversation_id`` and drops it from
        ``_existing_conversation``. Errors while closing are logged and
        swallowed, and an unknown conversation id is a no-op, so this is safe
        to call from attack lifecycle cleanup.

        Args:
            conversation_id (str): The conversation ID to disconnect from.

        Raises:
            asyncio.CancelledError: If cleanup is cancelled, after the connection finishes closing.
        """
        connection = self._existing_conversation.pop(conversation_id, None)
        if not connection:
            return

        close_task = asyncio.ensure_future(connection.close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError as cancellation_error:
            try:
                await close_task
            except BaseException as close_error:
                raise cancellation_error from close_error
            raise
        except Exception as e:
            logger.warning(f"Error closing connection for {conversation_id}: {e}")
        else:
            logger.info(f"Disconnected from {self._endpoint} with conversation ID: {conversation_id}")

    async def cleanup_conversation_async(self, conversation_id: str) -> None:
        """
        Disconnect from the Realtime API for a specific conversation.

        Deprecated. Use ``reset_conversation_async`` instead.

        Args:
            conversation_id (str): The conversation ID to disconnect from.
        """
        print_deprecation_message(
            old_item="RealtimeTarget.cleanup_conversation_async",
            new_item="RealtimeTarget.reset_conversation_async",
            removed_in="1.3.0",
        )
        await self.reset_conversation_async(conversation_id=conversation_id)

    async def _connect_async(self, *, conversation_id: str) -> Any:
        """
        Open a fresh Realtime API websocket connection and return the connection handle.

        Args:
            conversation_id: Conversation ID for logging/diagnostics; the connection
                itself is not bound to a conversation server-side.

        Returns:
            The Realtime API connection handle.
        """
        logger.info(f"Connecting to Realtime API: {self._endpoint} (conversation_id={conversation_id})")

        client = self._get_openai_client()
        connection = await client.realtime.connect(model=self._model_name).__aenter__()

        logger.info("Successfully connected to AzureOpenAI Realtime API")
        return connection

    async def save_audio_async(
        self,
        audio_bytes: bytes,
        num_channels: int = 1,
        sample_width: int = 2,
        sample_rate: int = 16000,
        output_filename: str | None = None,
    ) -> str:
        """
        Save audio bytes to a WAV file.

        Args:
            audio_bytes (bytes): Audio bytes to save.
            num_channels (int): Number of audio channels. Defaults to 1 for the PCM16 format
            sample_width (int): Sample width in bytes. Defaults to 2 for the PCM16 format
            sample_rate (int): Sample rate in Hz. Defaults to 16000 Hz for the PCM16 format
            output_filename (str): Output filename. If None, a UUID filename will be used.

        Returns:
            str: The path to the saved audio file.
        """
        data = data_serializer_factory(category="prompt-memory-entries", data_type="audio_path")

        await data.save_formatted_audio_async(
            data=audio_bytes,
            output_filename=output_filename,
            num_channels=num_channels,
            sample_width=sample_width,
            sample_rate=sample_rate,
        )

        return data.value

    async def send_response_create_async(self, conversation_id: str) -> None:
        """
        Send response.create using OpenAI client.

        Args:
            conversation_id (str): Conversation ID
        """
        connection = self._get_connection(conversation_id=conversation_id)
        await connection.response.create()

    async def receive_events_async(self, conversation_id: str) -> RealtimeTargetResult:
        """
        Continuously receive events from the OpenAI Realtime API connection.

        Uses a robust "soft-finish" strategy to handle cases where response.done
        may not arrive. The first audio.done fixes a deadline one grace period
        later; if no response.done arrives by then, the turn soft-finishes. Later
        events never extend that deadline.

        Args:
            conversation_id: conversation ID

        Returns:
            RealtimeTargetResult with audio data and transcripts

        Raises:
            TimeoutError: If waiting for events times out.
            ConnectionError: If connection is not valid
            RuntimeError: If server returns an error
        """
        connection = self._get_connection(conversation_id=conversation_id)
        state = _RealtimeReceiveState()
        clock = asyncio.get_running_loop().time

        try:
            event_iter = connection.__aiter__()
            while True:
                event = await self._next_receive_event_async(event_iter=event_iter, state=state, clock=clock)
                if event is None or self._apply_receive_event(event=event, state=state, clock=clock):
                    break
        except Exception as e:
            logger.error(f"An unexpected error occurred for conversation {conversation_id}: {e}")
            raise

        result = state.to_result()
        logger.debug(
            f"Completed receive_events with {len(result.transcripts)} transcripts "
            f"and {len(result.audio_bytes)} bytes of audio"
        )
        return result

    @staticmethod
    async def _next_receive_event_async(
        *, event_iter: Any, state: _RealtimeReceiveState, clock: Callable[[], float]
    ) -> Any | None:
        """
        Wait for the next server event, bounded by the turn's fixed soft-finish deadline.

        Args:
            event_iter (Any): The connection's async event iterator.
            state (_RealtimeReceiveState): The current turn state.
            clock (Callable[[], float]): Monotonic clock the deadline is measured against.

        Returns:
            Any | None: The next event, or None when the turn should finish without one
                (stream ended, grace period expired, or connection closed after audio arrived).

        Raises:
            TimeoutError: If a timeout occurs before any audio.done was received.
        """
        timeout = None if state.completion_deadline is None else max(0.0, state.completion_deadline - clock())
        try:
            return await asyncio.wait_for(event_iter.__anext__(), timeout=timeout)
        except TimeoutError:
            if state.completion_deadline is None:
                raise
            logger.warning(
                f"Soft-finishing: No response.done {state.GRACE_PERIOD_SEC}s after audio.done. "
                f"Audio bytes: {len(state.audio_buffer)}"
            )
        except StopAsyncIteration:
            logger.debug("Event stream ended")
        except Exception as conn_err:
            # Treat a websockets close as a soft-finish only once some audio has arrived.
            if "ConnectionClosed" not in type(conn_err).__name__ or not state.audio_buffer:
                raise
            logger.warning(
                f"Connection closed without response.done (likely API issue). "
                f"Audio bytes received: {len(state.audio_buffer)}. Soft-finishing."
            )
        return None

    @staticmethod
    def _apply_receive_event(*, event: Any, state: _RealtimeReceiveState, clock: Callable[[], float]) -> bool:
        """
        Apply one server event to the per-turn receive state.

        Args:
            event (Any): The server event.
            state (_RealtimeReceiveState): The turn state to update.
            clock (Callable[[], float]): Monotonic clock used to fix the soft-finish deadline.

        Returns:
            bool: True when the event completes the turn.

        Raises:
            RuntimeError: If the server sends an error event.
            ServerErrorException: If response.done reports a failed response.
        """
        event_type = event.type
        event_kind = _OpenAIRealtimeEventRouter.classify_event(event_type)
        event_response_id = _OpenAIRealtimeEventRouter.get_response_id(event=event)
        if not state.accepts(event_kind=event_kind, event_response_id=event_response_id):
            logger.debug(
                f"Skipping event '{event_type}' for response {event_response_id}; "
                f"current response is {state.response_id}"
            )
            return False

        state.accepted_event_count += 1
        logger.debug(f"Processing event type: {event_type}")
        audio_size_before = len(state.audio_buffer)
        _OpenAIRealtimeEventRouter.collect_response_delta(
            event=event,
            event_kind=event_kind,
            audio_buffer=state.audio_buffer,
            transcripts=state.transcripts,
        )

        if event_kind is _OpenAIRealtimeEventKind.RESPONSE_DONE:
            return RealtimeTarget._apply_response_done(event=event, state=state)
        if event_kind is _OpenAIRealtimeEventKind.ERROR:
            raise RuntimeError(f"Server error: {RealtimeTarget._describe_server_error(event=event)}")
        if event_kind is _OpenAIRealtimeEventKind.AUDIO_DONE:
            logger.debug(f"Received audio.done - will soft-finish in {state.GRACE_PERIOD_SEC}s if no response.done")
            state.start_completion_grace(clock=clock)
            return False

        RealtimeTarget._log_receive_event(
            event=event, event_kind=event_kind, decoded_audio_bytes=len(state.audio_buffer) - audio_size_before
        )
        return False

    @staticmethod
    def _apply_response_done(*, event: Any, state: _RealtimeReceiveState) -> bool:
        """
        Handle a response.done event for the current turn.

        Args:
            event (Any): The response.done event.
            state (_RealtimeReceiveState): The turn state.

        Returns:
            bool: True when the event completes the turn, False when it is a stale
                leftover from a prior turn's soft-finish.
        """
        RealtimeTarget._handle_response_done_event(event=event, transcript_count=len(state.transcripts))
        if state.owns_response_done():
            logger.debug("Received response.done - finishing normally")
            return True
        # A response.done arriving as the very first event, with no audio, is left over in the
        # WebSocket buffer from a prior turn's soft-finish. Skip it and keep waiting.
        logger.debug(
            "Received response.done as first event with no audio data — "
            "likely a stale event from a prior turn's soft-finish. Skipping."
        )
        return False

    @staticmethod
    def _describe_server_error(*, event: Any) -> str:
        """
        Log a server error event and describe it.

        Args:
            event (Any): The error event.

        Returns:
            str: The error formatted as ``[type] message``.
        """
        error_message = event.error.message if hasattr(event.error, "message") else str(event.error)
        error_type = event.error.type if hasattr(event.error, "type") else "unknown"
        logger.error(f"Received 'error' event: [{error_type}] {error_message}")
        return f"[{error_type}] {error_message}"

    @staticmethod
    def _log_receive_event(*, event: Any, event_kind: _OpenAIRealtimeEventKind, decoded_audio_bytes: int) -> None:
        """
        Log a non-terminal event that only feeds the accumulated response.

        Args:
            event (Any): The server event.
            event_kind (_OpenAIRealtimeEventKind): The classified event kind.
            decoded_audio_bytes (int): Audio bytes the event added to the buffer.
        """
        if event_kind is _OpenAIRealtimeEventKind.AUDIO_DELTA:
            logger.debug(f"Decoded {decoded_audio_bytes} bytes of audio data")
        elif event_kind is _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA:
            if getattr(event, "delta", ""):
                logger.debug(f"Captured transcript delta: {event.delta[:50]}...")
        elif event_kind is _OpenAIRealtimeEventKind.OUTPUT_TEXT_DONE:
            logger.debug("Received text.done")
        elif _OpenAIRealtimeEventRouter.is_lifecycle_event(event_kind):
            logger.debug(f"Lifecycle event '{event.type}'")
        else:
            logger.debug(f"Unhandled event type '{event.type}'")

    def _get_connection(self, *, conversation_id: str) -> Any:
        """
        Get and validate the Realtime API connection for a conversation.

        Args:
            conversation_id: The conversation ID

        Returns:
            The Realtime API connection

        Raises:
            ConnectionError: If connection is not established
        """
        connection = self._existing_conversation.get(conversation_id)
        if connection is None:
            raise ConnectionError(f"Realtime API connection is not established for conversation {conversation_id}")
        return connection

    @staticmethod
    def _handle_response_done_event(*, event: Any, transcript_count: int) -> None:
        """
        Process a response.done event from OpenAI client.

        Args:
            event: The event object from OpenAI client
            transcript_count: Number of transcript fragments received so far, for logging

        Raises:
            ValueError: If event structure doesn't match expectations
            ServerErrorException: If response status is failed

        Note:
            We no longer extract transcripts here since we capture them from
            transcript.delta events. This avoids duplicates and supports soft-finish
            when response.done never arrives.
        """
        logger.debug("Processing 'response.done' event")

        response = event.response

        # Check for failed status
        status = response.status
        if status == "failed":
            error_details = RealtimeTarget._extract_error_details(response=response)
            raise ServerErrorException(message=error_details)

        # We used to extract transcript here, but now we collect it from delta events
        # to support soft-finish when response.done doesn't arrive
        logger.debug(f"Response completed successfully with {transcript_count} transcript fragments")

    @staticmethod
    def _extract_error_details(*, response: Any) -> str:
        """
        Extract error details from a failed response.

        Args:
            response: The response object from OpenAI client

        Returns:
            A formatted error message
        """
        if hasattr(response, "status_details") and response.status_details:
            status_details = response.status_details
            if hasattr(status_details, "error") and status_details.error:
                error = status_details.error
                error_type = error.type if hasattr(error, "type") else "unknown"
                error_message = error.message if hasattr(error, "message") else "No error message provided"
                return f"[{error_type}] {error_message}"
        return "Unknown error occurred"

    async def send_text_async(
        self,
        *,
        text: str,
        conversation_id: str,
    ) -> tuple[str, RealtimeTargetResult]:
        """
        Send text prompt using OpenAI Realtime API client.

        Args:
            text: prompt to send.
            conversation_id: conversation ID

        Returns:
            tuple[str, RealtimeTargetResult]: Path to saved audio file and the RealtimeTargetResult

        Raises:
            RuntimeError: If no audio is received from the server.
        """
        connection = self._get_connection(conversation_id=conversation_id)

        # Start listening for responses
        receive_task = asyncio.create_task(self.receive_events_async(conversation_id=conversation_id))

        logger.info(f"Sending text message: {text}")

        try:
            await connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            )
            await self.send_response_create_async(conversation_id=conversation_id)
            result = await receive_task
        finally:
            await self._cancel_receive_task_async(receive_task=receive_task)

        if not result.audio_bytes:
            raise RuntimeError("No audio received from the server.")

        # Azure GA uses 24000 Hz sample rate
        output_audio_path = await self.save_audio_async(audio_bytes=result.audio_bytes, sample_rate=24000)
        return output_audio_path, result

    async def send_audio_async(
        self,
        *,
        filename: str,
        conversation_id: str,
    ) -> tuple[str, RealtimeTargetResult]:
        """
        Send an audio message using OpenAI Realtime API client.

        Args:
            filename (str): The path to the audio file.
            conversation_id (str): Conversation ID

        Returns:
            tuple[str, RealtimeTargetResult]: Path to saved audio file and the RealtimeTargetResult

        Raises:
            Exception: If sending audio fails.
            RuntimeError: If no audio is received from the server.
        """
        connection = self._get_connection(conversation_id=conversation_id)

        audio_content, num_channels, sample_width, frame_rate = await asyncio.to_thread(self._read_wav_file, filename)

        receive_task = asyncio.create_task(self.receive_events_async(conversation_id=conversation_id))

        try:
            audio_base64 = base64.b64encode(audio_content).decode("utf-8")

            # Use conversation.item.create with input_audio (like Azure sample)
            logger.info(f"Sending audio message via conversation.item.create with {len(audio_base64)} bytes")
            await connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_audio", "audio": audio_base64}],
                }
            )
            logger.debug("Sending response.create")
            await self.send_response_create_async(conversation_id=conversation_id)
            logger.debug("Waiting for response events...")
            result = await receive_task
        except Exception as e:
            logger.error(f"Error sending audio: {e}")
            raise
        finally:
            await self._cancel_receive_task_async(receive_task=receive_task)

        if not result.audio_bytes:
            raise RuntimeError("No audio received from the server.")

        output_audio_path = await self.save_audio_async(result.audio_bytes, num_channels, sample_width, frame_rate)
        return output_audio_path, result

    async def _cancel_receive_task_async(self, *, receive_task: asyncio.Task[RealtimeTargetResult]) -> None:
        """Cancel and retrieve a Realtime receive task."""
        if not receive_task.done():
            receive_task.cancel()
        await asyncio.gather(receive_task, return_exceptions=True)

    async def _construct_message_from_response_async(self, response: Any, request: Any) -> Message:
        """
        Not used in RealtimeTarget - message construction handled by receive_events.
        This implementation exists to satisfy the abstract base class requirement.
        """
        raise NotImplementedError("RealtimeTarget uses receive_events for message construction")

    @staticmethod
    def _read_wav_file(filename: str) -> tuple[bytes, int, int, int]:
        """
        Read raw audio frames and format metadata from a WAV file.

        Args:
            filename (str): Path to the WAV file to read.

        Returns:
            tuple[bytes, int, int, int]: The raw audio frames, number of channels,
                sample width in bytes, and frame rate.
        """
        with wave.open(filename, "rb") as wav_file:
            return (
                wav_file.readframes(wav_file.getnframes()),
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
            )
