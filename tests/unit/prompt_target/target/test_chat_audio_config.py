# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for OpenAIChatAudioConfig.

Tests cover initialization, validation, and conversion to API parameters.
"""

import pytest

from pyrit.prompt_target import OpenAIChatAudioConfig


class TestOpenAIChatAudioConfigInit:
    """Tests for OpenAIChatAudioConfig initialization."""

    def test_init_with_required_params_only(self):
        """Test initialization with only required parameters."""
        config = OpenAIChatAudioConfig(voice="alloy")

        assert config.voice == "alloy"
        assert config.audio_format == "wav"  # Default value
        assert config.prefer_transcript_for_history is True  # Default value

    def test_init_with_all_params(self):
        """Test initialization with all parameters specified."""
        config = OpenAIChatAudioConfig(
            voice="coral",
            audio_format="mp3",
            prefer_transcript_for_history=False,
        )

        assert config.voice == "coral"
        assert config.audio_format == "mp3"
        assert config.prefer_transcript_for_history is False

    @pytest.mark.parametrize(
        "voice",
        ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"],
    )
    def test_init_with_all_valid_voices(self, voice):
        """Test that all valid voice options are accepted."""
        config = OpenAIChatAudioConfig(voice=voice)
        assert config.voice == voice

    @pytest.mark.parametrize("audio_format", ["wav", "mp3", "flac", "opus", "pcm16"])
    def test_init_with_all_valid_formats(self, audio_format):
        """Test that all valid audio format options are accepted."""
        config = OpenAIChatAudioConfig(voice="alloy", audio_format=audio_format)
        assert config.audio_format == audio_format


class TestOpenAIChatAudioConfigToExtraBodyParameters:
    """Tests for the to_extra_body_parameters method."""

    def test_to_extra_body_parameters_basic(self):
        """Test conversion to extra body parameters with defaults."""
        config = OpenAIChatAudioConfig(voice="alloy")

        params = config.to_extra_body_parameters()

        assert params == {
            "modalities": ["text", "audio"],
            "audio": {"voice": "alloy", "format": "wav"},
        }

    def test_to_extra_body_parameters_custom_format(self):
        """Test conversion with custom format."""
        config = OpenAIChatAudioConfig(voice="coral", audio_format="mp3")

        params = config.to_extra_body_parameters()

        assert params == {
            "modalities": ["text", "audio"],
            "audio": {"voice": "coral", "format": "mp3"},
        }

    def test_to_extra_body_parameters_all_voices(self):
        """Test that all voices produce valid parameters."""
        voices = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]

        for voice in voices:
            config = OpenAIChatAudioConfig(voice=voice)  # type: ignore[arg-type]
            params = config.to_extra_body_parameters()

            assert params["modalities"] == ["text", "audio"]
            assert params["audio"]["voice"] == voice
            assert params["audio"]["format"] == "wav"

    def test_to_extra_body_parameters_all_formats(self):
        """Test that all formats produce valid parameters."""
        formats = ["wav", "mp3", "flac", "opus", "pcm16"]

        for audio_format in formats:
            config = OpenAIChatAudioConfig(voice="alloy", audio_format=audio_format)  # type: ignore[arg-type]
            params = config.to_extra_body_parameters()

            assert params["audio"]["format"] == audio_format


class TestOpenAIChatAudioConfigPreferTranscript:
    """Tests for the prefer_transcript_for_history attribute."""

    def test_prefer_transcript_default_true(self):
        """Test that prefer_transcript_for_history defaults to True."""
        config = OpenAIChatAudioConfig(voice="alloy")
        assert config.prefer_transcript_for_history is True

    def test_prefer_transcript_can_be_false(self):
        """Test that prefer_transcript_for_history can be set to False."""
        config = OpenAIChatAudioConfig(voice="alloy", prefer_transcript_for_history=False)
        assert config.prefer_transcript_for_history is False

    def test_prefer_transcript_not_in_extra_body(self):
        """Test that prefer_transcript_for_history is not included in extra_body_parameters."""
        config = OpenAIChatAudioConfig(voice="alloy", prefer_transcript_for_history=False)
        params = config.to_extra_body_parameters()

        # prefer_transcript_for_history is for PyRIT internal use, not sent to API
        assert "prefer_transcript_for_history" not in params
        assert "prefer_transcript_for_history" not in params.get("audio", {})
