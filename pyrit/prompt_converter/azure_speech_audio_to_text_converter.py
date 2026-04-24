# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import time
import warnings
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import azure.cognitiveservices.speech as speechsdk  # noqa: F401

from pyrit.auth.azure_auth import get_speech_config, get_speech_config_async
from pyrit.common import default_values
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AzureSpeechAudioToTextConverter(PromptConverter):
    """
    Transcribes a .wav audio file into text using Azure AI Speech service.

    Authentication is auto-detected from the provided credentials, in priority order:

    1. If ``azure_speech_key`` is a **callable** token provider, it takes highest priority — it is
       resolved at conversion time and used with Entra ID auth (``azure_speech_resource_id`` required).
    2. If ``azure_speech_key`` is a **string** (or the ``AZURE_SPEECH_KEY`` env var is set), API key auth is used.
    3. If **neither** is provided, Entra ID auth is used automatically via ``DefaultAzureCredential``
       and ``azure_speech_resource_id`` must be set.

    https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-to-text
    """

    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: The name of the Azure region.
    AZURE_SPEECH_REGION_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_REGION"
    #: The API key for accessing the service.
    AZURE_SPEECH_KEY_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_KEY"
    #: The resource ID for accessing the service when using Entra ID auth.
    AZURE_SPEECH_RESOURCE_ID_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_RESOURCE_ID"

    def __init__(
        self,
        *,
        azure_speech_region: Optional[str] = None,
        azure_speech_key: Optional[str | Callable[[], str | Awaitable[str]]] = None,
        azure_speech_resource_id: Optional[str] = None,
        use_entra_auth: Optional[bool] = None,
        recognition_language: str = "en-US",
    ) -> None:
        """
        Initialize the converter with Azure Speech service credentials and recognition language.

        Args:
            azure_speech_region (str, Optional): The name of the Azure region.
            azure_speech_key (str | Callable[[], str | Awaitable[str]], Optional): The API key for accessing
                the service, or a sync/async callable that returns a token string.
                If a string key is provided (or the ``AZURE_SPEECH_KEY`` env var is set), key auth is used.
                If a callable token provider is provided, it is resolved at conversion time and used with
                Entra ID auth (``azure_speech_resource_id`` must also be set).
                If omitted, Entra ID auth via ``DefaultAzureCredential`` is used automatically.
            azure_speech_resource_id (str, Optional): The resource ID for accessing the service when using
                Entra ID auth. Required when using a callable token provider or when no API key is available.
            use_entra_auth (bool, Optional): **Deprecated.** Will be removed in v0.15.0.
                Authentication is now auto-detected from the provided credentials.
            recognition_language (str): Recognition voice language. Defaults to "en-US".
                For more on supported languages, see the following link:
                https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support

        Raises:
            ValueError: If the required environment variables or parameters are not set.
        """
        if use_entra_auth is not None:
            warnings.warn(
                "'use_entra_auth' is deprecated and will be removed in v0.15.0. "
                "Authentication is now auto-detected: pass a key string for key auth, "
                "a callable token provider for token auth, or omit for automatic Entra ID auth.",
                DeprecationWarning,
                stacklevel=2,
            )

        self._azure_speech_region: str = default_values.get_required_value(
            env_var_name=self.AZURE_SPEECH_REGION_ENVIRONMENT_VARIABLE,
            passed_value=azure_speech_region,
        )

        self._token_provider: Callable[[], str | Awaitable[str]] | None = None
        self._azure_speech_key: str | None = None
        self._azure_speech_resource_id: str | None = None

        if azure_speech_key is not None and callable(azure_speech_key):
            self._token_provider = azure_speech_key
            self._azure_speech_resource_id = default_values.get_required_value(
                env_var_name=self.AZURE_SPEECH_RESOURCE_ID_ENVIRONMENT_VARIABLE,
                passed_value=azure_speech_resource_id,
            )
        else:
            key_value = default_values.get_non_required_value(
                env_var_name=self.AZURE_SPEECH_KEY_ENVIRONMENT_VARIABLE,
                passed_value=azure_speech_key,
            )
            if key_value:
                self._azure_speech_key = key_value
            else:
                logger.info(
                    "No azure_speech_key provided. "
                    "Entra ID authentication will be attempted via DefaultAzureCredential."
                )
                self._azure_speech_resource_id = default_values.get_required_value(
                    env_var_name=self.AZURE_SPEECH_RESOURCE_ID_ENVIRONMENT_VARIABLE,
                    passed_value=azure_speech_resource_id,
                )

        self._recognition_language = recognition_language
        # Create a flag to indicate when recognition is finished
        self.done = False

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with speech recognition parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "recognition_language": self._recognition_language,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        """
        Convert the given audio file into its text representation.

        Args:
            prompt (str): File path to the audio file to be transcribed.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the transcribed text.

        Raises:
            ValueError: If the input type is not supported or if the provided file is not a .wav file.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if not prompt.endswith(".wav"):
            raise ValueError("Please provide a .wav audio file. Compressed formats are not currently supported.")

        audio_serializer = data_serializer_factory(
            category="prompt-memory-entries", data_type="audio_path", value=prompt
        )
        audio_bytes = await audio_serializer.read_data()

        try:
            speech_config = await get_speech_config_async(
                token_provider=self._token_provider,
                resource_id=self._azure_speech_resource_id,
                key=self._azure_speech_key,
                region=self._azure_speech_region,
            )
            transcript = self._recognize_audio(audio_bytes=audio_bytes, speech_config=speech_config)
        except Exception as e:
            logger.error("Failed to convert audio file to text: %s", str(e))
            raise
        return ConverterResult(output_text=transcript, output_type="text")

    def recognize_audio(self, audio_bytes: bytes) -> str:
        """
        Recognize audio file and return transcribed text.

        .. deprecated::
            Use :meth:`convert_async` instead, which resolves token providers correctly.
            This method does not support callable token providers.

        Args:
            audio_bytes (bytes): Audio bytes input.

        Returns:
            str: Transcribed text.

        Raises:
            ModuleNotFoundError: If the azure.cognitiveservices.speech module is not installed.
        """
        if self._token_provider:
            warnings.warn(
                "recognize_audio() does not support callable token providers. "
                "Use convert_async() instead, which correctly resolves token providers.",
                DeprecationWarning,
                stacklevel=2,
            )
        speech_config = get_speech_config(
            resource_id=self._azure_speech_resource_id,
            key=self._azure_speech_key,
            region=self._azure_speech_region,
        )
        return self._recognize_audio(audio_bytes=audio_bytes, speech_config=speech_config)

    def _recognize_audio(self, *, audio_bytes: bytes, speech_config: "speechsdk.SpeechConfig") -> str:
        """
        Recognize audio from bytes using the given speech config.

        Args:
            audio_bytes (bytes): Audio bytes input.
            speech_config (speechsdk.SpeechConfig): Pre-built speech configuration.

        Returns:
            str: Transcribed text.

        Raises:
            ModuleNotFoundError: If the azure.cognitiveservices.speech module is not installed.
        """
        try:
            import azure.cognitiveservices.speech as speechsdk  # noqa: F811
        except ModuleNotFoundError as e:
            logger.error(
                "Could not import azure.cognitiveservices.speech. "
                "You may need to install it via 'pip install pyrit[speech]'"
            )
            raise e

        speech_config.speech_recognition_language = self._recognition_language

        push_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

        speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        transcribed_text: list[str] = []
        self.done = False

        speech_recognizer.recognized.connect(lambda evt: self.transcript_cb(evt, transcript=transcribed_text))
        speech_recognizer.recognizing.connect(lambda evt: logger.info(f"RECOGNIZING: {evt}"))
        speech_recognizer.recognized.connect(lambda evt: logger.info(f"RECOGNIZED: {evt}"))
        speech_recognizer.session_started.connect(lambda evt: logger.info(f"SESSION STARTED: {evt}"))
        speech_recognizer.session_stopped.connect(lambda evt: logger.info(f"SESSION STOPPED: {evt}"))
        speech_recognizer.canceled.connect(lambda evt: self.stop_cb(evt, recognizer=speech_recognizer))
        speech_recognizer.session_stopped.connect(lambda evt: self.stop_cb(evt, recognizer=speech_recognizer))

        speech_recognizer.start_continuous_recognition_async()

        push_stream.write(audio_bytes)
        push_stream.close()

        while not self.done:
            time.sleep(0.5)

        return "".join(transcribed_text)

    def transcript_cb(self, evt: Any, transcript: list[str]) -> None:
        """
        Append transcribed text upon receiving a "recognized" event.

        Args:
            evt (speechsdk.SpeechRecognitionEventArgs): Event.
            transcript (list): List to store transcribed text.
        """
        logger.info(f"RECOGNIZED: {evt.result.text}")
        transcript.append(evt.result.text)

    def stop_cb(self, evt: Any, recognizer: Any) -> None:
        """
        Stop continuous recognition upon receiving an event 'evt'.

        Args:
            evt (speechsdk.SpeechRecognitionEventArgs): Event.
            recognizer (speechsdk.SpeechRecognizer): Speech recognizer object.

        Raises:
            ModuleNotFoundError: If the azure.cognitiveservices.speech module is not installed.
        """
        try:
            import azure.cognitiveservices.speech as speechsdk  # noqa: F811
        except ModuleNotFoundError as e:
            logger.error(
                "Could not import azure.cognitiveservices.speech. "
                "You may need to install it via 'pip install pyrit[speech]'"
            )
            raise e

        logger.info(f"CLOSING on {evt}")
        recognizer.stop_continuous_recognition_async()
        self.done = True
        if evt.result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = evt.result.cancellation_details
            logger.info(f"Speech recognition canceled: {cancellation_details.reason}")
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                logger.error(f"Error details: {cancellation_details.error_details}")
            elif cancellation_details.reason == speechsdk.CancellationReason.EndOfStream:
                logger.info("End of audio stream detected.")
