# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import logging
import re
import wave
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Optional

from openai import AsyncOpenAI

from pyrit.exceptions import (
    pyrit_target_retry,
)
from pyrit.exceptions.exception_classes import ServerErrorException
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    Message,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.realtime_audio import (
    CommittedEvent,
    RealtimeEventDispatcher,
    RealtimeTargetResult,
    RealtimeTurnState,
    ServerVadConfig,
    StreamingHandle,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_target import OpenAITarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import (
        _OpenAIRealtimeStreamingSession,
    )

logger = logging.getLogger(__name__)

# Voices supported by the OpenAI Realtime API.
# See: https://platform.openai.com/docs/guides/realtime-conversations#voice-options
# For best quality, OpenAI recommends using "marin" or "cedar".
RealTimeVoice = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]


class _RealtimeStreamingHandle(StreamingHandle):
    """
    OpenAI Realtime API implementation of :class:`StreamingHandle`.

    Owns the websocket-level streaming surface (connect, push audio, config) and
    the audio persistence helper. Holds a back-reference to its owning
    :class:`RealtimeTarget` so it can read per-target state (server VAD config,
    OpenAI client, conversation registries).
    """

    SAMPLE_RATE_HZ: ClassVar[int] = 24000

    def __init__(self, target: "RealtimeTarget") -> None:
        self._target = target

    @property
    def server_vad_config(self) -> ServerVadConfig | None:
        return self._target._server_vad

    async def connect_async(self, conversation_id: str) -> Any:
        """
        Connect to Realtime API using AsyncOpenAI client and return the realtime connection.

        Returns:
            The Realtime API connection.
        """
        logger.info(f"Connecting to Realtime API: {self._target._endpoint}")

        client = self._target._get_openai_client()
        connection = await client.realtime.connect(model=self._target._model_name).__aenter__()

        logger.info("Successfully connected to AzureOpenAI Realtime API")
        return connection

    async def send_streaming_session_config_async(
        self,
        *,
        connection: Any,
        conversation: list[Message] | None = None,
        vad: ServerVadConfig | None = None,
    ) -> None:
        """
        Configure the realtime session for streaming use: server VAD with manual response creation.

        Emits the same session config as the atomic path except ``turn_detection.create_response``
        is forced to False so the streaming attack can swap the raw user audio item for converted
        audio before triggering ``response.create``.

        Args:
            connection: Active Realtime API connection.
            conversation: Optional conversation history; if its first message is a system
                message, its text becomes the session's instructions. Defaults to None,
                in which case the default system prompt is used.
            vad: Optional per-call VAD tuning. When provided, overrides the target's
                constructor-set ``_server_vad``. When None, falls back to the target's
                constructor value (existing behavior).

        Raises:
            ValueError: If neither ``vad`` nor the target's ``_server_vad`` is set.
        """
        effective_vad = vad if vad is not None else self._target._server_vad
        if effective_vad is None:
            raise ValueError(
                "send_streaming_session_config_async requires server VAD; "
                "pass vad=ServerVadConfig(...) or construct RealtimeTarget(server_vad=True)."
            )
        system_prompt = self._target._get_system_prompt_from_conversation(conversation=conversation or [])
        config = self._target._set_system_prompt_and_config_vars(system_prompt=system_prompt, server_vad=effective_vad)
        turn_detection = config.get("audio", {}).get("input", {}).get("turn_detection")
        if turn_detection is not None:
            turn_detection["create_response"] = False
        await connection.session.update(session=config)

    async def push_audio_chunk_async(self, *, connection: Any, pcm_bytes: bytes) -> None:
        """
        Append a single PCM16 mono @ 24 kHz audio chunk to the server's input buffer.

        Used by streaming-style callers (e.g. ``BargeInAttack``) that source chunks
        from an iterator and want to control commit timing externally. Server VAD,
        when enabled on the session, decides when to commit and fire response logic.
        Empty buffers are accepted as no-ops.

        Args:
            connection: Active Realtime API connection from ``connect_async``.
            pcm_bytes: Raw PCM16 mono audio for this chunk.
        """
        if not pcm_bytes:
            return
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await connection.input_audio_buffer.append(audio=audio_b64)

    async def save_audio(
        self,
        audio_bytes: bytes,
        num_channels: int = 1,
        sample_width: int = 2,
        sample_rate: int = 16000,
        output_filename: Optional[str] = None,
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

        await data.save_formatted_audio(
            data=audio_bytes,
            output_filename=output_filename,
            num_channels=num_channels,
            sample_width=sample_width,
            sample_rate=sample_rate,
        )

        return data.value


class RealtimeTarget(OpenAITarget, PromptTarget):
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
            supports_streaming_barge_in=True,
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

    #: Narrower override of ``PromptTarget.streaming``. ``RealtimeTarget`` always sets
    #: this in ``__init__``, so it is guaranteed non-None for downstream callers.
    streaming: "_RealtimeStreamingHandle"

    def __init__(
        self,
        *,
        voice: Optional[RealTimeVoice] = None,
        existing_convo: Optional[dict[str, Any]] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
        server_vad: bool | ServerVadConfig = False,
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
            server_vad (bool | ServerVadConfig): Server-side voice activity detection (VAD).
                ``False`` (default) keeps the existing atomic send/receive behavior.
                ``True`` enables VAD with default tuning.
                Pass a ``ServerVadConfig`` to enable with custom tuning. Streaming attacks
                obtain a dedicated session via :meth:`open_streaming_session` and require
                VAD to be enabled.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(custom_configuration=custom_configuration, **kwargs)

        self.voice = voice
        self._existing_conversation = existing_convo if existing_convo is not None else {}
        self._realtime_client: Optional[AsyncOpenAI] = None

        if isinstance(server_vad, ServerVadConfig):
            self._server_vad: Optional[ServerVadConfig] = server_vad
        elif server_vad:
            self._server_vad = ServerVadConfig()
        else:
            self._server_vad = None

        # Composition: streaming surface lives on a dedicated handle so the attack can
        # type against the provider-agnostic ``StreamingHandle`` ABC.
        self.streaming = _RealtimeStreamingHandle(target=self)

    def open_streaming_session(
        self,
        *,
        audio_chunks: "AsyncIterator[bytes]",
        prompt_normalizer: "PromptNormalizer",
        conversation_id: str | None = None,
        request_converter_configurations: "list[PromptConverterConfiguration] | None" = None,
        response_converter_configurations: "list[PromptConverterConfiguration] | None" = None,
        prepended_conversation: list[Message] | None = None,
        vad: ServerVadConfig | None = None,
        attack_identifier: "ComponentIdentifier | None" = None,
        persist_prepended_conversation: bool = True,
    ) -> "_OpenAIRealtimeStreamingSession":
        """
        Open a new server-VAD streaming session bound to this target.

        Returns:
            A fresh :class:`_OpenAIRealtimeStreamingSession`. Drive it by iterating
            ``await session.run_async()``; one assistant ``Message`` is yielded per
            VAD-committed turn, and the matching user message is persisted to memory
            (but not yielded). The session owns its websocket connection + dispatcher
            for the duration of ``run_async``.

        Args:
            audio_chunks: Async iterator yielding PCM16 mono bytes at the target's
                ``streaming.SAMPLE_RATE_HZ`` rate.
            prompt_normalizer: Normalizer used to apply converters and persist messages.
            conversation_id: Conversation id for this session. Auto-generated when omitted.
            request_converter_configurations: Converters applied to each committed user turn
                before swap-and-respond.
            response_converter_configurations: Converters applied to each assistant turn
                before persistence.
            prepended_conversation: Optional conversation history. The leading system
                message becomes session instructions.
            vad: Optional per-call VAD tuning. When ``None``, falls back to the target's
                constructor-set ``server_vad``.
            attack_identifier: Stamped on every persisted user / assistant piece for
                attribution. Pass the caller's identifier so live messages share the
                provenance contract of prepended messages.
            persist_prepended_conversation: When ``True`` (default), the session writes
                ``prepended_conversation`` to memory itself. Pass ``False`` when the
                caller already persisted the prepended conversation (e.g. via
                ``ConversationManager.initialize_context_async``) to avoid double-writes.
        """
        # Local import: the session module imports ``_OpenAIRealtimeDispatcher`` from
        # this module, so a module-level import here would be circular.
        from pyrit.prompt_target.openai._openai_realtime_streaming_session import (
            _OpenAIRealtimeStreamingSession,
        )

        return _OpenAIRealtimeStreamingSession(
            target=self,
            audio_chunks=audio_chunks,
            prompt_normalizer=prompt_normalizer,
            conversation_id=conversation_id,
            request_converter_configurations=request_converter_configurations,
            response_converter_configurations=response_converter_configurations,
            prepended_conversation=prepended_conversation,
            vad=vad,
            attack_identifier=attack_identifier,
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
            server_vad: Optional VAD override. When None, falls back to the target's
                constructor-set ``self._server_vad``.

        Returns:
            dict: Session configuration dictionary.
        """
        effective_vad = server_vad if server_vad is not None else self._server_vad
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
                        "rate": self.streaming.SAMPLE_RATE_HZ,
                    },
                },
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.streaming.SAMPLE_RATE_HZ,
                    }
                },
            },
        }

        if effective_vad is not None:
            session_config["audio"]["input"]["turn_detection"] = {  # type: ignore[ty:invalid-assignment]
                "type": "server_vad",
                "threshold": effective_vad.threshold,
                "prefix_padding_ms": effective_vad.prefix_padding_ms,
                "silence_duration_ms": effective_vad.silence_duration_ms,
                "create_response": True,
                "interrupt_response": True,
            }

        if self.voice:
            session_config["audio"]["output"]["voice"] = self.voice  # type: ignore[ty:invalid-assignment]

        return session_config

    async def send_config(self, *, conversation_id: str, conversation: list[Message] | None = None) -> None:
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
            else list(self._memory.get_conversation(conversation_id=conversation_id))
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

    @limit_requests_per_minute
    @pyrit_target_retry
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously send a message to the OpenAI realtime target.

        Dispatches to the atomic send_audio / send_text path based on the
        request's data type. Streaming attacks bypass this entry point and drive
        the connection through :class:`_OpenAIRealtimeStreamingSession` instead.

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

        if conversation_id not in self._existing_conversation:
            connection = await self.streaming.connect_async(conversation_id=conversation_id)
            self._existing_conversation[conversation_id] = connection

            # Only send config when creating a new connection
            await self.send_config(conversation_id=conversation_id, conversation=normalized_conversation)
            # Give the server a moment to process the session update
            await asyncio.sleep(0.5)

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

    async def cleanup_target(self) -> None:
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

    async def send_response_create(self, conversation_id: str) -> None:
        """
        Send response.create using OpenAI client.

        Args:
            conversation_id (str): Conversation ID
        """
        connection = self._get_connection(conversation_id=conversation_id)
        await connection.response.create()

    async def insert_user_audio_async(self, *, connection: Any, pcm_bytes: bytes) -> None:
        """
        Insert a user message containing the given PCM16 mono @ 24 kHz audio into the conversation.

        Use for the convert-on-commit dance — after deleting the server's raw user item,
        the attack inserts the converted audio via this method before manually triggering
        ``response.create``.

        Args:
            connection: Active Realtime API connection.
            pcm_bytes: Converted PCM16 mono audio.
        """
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_audio", "audio": audio_b64}],
            }
        )

    async def delete_conversation_item_async(self, *, connection: Any, item_id: str) -> None:
        """
        Delete a conversation item by id (e.g. the server's raw user audio item).

        Used during convert-on-commit to remove the raw audio item before replacing
        it with a converted one. Errors are propagated; callers that want best-effort
        deletion should wrap with ``contextlib.suppress``.

        Args:
            connection: Active Realtime API connection.
            item_id: Server-assigned item id to delete.
        """
        await connection.conversation.item.delete(item_id=item_id)

    async def swap_user_audio_async(
        self,
        *,
        connection: Any,
        committed_event: CommittedEvent,
        converted_pcm: bytes,
    ) -> None:
        """
        Replace the server's just-committed user audio with converted PCM.

        Inserts ``converted_pcm`` as a new user item and best-effort deletes the original
        item identified by ``committed_event``. Hides OpenAI's item-id concept from
        callers so streaming attacks can stay provider-agnostic.

        Args:
            connection: Active Realtime API connection.
            committed_event: Payload received in the on-committed callback.
            converted_pcm: PCM16 mono @ 24 kHz audio to insert in place of the original.
        """
        await self.insert_user_audio_async(connection=connection, pcm_bytes=converted_pcm)
        try:
            await self.delete_conversation_item_async(connection=connection, item_id=committed_event.item_id)
        except Exception as e:
            logger.warning(f"conversation.item.delete failed for {committed_event.item_id}: {e}")

    async def request_response_async(
        self,
        *,
        connection: Any,
        dispatcher: RealtimeEventDispatcher,
    ) -> asyncio.Future[RealtimeTargetResult]:
        """
        Trigger ``response.create`` and return a future that resolves when the turn ends.

        Constructs a fresh ``RealtimeTurnState``, binds it to the dispatcher as the
        active turn, then sends ``response.create``. The dispatcher resolves the
        returned future via ``response.done`` (with ``interrupted=False``) or via
        the barge-in cancel path (with ``interrupted=True``).

        Args:
            connection: Active Realtime API connection.
            dispatcher: The dispatcher driving this connection. Must not have
                another turn pending.

        Returns:
            Future resolved with the assembled ``RealtimeTargetResult`` when this
            turn ends (normally or via barge-in).

        Raises:
            RuntimeError: If another turn is already pending on the dispatcher.
        """
        state = RealtimeTurnState(completion=asyncio.get_running_loop().create_future())
        dispatcher.register_turn(state)
        await connection.response.create()
        return state.completion

    async def receive_events(self, conversation_id: str) -> RealtimeTargetResult:
        """
        Continuously receive events from the OpenAI Realtime API connection.

        Uses a robust "soft-finish" strategy to handle cases where response.done
        may not arrive. After receiving audio.done, waits for a grace period
        before soft-finishing if no response.done arrives.

        Args:
            conversation_id: conversation ID

        Returns:
            RealtimeTargetResult with audio data and transcripts

        Raises:
            asyncio.TimeoutError: If waiting for events times out.
            ConnectionError: If connection is not valid
            RuntimeError: If server returns an error
        """
        connection = self._get_connection(conversation_id=conversation_id)

        result = RealtimeTargetResult()
        audio_done_received = False
        current_turn_event_count = 0
        grace_period_sec = 1.0  # Wait 1 second after audio.done before soft-finishing

        try:
            # Create event iterator
            event_iter = connection.__aiter__()

            while True:
                # If we've seen audio.done, wait with a short timeout for response.done
                # Otherwise, wait indefinitely for events
                timeout = grace_period_sec if audio_done_received else None

                try:
                    event = await asyncio.wait_for(event_iter.__anext__(), timeout=timeout)
                except asyncio.TimeoutError:
                    # Soft-finish: audio.done was received but no response.done after grace period
                    if audio_done_received:
                        logger.warning(
                            f"Soft-finishing: No response.done {grace_period_sec}s after audio.done. "
                            f"Audio bytes: {len(result.audio_bytes)}"
                        )
                        break
                    # Should not happen if timeout is None, but re-raise if it does
                    raise
                except StopAsyncIteration:
                    # Connection closed normally
                    logger.debug("Event stream ended")
                    break
                except Exception as conn_err:
                    # Handle websockets connection errors as soft-finish if we have audio
                    if "ConnectionClosed" in str(type(conn_err).__name__) and result.audio_bytes:
                        logger.warning(
                            f"Connection closed without response.done (likely API issue). "
                            f"Audio bytes received: {len(result.audio_bytes)}. Soft-finishing."
                        )
                        break
                    # Re-raise if not a connection close or no audio received
                    raise

                event_type = event.type
                current_turn_event_count += 1
                logger.debug(f"Processing event type: {event_type}")

                if event_type == "response.done":
                    self._handle_response_done_event(event=event, result=result)
                    if result.audio_bytes or current_turn_event_count > 1:
                        # Legitimate response.done: either we have audio, or other events
                        # (e.g. response.created) preceded it, confirming it belongs to this turn.
                        logger.debug("Received response.done - finishing normally")
                        break
                    # Stale response.done from a previous turn's soft-finish that was
                    # left unconsumed in the WebSocket buffer. This is the very first
                    # event received, so it can't belong to the current turn. Skip it
                    # and continue waiting for the current turn's events.
                    logger.debug(
                        "Received response.done as first event with no audio data — "
                        "likely a stale event from a prior turn's soft-finish. Skipping."
                    )

                elif event_type == "error":
                    error_message = event.error.message if hasattr(event.error, "message") else str(event.error)
                    error_type = event.error.type if hasattr(event.error, "type") else "unknown"
                    logger.error(f"Received 'error' event: [{error_type}] {error_message}")
                    raise RuntimeError(f"Server error: [{error_type}] {error_message}")

                elif event_type in ["response.audio.delta", "response.output_audio.delta"]:
                    audio_data = base64.b64decode(event.delta)
                    result.audio_bytes += audio_data
                    logger.debug(f"Decoded {len(audio_data)} bytes of audio data")

                elif event_type in ["response.audio.done", "response.output_audio.done"]:
                    logger.debug(f"Received audio.done - will soft-finish in {grace_period_sec}s if no response.done")
                    audio_done_received = True

                elif event_type in ["response.audio_transcript.delta", "response.output_audio_transcript.delta"]:
                    # Capture transcript deltas as they arrive (needed when response.done never comes)
                    if hasattr(event, "delta") and event.delta:
                        result.transcripts.append(event.delta)
                        logger.debug(f"Captured transcript delta: {event.delta[:50]}...")

                elif event_type in ["response.output_text.done"]:
                    logger.debug("Received text.done")

                # Handle lifecycle events that we can safely log
                elif event_type in [
                    "session.created",
                    "session.updated",
                    "conversation.created",
                    "conversation.item.created",
                    "conversation.item.added",
                    "conversation.item.done",
                    "input_audio_buffer.committed",
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "conversation.item.input_audio_transcription.completed",
                    "response.created",
                    "response.output_item.added",
                    "response.output_item.created",
                    "response.output_item.done",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.audio_transcript.done",
                    "response.output_audio_transcript.done",
                    "response.output_text.delta",
                    "rate_limits.updated",
                ]:
                    logger.debug(f"Lifecycle event '{event_type}'")

                else:
                    logger.debug(f"Unhandled event type '{event_type}'")

        except Exception as e:
            logger.error(f"An unexpected error occurred for conversation {conversation_id}: {e}")
            raise

        logger.debug(
            f"Completed receive_events with {len(result.transcripts)} transcripts "
            f"and {len(result.audio_bytes)} bytes of audio"
        )
        return result

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
    def _handle_response_done_event(*, event: Any, result: RealtimeTargetResult) -> None:
        """
        Process a response.done event from OpenAI client.

        Args:
            event: The event object from OpenAI client
            result: RealtimeTargetResult to update

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
        logger.debug(f"Response completed successfully with {len(result.transcripts)} transcript fragments")

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
            Tuple[str, RealtimeTargetResult]: Path to saved audio file and the RealtimeTargetResult

        Raises:
            RuntimeError: If no audio is received from the server.
        """
        connection = self._get_connection(conversation_id=conversation_id)

        # Start listening for responses
        receive_tasks = asyncio.create_task(self.receive_events(conversation_id=conversation_id))

        logger.info(f"Sending text message: {text}")

        # Send conversation item
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )

        # Request response from model
        await self.send_response_create(conversation_id=conversation_id)

        # Wait for response - receive_events has its own soft-finish logic
        result = await receive_tasks

        if not result.audio_bytes:
            raise RuntimeError("No audio received from the server.")

        # Azure GA uses 24000 Hz sample rate
        output_audio_path = await self.streaming.save_audio(audio_bytes=result.audio_bytes, sample_rate=24000)
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
            Tuple[str, RealtimeTargetResult]: Path to saved audio file and the RealtimeTargetResult

        Raises:
            Exception: If sending audio fails.
            RuntimeError: If no audio is received from the server.
        """
        connection = self._get_connection(conversation_id=conversation_id)

        with wave.open(filename, "rb") as wav_file:
            # Read WAV parameters
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()  # Should be 2 bytes for PCM16
            frame_rate = wav_file.getframerate()
            num_frames = wav_file.getnframes()

            audio_content = wav_file.readframes(num_frames)

        receive_tasks = asyncio.create_task(self.receive_events(conversation_id=conversation_id))

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

        except Exception as e:
            logger.error(f"Error sending audio: {e}")
            raise

        logger.debug("Sending response.create")
        await self.send_response_create(conversation_id=conversation_id)

        logger.debug("Waiting for response events...")
        # Wait for response - receive_events has its own soft-finish logic
        result = await receive_tasks
        if not result.audio_bytes:
            raise RuntimeError("No audio received from the server.")

        output_audio_path = await self.streaming.save_audio(result.audio_bytes, num_channels, sample_width, frame_rate)
        return output_audio_path, result

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Not used in RealtimeTarget - message construction handled by receive_events.
        This implementation exists to satisfy the abstract base class requirement.
        """
        raise NotImplementedError("RealtimeTarget uses receive_events for message construction")


class _OpenAIRealtimeDispatcher(RealtimeEventDispatcher):
    """
    Concrete ``RealtimeEventDispatcher`` for the OpenAI Realtime API.

    Routes OpenAI server events into the active ``RealtimeTurnState`` and issues
    ``response.cancel`` plus ``conversation.item.truncate`` when interrupted.
    """

    async def _route_event(self, *, event: Any, state: RealtimeTurnState | None) -> None:
        """Route an OpenAI Realtime event to the active turn or to an input-side callback."""
        event_type = getattr(event, "type", "")

        # Capture audio_start_ms from speech_started for the next committed event.
        # The server reports it reliably here but omits it from the commit event itself.
        # Do not return — the downstream state-aware branch still needs to fire the
        # barge-in cancel when speech starts mid-response.
        if event_type == "input_audio_buffer.speech_started":
            speech_start = getattr(event, "audio_start_ms", None)
            if speech_start is not None:
                self._pending_speech_start_ms = speech_start

        # Input-side events fire callbacks regardless of whether a turn is registered.
        if event_type == "input_audio_buffer.committed":
            item_id = getattr(event, "item_id", None)
            if item_id is None:
                return
            audio_start_ms = getattr(event, "audio_start_ms", None)
            if audio_start_ms is None:
                audio_start_ms = self._pending_speech_start_ms
            self._pending_speech_start_ms = None
            self._fire_committed_callback(
                CommittedEvent(
                    item_id=item_id,
                    audio_start_ms=audio_start_ms,
                )
            )
            # Fall through: also include the bookkeeping below (none currently uses committed).
            return

        # Remaining events are output-side and mutate per-turn state; drop if no turn.
        if state is None or state.completion.done():
            return

        if event_type == "response.created":
            state.is_responding = True
            response = getattr(event, "response", None)
            if response is not None:
                state.last_response_id = getattr(response, "id", None)
            return

        if event_type in ("response.output_item.added", "response.output_item.created"):
            item = getattr(event, "item", None)
            if item is not None:
                state.current_item_id = getattr(item, "id", None)
            return

        if event_type in ("response.audio.delta", "response.output_audio.delta"):
            delta = getattr(event, "delta", "")
            if delta:
                state.delivered_audio.extend(base64.b64decode(delta))
            return

        if event_type in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = getattr(event, "delta", "")
            if delta:
                state.delivered_transcripts.append(delta)
            return

        if event_type == "response.done":
            response = getattr(event, "response", None)
            done_response_id = getattr(response, "id", None) if response is not None else None
            if state.last_response_id is not None and done_response_id != state.last_response_id:
                # Stale event from a cancelled response; drop without resolving.
                return
            state.is_responding = False
            state.completion.set_result(
                RealtimeTargetResult(
                    audio_bytes=bytes(state.delivered_audio),
                    transcripts=list(state.delivered_transcripts),
                )
            )
            return

        if event_type == "input_audio_buffer.speech_started" and state.is_responding:
            await self._cancel(state=state)
            state.is_responding = False
            state.completion.set_result(
                RealtimeTargetResult(
                    audio_bytes=bytes(state.delivered_audio),
                    transcripts=list(state.delivered_transcripts),
                    interrupted=True,
                )
            )
            return

        if event_type == "error":
            error = getattr(event, "error", None)
            message = getattr(error, "message", "unknown") if error is not None else "unknown"
            state.completion.set_exception(RuntimeError(f"Realtime API error: {message}"))
            return

    async def _cancel(self, *, state: RealtimeTurnState) -> None:
        """
        Truncate the in-flight response's conversation item to what was actually delivered.

        The server auto-cancels the response when it detects new speech, so we only need to
        trim the conversation history to match the audio we received.

        Marks ``state.interrupted = True`` even when the truncate call fails.
        Does not resolve ``state.completion``; the caller (``_route_event``) does that.

        Args:
            state (RealtimeTurnState): The turn whose response should be cancelled.
        """
        if state.current_item_id is not None:
            # PCM16 @ 24 kHz: 48 bytes per millisecond.
            audio_end_ms = len(state.delivered_audio) // 48
            try:
                await self._connection.conversation.item.truncate(
                    item_id=state.current_item_id,
                    content_index=0,
                    audio_end_ms=audio_end_ms,
                )
            except Exception as e:
                logger.warning(
                    f"conversation.item.truncate failed for item {state.current_item_id} "
                    f"(audio_end_ms={audio_end_ms}): {e}"
                )
        state.interrupted = True
