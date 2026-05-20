# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import logging
import re
import wave
from collections.abc import Callable, Coroutine
from typing import Any, Literal, Optional

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
    RealtimeTargetResult,
    ServerVadConfig,
    _CommittedEvent,
    _RealtimeEventDispatcher,
    _RealtimeTurnState,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)

# Voices supported by the OpenAI Realtime API.
# See: https://platform.openai.com/docs/guides/realtime-conversations#voice-options
# For best quality, OpenAI recommends using "marin" or "cedar".
RealTimeVoice = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]


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

    def __init__(
        self,
        *,
        voice: Optional[RealTimeVoice] = None,
        existing_convo: Optional[dict[str, Any]] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
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
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
            server_vad (bool | ServerVadConfig): Server-side voice activity detection (VAD).
                ``False`` (default) keeps the existing atomic send/receive behavior.
                ``True`` enables VAD with default tuning.
                Pass a ``ServerVadConfig`` to enable with custom tuning. Streaming/interruption plumbing
                arrives in subsequent changes; this currently only affects the emitted session config.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(custom_configuration=custom_configuration, custom_capabilities=custom_capabilities, **kwargs)

        self.voice = voice
        self._existing_conversation = existing_convo if existing_convo is not None else {}
        self._realtime_client: Optional[AsyncOpenAI] = None

        if isinstance(server_vad, ServerVadConfig):
            self._server_vad: Optional[ServerVadConfig] = server_vad
        elif server_vad:
            self._server_vad = ServerVadConfig()
        else:
            self._server_vad = None

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

    async def connect(self, conversation_id: str) -> Any:
        """
        Connect to Realtime API using AsyncOpenAI client and return the realtime connection.

        Returns:
            The Realtime API connection.
        """
        logger.info(f"Connecting to Realtime API: {self._endpoint}")

        client = self._get_openai_client()
        connection = await client.realtime.connect(model=self._model_name).__aenter__()

        logger.info("Successfully connected to AzureOpenAI Realtime API")
        return connection

    def _set_system_prompt_and_config_vars(self, system_prompt: str) -> dict[str, Any]:
        """
        Create session configuration for OpenAI client.
        Uses the Azure GA format with nested audio config.

        Args:
            system_prompt: The system prompt to use in the session configuration.

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
                        "rate": 24000,
                    },
                },
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": 24000,
                    }
                },
            },
        }

        if self._server_vad is not None:
            session_config["audio"]["input"]["turn_detection"] = {  # type: ignore[ty:invalid-assignment]
                "type": "server_vad",
                "threshold": self._server_vad.threshold,
                "prefix_padding_ms": self._server_vad.prefix_padding_ms,
                "silence_duration_ms": self._server_vad.silence_duration_ms,
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
        if conversation_id not in self._existing_conversation:
            connection = await self.connect(conversation_id=conversation_id)
            self._existing_conversation[conversation_id] = connection

            # Only send config when creating a new connection
            await self.send_config(conversation_id=conversation_id, conversation=normalized_conversation)
            # Give the server a moment to process the session update
            await asyncio.sleep(0.5)

        request = message.message_pieces[0]
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

    async def cleanup_target(self) -> None:
        """
        Disconnects from the Realtime API connections.
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

    async def cleanup_conversation(self, conversation_id: str) -> None:
        """
        Disconnects from the Realtime API for a specific conversation.

        Args:
            conversation_id (str): The conversation ID to disconnect from.

        """
        connection = self._existing_conversation.get(conversation_id)
        if connection:
            try:
                await connection.close()
                logger.info(f"Disconnected from {self._endpoint} with conversation ID: {conversation_id}")
            except Exception as e:
                logger.warning(f"Error closing connection for {conversation_id}: {e}")
            del self._existing_conversation[conversation_id]

    async def send_response_create(self, conversation_id: str) -> None:
        """
        Send response.create using OpenAI client.

        Args:
            conversation_id (str): Conversation ID
        """
        connection = self._get_connection(conversation_id=conversation_id)
        await connection.response.create()

    async def push_audio_chunk_async(self, *, connection: Any, pcm_bytes: bytes) -> None:
        """
        Append a single PCM16 mono @ 24 kHz audio chunk to the server's input buffer.

        Used by streaming-style callers (e.g. ``BargeInAttack``) that source chunks
        from an iterator and want to control commit timing externally. Server VAD,
        when enabled on the session, decides when to commit and fire response logic.
        Empty buffers are accepted as no-ops.

        Args:
            connection: Active Realtime API connection from ``self.connect()``.
            pcm_bytes: Raw PCM16 mono audio for this chunk.
        """
        if not pcm_bytes:
            return
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await connection.input_audio_buffer.append(audio=audio_b64)

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

    async def insert_user_text_async(self, *, connection: Any, text: str) -> None:
        """
        Insert a user message containing the given text into the conversation.

        Lets streaming attacks mix text turns into an otherwise audio-driven session.
        The caller is responsible for triggering ``response.create`` after insertion.

        Args:
            connection: Active Realtime API connection.
            text: User-side text content.
        """
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
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

    async def subscribe_events_async(
        self,
        *,
        connection: Any,
        on_user_audio_committed: (Callable[[_CommittedEvent], Coroutine[Any, Any, None]] | None) = None,
    ) -> _RealtimeEventDispatcher:
        """
        Start consuming events from the connection and route them via the OpenAI dispatcher.

        Streaming-style callers (``BargeInAttack``) use this to receive normalized
        events (``user_audio_committed``). The returned dispatcher exposes
        ``stop()`` to tear down the background task and drain in-flight callback
        tasks, and a ``failure`` property that callers can poll between operations
        to detect a dead dispatch loop (e.g. websocket closed). Callers should
        call ``stop()`` before closing the connection.

        Args:
            connection: Active Realtime API connection from ``self.connect()``.
            on_user_audio_committed: Async callback fired when server VAD finalizes
                a user audio buffer. Called as a background task.

        Returns:
            The started dispatcher. Pass it to ``request_response_async`` for turn
            futures, poll ``failure`` for dispatch-loop errors, and call ``stop()``
            to tear it down.
        """
        dispatcher = _OpenAIRealtimeDispatcher(
            connection=connection,
            on_user_audio_committed=on_user_audio_committed,
        )
        await dispatcher.start()
        return dispatcher

    async def request_response_async(
        self,
        *,
        connection: Any,
        dispatcher: _RealtimeEventDispatcher,
    ) -> asyncio.Future[RealtimeTargetResult]:
        """
        Trigger ``response.create`` and return a future that resolves when the turn ends.

        Constructs a fresh ``_RealtimeTurnState``, binds it to the dispatcher as the
        active turn, then sends ``response.create``. The dispatcher resolves the
        returned future via ``response.done`` (with ``interrupted=False``) or via
        the barge-in cancel path (with ``interrupted=True``).

        Args:
            connection: Active Realtime API connection.
            dispatcher: Subscription handle previously returned by
                ``subscribe_events_async``. Must not have another turn pending.

        Returns:
            Future resolved with the assembled ``RealtimeTargetResult`` when this
            turn ends (normally or via barge-in).

        Raises:
            RuntimeError: If another turn is already pending on the dispatcher.
        """
        state = _RealtimeTurnState(completion=asyncio.get_running_loop().create_future())
        dispatcher.register_turn(state)
        await connection.response.create()
        return state.completion

    async def send_streaming_session_config_async(self, *, connection: Any, system_prompt: str) -> None:
        """
        Configure the realtime session for streaming use: server VAD with manual response creation.

        Emits the same session config as the atomic path except ``turn_detection.create_response``
        is forced to False so the streaming attack can swap the raw user audio item for converted
        audio before triggering ``response.create``.

        Args:
            connection: Active Realtime API connection.
            system_prompt: System prompt for the realtime session.

        Raises:
            ValueError: If the target was constructed without server VAD.
        """
        if self._server_vad is None:
            raise ValueError(
                "send_streaming_session_config_async requires server VAD; "
                "construct RealtimeTarget(server_vad=True) or pass a ServerVadConfig."
            )
        config = self._set_system_prompt_and_config_vars(system_prompt=system_prompt)
        turn_detection = config.get("audio", {}).get("input", {}).get("turn_detection")
        if turn_detection is not None:
            turn_detection["create_response"] = False
        await connection.session.update(session=config)

    async def _stream_pcm_async(
        self,
        *,
        connection: Any,
        pcm_bytes: bytes,
        commit: bool,
        chunk_ms: int = 100,
        sample_rate: int = 24000,
    ) -> None:
        """
        Stream raw PCM16 audio to the Realtime API as ``input_audio_buffer.append`` chunks.

        Operates on raw PCM bytes (not WAV) so this helper can back both the
        WAV-file path and future per-frame streaming consumers (e.g. browser audio
        forwarded by a GUI backend). Caller decides whether to manually commit;
        server VAD commits automatically when enabled.

        Args:
            connection: Active Realtime API connection from ``self.connect()``.
            pcm_bytes (bytes): Raw PCM16 mono audio. Empty buffers are accepted
                and result in zero appends.
            commit (bool): When True, sends ``input_audio_buffer.commit`` after the
                final chunk. Pass False when server VAD is committing automatically.
            chunk_ms (int): Milliseconds of audio per chunk. Defaults to 100.
            sample_rate (int): PCM sample rate in Hz. Defaults to 24000.
        """
        bytes_per_sample = 2  # PCM16
        chunk_size = (chunk_ms * sample_rate * bytes_per_sample) // 1000

        for offset in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[offset : offset + chunk_size]
            audio_b64 = base64.b64encode(chunk).decode("ascii")
            await connection.input_audio_buffer.append(audio=audio_b64)

        if commit:
            await connection.input_audio_buffer.commit()

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
        output_audio_path = await self.save_audio(audio_bytes=result.audio_bytes, sample_rate=24000)
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

        output_audio_path = await self.save_audio(result.audio_bytes, num_channels, sample_width, frame_rate)
        return output_audio_path, result

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Not used in RealtimeTarget - message construction handled by receive_events.
        This implementation exists to satisfy the abstract base class requirement.
        """
        raise NotImplementedError("RealtimeTarget uses receive_events for message construction")


class _OpenAIRealtimeDispatcher(_RealtimeEventDispatcher):
    """
    Concrete ``_RealtimeEventDispatcher`` for the OpenAI Realtime API.

    Routes OpenAI server events into the active ``_RealtimeTurnState`` and issues
    ``response.cancel`` plus ``conversation.item.truncate`` when interrupted.
    """

    async def _route_event(self, *, event: Any, state: _RealtimeTurnState | None) -> None:
        """Route an OpenAI Realtime event to the active turn or to an input-side callback."""
        event_type = getattr(event, "type", "")

        # Input-side events fire callbacks regardless of whether a turn is registered.
        if event_type == "input_audio_buffer.committed":
            item_id = getattr(event, "item_id", None)
            if item_id is None:
                return
            self._fire_committed_callback(
                _CommittedEvent(
                    item_id=item_id,
                    audio_start_ms=getattr(event, "audio_start_ms", None),
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

    async def _cancel(self, *, state: _RealtimeTurnState) -> None:
        """
        Send ``response.cancel`` + ``conversation.item.truncate`` for the in-flight response.

        Marks ``state.interrupted = True`` even when either wire call fails.
        Does not resolve ``state.completion``; the caller (``_route_event``) does that.

        Args:
            state (_RealtimeTurnState): The turn whose response should be cancelled.
        """
        if state.last_response_id is not None:
            try:
                await self._connection.response.cancel(response_id=state.last_response_id)
            except Exception as e:
                logger.debug(f"response.cancel raised for {state.last_response_id} (likely cancelled server-side): {e}")
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
