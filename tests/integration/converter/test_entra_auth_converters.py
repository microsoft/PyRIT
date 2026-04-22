# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os

import pytest

from pyrit.prompt_converter import (
    AzureSpeechAudioToTextConverter,
    AzureSpeechTextToAudioConverter,
)


@pytest.mark.asyncio
async def test_azure_speech_text_to_audio_converter_entra_auth(monkeypatch):
    """Test Azure Speech text-to-audio converter with Entra authentication."""
    # Ensure key auth is not used — this test validates the Entra auto-detection path
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)

    region = os.getenv("AZURE_SPEECH_REGION")
    resource_id = os.getenv("AZURE_SPEECH_RESOURCE_ID")

    # The Azure Speech resource should have Entra authentication enabled in the current context.
    # e.g. Cognitive Services Speech Contributor role
    converter = AzureSpeechTextToAudioConverter(azure_speech_region=region, azure_speech_resource_id=resource_id)

    # Test conversion with simple text
    test_text = "Hello, this is a test."

    result = await converter.convert_async(prompt=test_text, input_type="text")

    # Verify we got a response back (indicates auth worked)
    assert result is not None
    assert result.output_type == "audio_path"

    # Clean up generated audio file
    if result.output_text and os.path.exists(result.output_text):
        os.unlink(result.output_text)


@pytest.mark.asyncio
async def test_azure_speech_audio_to_text_converter_entra_auth(monkeypatch):
    """Test Azure Speech audio-to-text converter with Entra authentication."""
    import tempfile

    # Ensure key auth is not used — this test validates the Entra auto-detection path
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)

    region = os.getenv("AZURE_SPEECH_REGION")
    resource_id = os.getenv("AZURE_SPEECH_RESOURCE_ID")

    converter = AzureSpeechAudioToTextConverter(azure_speech_region=region, azure_speech_resource_id=resource_id)

    # Create a temporary audio file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        temp_file.write(b"test audio data")
        temp_audio_path = temp_file.name

    try:
        # Test conversion with temporary audio file
        result = await converter.convert_async(prompt=temp_audio_path, input_type="audio_path")

        # Verify we got a response back (indicates auth worked)
        assert result is not None
        assert result.output_type == "text"
    finally:
        # Clean up temporary file
        os.unlink(temp_audio_path)
