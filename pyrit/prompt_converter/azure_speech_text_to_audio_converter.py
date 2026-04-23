# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import warnings
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    import azure.cognitiveservices.speech as speechsdk  # noqa: F401

from pyrit.auth.azure_auth import get_speech_config_async
from pyrit.common import default_values
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AzureSpeechTextToAudioConverter(PromptConverter):
    """
    Generates a wave file from a text prompt using Azure AI Speech service.

    Authentication is auto-detected from the provided credentials, in priority order:

    1. If ``azure_speech_key`` is a **callable** token provider, it takes highest priority — it is
       resolved at conversion time and used with Entra ID auth (``azure_speech_resource_id`` required).
    2. If ``azure_speech_key`` is a **string** (or the ``AZURE_SPEECH_KEY`` env var is set), API key auth is used.
    3. If **neither** is provided, Entra ID auth is used automatically via ``DefaultAzureCredential``
       and ``azure_speech_resource_id`` must be set.

    https://learn.microsoft.com/en-us/azure/ai-services/speech-service/text-to-speech
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("audio_path",)

    #: The name of the Azure region.
    AZURE_SPEECH_REGION_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_REGION"
    #: The API key for accessing the service.
    AZURE_SPEECH_KEY_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_KEY"
    #: The resource ID for accessing the service when using Entra ID auth.
    AZURE_SPEECH_RESOURCE_ID_ENVIRONMENT_VARIABLE: str = "AZURE_SPEECH_RESOURCE_ID"

    #: Supported audio formats for output.
    AzureSpeechAudioFormat = Literal["wav", "mp3"]

    def __init__(
        self,
        *,
        azure_speech_region: Optional[str] = None,
        azure_speech_key: Optional[str | Callable[[], str | Awaitable[str]]] = None,
        azure_speech_resource_id: Optional[str] = None,
        use_entra_auth: Optional[bool] = None,
        synthesis_language: str = "en_US",
        synthesis_voice_name: str = "en-US-AvaNeural",
        output_format: AzureSpeechAudioFormat = "wav",
    ) -> None:
        """
        Initialize the converter with Azure Speech service credentials, synthesis language, and voice name.

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
            synthesis_language (str): Synthesis voice language.
            synthesis_voice_name (str): Synthesis voice name.
                For more details see the following link for synthesis language and synthesis voice:
                https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
            output_format (str): Either wav or mp3. Must match the file prefix.

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

        self._synthesis_language = synthesis_language
        self._synthesis_voice_name = synthesis_voice_name
        self._output_format = output_format

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with speech synthesis parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "synthesis_language": self._synthesis_language,
                "synthesis_voice_name": self._synthesis_voice_name,
                "output_format": self._output_format,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given text prompt into its audio representation.

        Args:
            prompt (str): The text prompt to be converted into audio.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the audio file path.

        Raises:
            ModuleNotFoundError: If the ``azure.cognitiveservices.speech`` module is not installed.
            RuntimeError: If there is an error during the speech synthesis process.
            ValueError: If the input type is not supported or if the prompt is empty.
        """
        try:
            import azure.cognitiveservices.speech as speechsdk  # noqa: F811
        except ModuleNotFoundError as e:
            logger.error(
                "Could not import azure.cognitiveservices.speech. "
                "You may need to install it via 'pip install pyrit[speech]'"
            )
            raise e

        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if prompt.strip() == "":
            raise ValueError("Prompt was empty. Please provide valid input prompt.")

        audio_serializer = data_serializer_factory(
            category="prompt-memory-entries", data_type="audio_path", extension=self._output_format
        )

        audio_serializer_file = None
        try:
            speech_config = await get_speech_config_async(
                token_provider=self._token_provider,
                resource_id=self._azure_speech_resource_id,
                key=self._azure_speech_key,
                region=self._azure_speech_region,
            )
            pull_stream = speechsdk.audio.PullAudioOutputStream()
            audio_cfg = speechsdk.audio.AudioOutputConfig(stream=pull_stream)
            speech_config.speech_synthesis_language = self._synthesis_language
            speech_config.speech_synthesis_voice_name = self._synthesis_voice_name

            if self._output_format == "mp3":
                speech_config.set_speech_synthesis_output_format(
                    speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
                )

            speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_cfg)

            result = speech_synthesizer.speak_text_async(prompt).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_data = result.audio_data
                await audio_serializer.save_data(audio_data)
                audio_serializer_file = str(audio_serializer.value)
                logger.info(
                    f"Speech synthesized for text [{prompt}], and the audio was saved to [{audio_serializer_file}]"
                )
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                logger.info(f"Speech synthesis canceled: {cancellation_details.reason}")
                if cancellation_details.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"Error details: {cancellation_details.error_details}")
                raise RuntimeError(
                    f"Speech synthesis canceled: {cancellation_details.reason}. "
                    f"Error details: {cancellation_details.error_details}"
                )
        except Exception as e:
            logger.error("Failed to convert prompt to audio: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file or "", output_type="audio_path")
