# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import azure.cognitiveservices.speech as speechsdk  # noqa: F401

from pyrit.auth.azure_auth import get_speech_config
from pyrit.common import default_values
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AzureSpeechAudioToTextConverter(PromptConverter):
    """
    Transcribes a .wav audio file into text using Azure AI Speech service.

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
        azure_speech_region: Optional[str] = None,
        azure_speech_key: Optional[str] = None,
        azure_speech_resource_id: Optional[str] = None,
        use_entra_auth: bool = False,
        recognition_language: str = "en-US",
    ) -> None:
        """
        Initialize the converter with Azure Speech service credentials and recognition language.

        Args:
            azure_speech_region (str, Optional): The name of the Azure region.
            azure_speech_key (str, Optional): The API key for accessing the service (if not using Entra ID auth).
            azure_speech_resource_id (str, Optional): The resource ID for accessing the service when using
                Entra ID auth. This can be found by selecting 'Properties' in the 'Resource Management'
                section of your Azure Speech resource in the Azure portal.
            use_entra_auth (bool): Whether to use Entra ID authentication. If True, azure_speech_resource_id
                must be provided. If False, azure_speech_key must be provided. Defaults to False.
            recognition_language (str): Recognition voice language. Defaults to "en-US".
                For more on supported languages, see the following link:
                https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support

        Raises:
            ValueError: If the required environment variables are not set, if azure_speech_key is passed in
                when use_entra_auth is True, or if azure_speech_resource_id is passed in when use_entra_auth
                is False.
        """
        self._azure_speech_region: str = default_values.get_required_value(
            env_var_name=self.AZURE_SPEECH_REGION_ENVIRONMENT_VARIABLE,
            passed_value=azure_speech_region,
        )
        if use_entra_auth:
            if azure_speech_key:
                raise ValueError("If using Entra ID auth, please do not specify azure_speech_key.")
            self._azure_speech_resource_id = default_values.get_required_value(
                env_var_name=self.AZURE_SPEECH_RESOURCE_ID_ENVIRONMENT_VARIABLE,
                passed_value=azure_speech_resource_id,
            )
            self._azure_speech_key = None
        else:
            if azure_speech_resource_id:
                raise ValueError("If using key auth, please do not specify azure_speech_resource_id.")
            self._azure_speech_key = default_values.get_required_value(
                env_var_name=self.AZURE_SPEECH_KEY_ENVIRONMENT_VARIABLE,
                passed_value=azure_speech_key,
            )
            self._azure_speech_resource_id = None

        self._recognition_language = recognition_language
        # Create a flag to indicate when recognition is finished
        self.done = False

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
            transcript = self.recognize_audio(audio_bytes)
        except Exception as e:
            logger.error("Failed to convert audio file to text: %s", str(e))
            raise
        return ConverterResult(output_text=transcript, output_type="text")

    def recognize_audio(self, audio_bytes: bytes) -> str:
        """
        Recognize audio file and return transcribed text.

        Args:
            audio_bytes (bytes): Audio bytes input.

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
                + "You may need to install it via 'pip install pyrit[speech]'"
            )
            raise e

        speech_config = get_speech_config(
            resource_id=self._azure_speech_resource_id, key=self._azure_speech_key, region=self._azure_speech_region
        )
        speech_config.speech_recognition_language = self._recognition_language

        # Create a PullAudioInputStream from the byte stream
        push_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

        # Instantiate a speech recognizer object
        speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        # Create an empty list to store recognized text
        transcribed_text: list[str] = []
        # Flag is set to False to indicate that recognition is not yet finished
        self.done = False

        # Connect callbacks to the events fired by the speech recognizer
        speech_recognizer.recognized.connect(lambda evt: self.transcript_cb(evt, transcript=transcribed_text))
        speech_recognizer.recognizing.connect(lambda evt: logger.info("RECOGNIZING: {}".format(evt)))
        speech_recognizer.recognized.connect(lambda evt: logger.info("RECOGNIZED: {}".format(evt)))
        speech_recognizer.session_started.connect(lambda evt: logger.info("SESSION STARTED: {}".format(evt)))
        speech_recognizer.session_stopped.connect(lambda evt: logger.info("SESSION STOPPED: {}".format(evt)))
        # Stop continuous recognition when stopped or canceled event is fired
        speech_recognizer.canceled.connect(lambda evt: self.stop_cb(evt, recognizer=speech_recognizer))
        speech_recognizer.session_stopped.connect(lambda evt: self.stop_cb(evt, recognizer=speech_recognizer))

        # Start continuous recognition
        speech_recognizer.start_continuous_recognition_async()

        # Push the entire audio data into the stream
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
        logger.info("RECOGNIZED: {}".format(evt.result.text))
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
                + "You may need to install it via 'pip install pyrit[speech]'"
            )
            raise e

        logger.info("CLOSING on {}".format(evt))
        recognizer.stop_continuous_recognition_async()
        self.done = True
        if evt.result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = evt.result.cancellation_details
            logger.info("Speech recognition canceled: {}".format(cancellation_details.reason))
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                logger.error("Error details: {}".format(cancellation_details.error_details))
            elif cancellation_details.reason == speechsdk.CancellationReason.EndOfStream:
                logger.info("End of audio stream detected.")
