# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.prompt_converter import AzureSpeechAudioToTextConverter


def is_speechsdk_installed():
    try:
        import azure.cognitiveservices.speech  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
class TestAzureSpeechAudioToTextConverter:
    @patch(
        "pyrit.common.default_values.get_required_value", side_effect=lambda env_var_name, passed_value: passed_value
    )
    def test_azure_speech_audio_text_converter_initialization(self, mock_get_required_value):
        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="dummy_region", azure_speech_key="dummy_key", recognition_language="es-ES"
        )
        assert converter._recognition_language == "es-ES"
        assert converter._azure_speech_region == "dummy_region"
        assert converter._azure_speech_key == "dummy_key"
        assert converter.done is False

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    @patch("azure.cognitiveservices.speech.SpeechRecognizer")
    @patch("pyrit.prompt_converter.azure_speech_audio_to_text_converter.logger")
    def test_stop_cb(self, mock_logger, MockSpeechRecognizer, mock_get_required_value):  # noqa: N803
        import azure.cognitiveservices.speech as speechsdk

        # Create a mock event
        mock_event = MagicMock()
        mock_event.result.reason = speechsdk.ResultReason.Canceled
        mock_event.result.cancellation_details.reason = speechsdk.CancellationReason.EndOfStream
        mock_event.result.cancellation_details.error_details = "Mock error details"

        MockSpeechRecognizer.return_value = MagicMock()

        mock_logger.return_value = MagicMock()

        # Call the stop_cb function with the mock event
        converter = AzureSpeechAudioToTextConverter()
        converter.stop_cb(evt=mock_event, recognizer=MockSpeechRecognizer)

        # Check if the callback function worked as expected
        MockSpeechRecognizer.stop_continuous_recognition_async.assert_called_once()
        mock_logger.info.assert_any_call(f"CLOSING on {mock_event}")
        mock_logger.info.assert_any_call(f"Speech recognition canceled: {speechsdk.CancellationReason.EndOfStream}")
        mock_logger.info.assert_called_with("End of audio stream detected.")

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    @patch("azure.cognitiveservices.speech.SpeechRecognizer")
    @patch("pyrit.prompt_converter.azure_speech_audio_to_text_converter.logger")
    def test_transcript_cb(self, mock_logger, MockSpeechRecognizer, mock_get_required_value):  # noqa: N803
        import azure.cognitiveservices.speech as speechsdk

        # Create a mock event
        mock_event = MagicMock()
        mock_event.result.reason = speechsdk.ResultReason.RecognizedSpeech
        mock_event.result.text = "Mock transcribed text"

        MockSpeechRecognizer.return_value = MagicMock()

        mock_logger.return_value = MagicMock()

        # Call the transcript_cb function with the mock event
        converter = AzureSpeechAudioToTextConverter()
        transcript = ["sample", "transcript"]
        converter.transcript_cb(evt=mock_event, transcript=transcript)

        # Check if the callback function logged the recognition
        mock_logger.info.assert_any_call(f"RECOGNIZED: {mock_event.result.text}")
        assert mock_event.result.text in transcript

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_azure_speech_audio_text_converter_input_supported(self, mock_get_required_value):
        converter = AzureSpeechAudioToTextConverter()
        assert converter.input_supported("image_path") is False
        assert converter.input_supported("audio_path") is True

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    async def test_azure_speech_audio_text_converter_nonexistent_path(self, mock_get_required_value):
        converter = AzureSpeechAudioToTextConverter()
        prompt = "nonexistent_path2.wav"
        with pytest.raises(FileNotFoundError):
            assert await converter.convert_async(prompt=prompt, input_type="audio_path")

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    @patch("os.path.exists", return_value=True)
    async def test_azure_speech_audio_text_converter_non_wav_file(self, mock_path_exists, mock_get_required_value):
        converter = AzureSpeechAudioToTextConverter()
        prompt = "dummy_audio.mp3"
        with pytest.raises(ValueError):
            assert await converter.convert_async(prompt=prompt, input_type="audio_path")

    def test_use_entra_auth_emits_deprecation_warning(self):
        """Test that use_entra_auth emits DeprecationWarning."""
        with pytest.warns(DeprecationWarning, match="use_entra_auth.*deprecated"):
            AzureSpeechAudioToTextConverter(
                azure_speech_region="test_region",
                azure_speech_resource_id="test_resource_id",
                use_entra_auth=True,
            )

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_init_with_key_uses_key_auth(self, mock_get_required_value):
        converter = AzureSpeechAudioToTextConverter(azure_speech_region="test_region", azure_speech_key="test_key")
        assert converter._azure_speech_key == "test_key"
        assert converter._azure_speech_resource_id is None
        assert converter._token_provider is None

    @patch("pyrit.common.default_values.get_non_required_value", return_value="")
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_init_with_resource_id_auto_entra(self, mock_required, mock_non_required):
        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="test_region", azure_speech_resource_id="test_resource_id"
        )
        assert converter._azure_speech_key is None
        assert converter._azure_speech_resource_id == "test_resource_id"
        assert converter._token_provider is None

    @patch("pyrit.common.default_values.get_non_required_value", return_value="")
    @patch.dict("os.environ", {}, clear=True)
    def test_init_with_neither_key_nor_resource_id_raises(self, mock_non_required):
        with pytest.raises(ValueError):
            AzureSpeechAudioToTextConverter(azure_speech_region="test_region")

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_init_with_callable_key_stores_token_provider(self, mock_get_required_value):
        def my_provider():
            return "my_token"

        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="test_region",
            azure_speech_key=my_provider,
            azure_speech_resource_id="test_resource_id",
        )
        assert converter._token_provider is my_provider
        assert converter._azure_speech_key is None
        assert converter._azure_speech_resource_id == "test_resource_id"

    @patch("azure.cognitiveservices.speech.SpeechConfig")
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    async def test_get_speech_config_async_with_sync_token_provider(self, mock_get_required_value, MockSpeechConfig):  # noqa: N803
        from pyrit.auth.azure_auth import get_speech_config_async

        def my_provider():
            return "my_token"

        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="test_region",
            azure_speech_key=my_provider,
            azure_speech_resource_id="test_resource_id",
        )
        await get_speech_config_async(
            token_provider=converter._token_provider,
            resource_id=converter._azure_speech_resource_id,
            key=converter._azure_speech_key,
            region=converter._azure_speech_region,
        )
        MockSpeechConfig.assert_called_once_with(auth_token="aad#test_resource_id#my_token", region="test_region")

    @patch("azure.cognitiveservices.speech.SpeechConfig")
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    async def test_get_speech_config_async_with_async_token_provider(self, mock_get_required_value, MockSpeechConfig):  # noqa: N803
        from pyrit.auth.azure_auth import get_speech_config_async

        async def my_async_provider():
            return "my_async_token"

        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="test_region",
            azure_speech_key=my_async_provider,
            azure_speech_resource_id="test_resource_id",
        )
        await get_speech_config_async(
            token_provider=converter._token_provider,
            resource_id=converter._azure_speech_resource_id,
            key=converter._azure_speech_key,
            region=converter._azure_speech_region,
        )
        MockSpeechConfig.assert_called_once_with(auth_token="aad#test_resource_id#my_async_token", region="test_region")

    @patch("pyrit.common.default_values.get_non_required_value", return_value="")
    @patch.dict("os.environ", {}, clear=True)
    def test_init_with_callable_key_without_resource_id_raises(self, mock_non_required):
        def my_provider():
            return "my_token"

        with pytest.raises(ValueError, match="AZURE_SPEECH_RESOURCE_ID"):
            AzureSpeechAudioToTextConverter(azure_speech_region="test_region", azure_speech_key=my_provider)

    @patch(
        "pyrit.prompt_converter.azure_speech_audio_to_text_converter.get_speech_config_async",
        new_callable=AsyncMock,
    )
    @patch(
        "pyrit.prompt_converter.azure_speech_audio_to_text_converter.data_serializer_factory",
    )
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    async def test_convert_async_happy_path(self, mock_required, mock_factory, mock_get_config):
        """Test convert_async exercises the get_speech_config_async + _recognize_audio path."""
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = b"fake audio bytes"
        mock_factory.return_value = mock_serializer

        mock_speech_config = MagicMock()
        mock_get_config.return_value = mock_speech_config

        converter = AzureSpeechAudioToTextConverter(azure_speech_region="test_region", azure_speech_key="test_key")

        with patch.object(converter, "_recognize_audio", return_value="hello world") as mock_recognize:
            result = await converter.convert_async(prompt="test.wav", input_type="audio_path")

        assert result.output_text == "hello world"
        assert result.output_type == "text"
        mock_get_config.assert_called_once()
        mock_recognize.assert_called_once_with(audio_bytes=b"fake audio bytes", speech_config=mock_speech_config)

    @patch("pyrit.prompt_converter.azure_speech_audio_to_text_converter.get_speech_config")
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_recognize_audio_calls_get_speech_config(self, mock_required, mock_get_config):
        """Test that recognize_audio() calls get_speech_config and _recognize_audio."""
        mock_speech_config = MagicMock()
        mock_get_config.return_value = mock_speech_config

        converter = AzureSpeechAudioToTextConverter(azure_speech_region="test_region", azure_speech_key="test_key")

        with patch.object(converter, "_recognize_audio", return_value="transcribed") as mock_recognize:
            result = converter.recognize_audio(audio_bytes=b"fake audio")

        assert result == "transcribed"
        mock_get_config.assert_called_once_with(resource_id=None, key="test_key", region="test_region")
        mock_recognize.assert_called_once_with(audio_bytes=b"fake audio", speech_config=mock_speech_config)

    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    def test_recognize_audio_warns_when_token_provider_set(self, mock_required):
        """Test that recognize_audio() emits DeprecationWarning when _token_provider is set."""

        def my_provider():
            return "my_token"

        converter = AzureSpeechAudioToTextConverter(
            azure_speech_region="test_region",
            azure_speech_key=my_provider,
            azure_speech_resource_id="test_resource_id",
        )

        with (
            patch("pyrit.prompt_converter.azure_speech_audio_to_text_converter.get_speech_config") as mock_config,
            patch.object(converter, "_recognize_audio", return_value="text"),
            warnings.catch_warnings(record=True) as w,
        ):
            warnings.simplefilter("always")
            mock_config.return_value = MagicMock()
            converter.recognize_audio(audio_bytes=b"fake audio")

        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert any(
            "recognize_audio() does not support callable token providers" in str(x.message)
            for x in deprecation_warnings
        )
