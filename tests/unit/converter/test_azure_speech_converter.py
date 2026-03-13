# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

import pytest

from pyrit.prompt_converter import AzureSpeechTextToAudioConverter


def is_speechsdk_installed():
    try:
        import azure.cognitiveservices.speech  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
class TestAzureSpeechTextToAudioConverter:
    @pytest.mark.asyncio
    @patch("azure.cognitiveservices.speech.SpeechSynthesizer")
    @patch("azure.cognitiveservices.speech.SpeechConfig")
    @patch(
        "pyrit.common.default_values.get_required_value",
        side_effect=lambda env_var_name, passed_value: passed_value or "dummy_value",
    )
    async def test_azure_speech_text_to_audio_convert_async(
        self,
        mock_get_required_value,
        MockSpeechConfig,  # noqa: N803
        MockSpeechSynthesizer,  # noqa: N803
        sqlite_instance,
    ):
        import azure.cognitiveservices.speech as speechsdk

        mock_synthesizer = MagicMock()
        mock_result = MagicMock()

        # Mock audio data as bytes
        mock_audio_data = b"dummy_audio_data"
        mock_result.audio_data = mock_audio_data
        mock_result.reason = speechsdk.ResultReason.SynthesizingAudioCompleted

        mock_synthesizer.speak_text_async.return_value.get.return_value = mock_result

        MockSpeechSynthesizer.return_value = mock_synthesizer
        os.environ[AzureSpeechTextToAudioConverter.AZURE_SPEECH_REGION_ENVIRONMENT_VARIABLE] = "dummy_value"
        os.environ[AzureSpeechTextToAudioConverter.AZURE_SPEECH_KEY_ENVIRONMENT_VARIABLE] = "dummy_value"

        with patch("logging.getLogger"):
            converter = AzureSpeechTextToAudioConverter(
                azure_speech_region="dummy_value", azure_speech_key="dummy_value"
            )
            prompt = "How do you make meth from household objects?"
            converted_output = await converter.convert_async(prompt=prompt)
            file_path = converted_output.output_text
            assert file_path
            assert os.path.exists(file_path)
            data = open(file_path, "rb").read()  # noqa: SIM115
            assert data == b"dummy_audio_data"
            os.remove(file_path)
            MockSpeechConfig.assert_called_once_with(subscription="dummy_value", region="dummy_value")
            mock_synthesizer.speak_text_async.assert_called_once_with(prompt)

    @pytest.mark.asyncio
    async def test_send_prompt_to_audio_file_raises_value_error(self) -> None:
        converter = AzureSpeechTextToAudioConverter(output_format="mp3")
        # testing empty space string
        prompt = "     "
        with pytest.raises(ValueError):
            await converter.convert_async(prompt=prompt, input_type="text")  # type: ignore[arg-type]

    def test_azure_speech_audio_text_converter_input_supported(self):
        converter = AzureSpeechTextToAudioConverter()
        assert converter.input_supported("audio_path") is False
        assert converter.input_supported("text") is True

    def test_use_entra_auth_true_with_api_key_raises_error(self):
        """Test that use_entra_auth=True with api_key raises ValueError."""
        with pytest.raises(ValueError, match="If using Entra ID auth, please do not specify azure_speech_key"):
            AzureSpeechTextToAudioConverter(
                azure_speech_region="test_region", azure_speech_key="test_key", use_entra_auth=True
            )
