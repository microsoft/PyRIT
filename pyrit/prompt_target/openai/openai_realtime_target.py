# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import logging
import re
import wave
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Tuple

from openai import AsyncOpenAI

from pyrit.exceptions import (
    pyrit_target_retry,
)
from pyrit.exceptions.exception_classes import ServerErrorException
from pyrit.models import (
    Message,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)

# Voices supported by the OpenAI Realtime API.
# See: https://platform.openai.com/docs/guides/realtime-conversations#voice-options
# For best quality, OpenAI recommends using "marin" or "cedar".
RealTimeVoice = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]


@dataclass
class RealtimeTargetResult:
    """
    Represents the result of a Realtime API request, containing audio data and transcripts.

    Attributes:
        audio_bytes: Raw audio data returned by the API
        transcripts: List of text transcripts generated from the audio
    """

    audio_bytes: bytes = field(default_factory=lambda: b"")
    transcripts: List[str] = field(default_factory=list)

    def flatten_transcripts(self) -> str:
        """
        Flattens the list of transcripts into a single string.

        Returns:
            A single string containing all transcripts concatenated together.
        """
        return "".join(self.transcripts)


class RealtimeTarget(OpenAITarget):
    """
    A prompt target for Azure OpenAI Realtime API.

    This class enables real-time audio communication with OpenAI models, supporting
    voice input and output with configurable voice options.

    Read more at https://learn.microsoft.com/en-us/azure/ai-services/openai/realtime-audio-reference
    and https://platform.openai.com/docs/guides/realtime-websocket
    """

    def __init__(
        self,
        *,
        voice: Optional[RealTimeVoice] = None,
        existing_convo: Optional[dict[str, Any]] = None,
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
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(**kwargs)

        self.voice = voice
        self._existing_conversation = existing_convo if existing_convo is not None else {}
        self._realtime_client: Optional[AsyncOpenAI] = None

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "OPENAI_REALTIME_MODEL"
        self.endpoint_environment_variable = "OPENAI_REALTIME_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_REALTIME_API_KEY"
        self.underlying_model_environment_variable = "OPENAI_REALTIME_UNDERLYING_MODEL"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/realtime", "/v1/realtime"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "wss://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "wss://api.openai.com/v1",
        }

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

        if self.voice:
            session_config["audio"]["output"]["voice"] = self.voice  # type: ignore[index]

        return session_config

    async def send_config(self, conversation_id: str) -> None:
        """
        Send the session configuration using OpenAI client.

        Args:
            conversation_id (str): Conversation ID
        """
        # Extract system prompt from conversation history
        system_prompt = self._get_system_prompt_from_conversation(conversation_id=conversation_id)
        config_variables = self._set_system_prompt_and_config_vars(system_prompt=system_prompt)

        connection = self._get_connection(conversation_id=conversation_id)
        await connection.session.update(session=config_variables)
        logger.info("Session configuration sent")

    def _get_system_prompt_from_conversation(self, *, conversation_id: str) -> str:
        """
        Retrieve the system prompt from conversation history.

        Args:
            conversation_id (str): The conversation ID

        Returns:
            str: The system prompt from conversation history, or a default if none found
        """
        conversation = self._memory.get_conversation(conversation_id=conversation_id)

        # Look for a system message at the beginning of the conversation
        if conversation and len(conversation) > 0:
            first_message = conversation[0]
            if first_message.message_pieces and first_message.message_pieces[0].api_role == "system":
                return first_message.message_pieces[0].converted_value

        # Return default system prompt if none found in conversation
        return "You are a helpful AI assistant"

    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message to the OpenAI realtime target.

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing the response from the prompt target.

        Raises:
            ValueError: If the message piece type is unsupported.
        """
        conversation_id = message.message_pieces[0].conversation_id
        if conversation_id not in self._existing_conversation:
            connection = await self.connect(conversation_id=conversation_id)
            self._existing_conversation[conversation_id] = connection

            # Only send config when creating a new connection
            await self.send_config(conversation_id=conversation_id)
            # Give the server a moment to process the session update
            await asyncio.sleep(0.5)

        self._validate_request(message=message)

        request = message.message_pieces[0]
        response_type = request.converted_value_data_type

        # Order of messages sent varies based on the data format of the prompt
        if response_type == "audio_path":
            output_audio_path, result = await self.send_audio_async(
                filename=request.converted_value, conversation_id=conversation_id
            )

        elif response_type == "text":
            output_audio_path, result = await self.send_text_async(
                text=request.converted_value, conversation_id=conversation_id
            )
        else:
            raise ValueError(f"Unsupported response type: {response_type}")

        text_response_piece = construct_response_from_request(
            request=request, response_text_pieces=[result.flatten_transcripts()], response_type="text"
        ).message_pieces[0]

        audio_response_piece = construct_response_from_request(
            request=request, response_text_pieces=[output_audio_path], response_type="audio_path"
        ).message_pieces[0]

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
        GRACE_PERIOD_SEC = 1.0  # Wait 1 second after audio.done before soft-finishing

        try:
            # Create event iterator
            event_iter = connection.__aiter__()

            while True:
                # If we've seen audio.done, wait with a short timeout for response.done
                # Otherwise, wait indefinitely for events
                timeout = GRACE_PERIOD_SEC if audio_done_received else None

                try:
                    event = await asyncio.wait_for(event_iter.__anext__(), timeout=timeout)
                except asyncio.TimeoutError:
                    # Soft-finish: audio.done was received but no response.done after grace period
                    if audio_done_received:
                        logger.warning(
                            f"Soft-finishing: No response.done {GRACE_PERIOD_SEC}s after audio.done. "
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
                logger.debug(f"Processing event type: {event_type}")

                if event_type == "response.done":
                    self._handle_response_done_event(event=event, result=result)
                    logger.debug("Received response.done - finishing normally")
                    break

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
                    logger.debug(f"Received audio.done - will soft-finish in {GRACE_PERIOD_SEC}s if no response.done")
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

    async def send_text_async(self, text: str, conversation_id: str) -> Tuple[str, RealtimeTargetResult]:
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

        # Close and recreate connection to avoid websockets library state issues with fragmented frames
        # This prevents "cannot reset() while queue isn't empty" errors in multi-turn conversations
        await self.cleanup_conversation(conversation_id=conversation_id)
        new_connection = await self.connect(conversation_id=conversation_id)
        self._existing_conversation[conversation_id] = new_connection

        # Send session configuration to new connection
        system_prompt = self._get_system_prompt_from_conversation(conversation_id=conversation_id)
        session_config = self._set_system_prompt_and_config_vars(system_prompt=system_prompt)
        await new_connection.session.update(session=session_config)

        # Azure GA uses 24000 Hz sample rate
        output_audio_path = await self.save_audio(audio_bytes=result.audio_bytes, sample_rate=24000)
        return output_audio_path, result

    async def send_audio_async(self, filename: str, conversation_id: str) -> Tuple[str, RealtimeTargetResult]:
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

        # Close and recreate connection to avoid websockets library state issues with fragmented frames
        # This prevents "cannot reset() while queue isn't empty" errors in multi-turn conversations
        await self.cleanup_conversation(conversation_id=conversation_id)
        new_connection = await self.connect(conversation_id=conversation_id)
        self._existing_conversation[conversation_id] = new_connection

        # Send session configuration to new connection
        system_prompt = self._get_system_prompt_from_conversation(conversation_id=conversation_id)
        session_config = self._set_system_prompt_and_config_vars(system_prompt=system_prompt)
        await new_connection.session.update(session=session_config)

        output_audio_path = await self.save_audio(result.audio_bytes, num_channels, sample_width, frame_rate)
        return output_audio_path, result

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Not used in RealtimeTarget - message construction handled by receive_events.
        This implementation exists to satisfy the abstract base class requirement.
        """
        raise NotImplementedError("RealtimeTarget uses receive_events for message construction")

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the structure and content of a message for compatibility of this target.

        Args:
            message (Message): The message object.

        Raises:
            ValueError: If more than two message pieces are provided.
            ValueError: If any of the message pieces have a data type other than 'text' or 'audio_path'.
        """
        # Check the number of message pieces
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports one message piece. Received: {n_pieces} pieces.")

        piece_type = message.message_pieces[0].converted_value_data_type
        if piece_type not in ["text", "audio_path"]:
            raise ValueError(f"This target only supports text and audio_path prompt input. Received: {piece_type}.")

    def is_json_response_supported(self) -> bool:
        """
        Check if the target supports JSON as a response format.

        Returns:
            bool: True if JSON response is supported, False otherwise.
        """
        return False
