# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import Any, Literal

# Voices supported by OpenAI Chat Completions API audio output.
# OpenAI SDK: openai/types/chat/chat_completion_audio_param.py voice field
# SDK Literal includes: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar
# SDK docstring also lists: fable, nova, onyx (we include these for completeness)
# Note: SDK uses Union[str, Literal[...]] so any string is accepted by the API.
ChatAudioVoice = Literal[
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"
]

# Audio output formats supported by OpenAI Chat Completions API.
# OpenAI SDK: openai/types/chat/chat_completion_audio_param.py format field
# defines format: Required[Literal["wav", "aac", "mp3", "flac", "opus", "pcm16"]]
ChatAudioFormat = Literal["wav", "aac", "mp3", "flac", "opus", "pcm16"]


@dataclass
class OpenAIChatAudioConfig:
    """
    Configuration for audio output from OpenAI Chat Completions API.

    When provided to OpenAIChatTarget, this enables audio output from models
    that support it (e.g., gpt-4o-audio-preview).

    Note: This is specific to the Chat Completions API. The Responses API does not
    support audio input or output. For real-time audio, use RealtimeTarget instead.
    """

    # The voice to use for audio output. Supported voices are:
    voice: ChatAudioVoice

    # The audio format for the response. Supported formats are:
    audio_format: ChatAudioFormat = "wav"

    # If True, historical user messages that contain both audio and text will only send
    # the text (transcript) to reduce bandwidth and token usage. The current (last) user
    # message will still include audio. Defaults to True.
    prefer_transcript_for_history: bool = True

    def to_extra_body_parameters(self) -> dict[str, Any]:
        """
        Convert the config to extra_body_parameters format for OpenAI API.

        Returns:
            dict: Parameters to include in the request body for audio output.
        """
        return {
            "modalities": ["text", "audio"],
            "audio": {"voice": self.voice, "format": self.audio_format},
        }
