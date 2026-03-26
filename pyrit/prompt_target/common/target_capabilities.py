# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import Optional, cast

from pyrit.models import PromptDataType


@dataclass(frozen=True)
class TargetCapabilities:
    """
    Describes the capabilities of a PromptTarget so that attacks
    and other components can adapt their behavior accordingly.

    Each target class defines default capabilities via the _DEFAULT_CAPABILITIES
    class attribute. Users can override individual capabilities per instance
    through constructor parameters, which is useful for targets whose
    capabilities depend on deployment configuration (e.g., Playwright, HTTP).
    """

    # Whether the target natively supports multi-turn conversations
    # (i.e., it accepts and uses conversation history or maintains state
    # across turns via external mechanisms like WebSocket connections).
    supports_multi_turn: bool = False

    # Whether the target natively supports multiple message pieces in a single request.
    supports_multi_message_pieces: bool = False

    # Whether the target natively supports constraining output to a provided JSON schema.
    supports_json_schema: bool = False

    # Whether the target natively supports JSON output (e.g., via a "json" response format), which ensures the output
    # is valid JSON.
    supports_json_output: bool = False

    # Whether the target allows the attack history to be modified. Implies that the target supports
    # multi-turn interactions and that the attack history is not immutable once set.
    supports_editable_history: bool = False

    # The input modalities supported by the target (e.g., "text", "image").
    input_modalities: frozenset[frozenset[PromptDataType]] = frozenset({frozenset(["text"])})

    # The output modalities supported by the target (e.g., "text", "image").
    output_modalities: frozenset[frozenset[PromptDataType]] = frozenset({frozenset(["text"])})

    @staticmethod
    def get_known_capabilities(underlying_model: str) -> "Optional[TargetCapabilities]":
        """
        Return the known capabilities for a specific underlying model, or None if unrecognized.

        Args:
            underlying_model (str): The underlying model name (e.g., "gpt-4o").

        Returns:
            TargetCapabilities | None: The known capabilities for the model, or None if the model
            is not recognized.
        """
        return _KNOWN_CAPABILITIES.get(underlying_model)


# ---------------------------------------------------------------------------
# Known capability profiles — add new models here.
# Shared profiles are defined once and referenced by multiple model names.
# ---------------------------------------------------------------------------

_TEXT_IMAGE_INPUT: frozenset[frozenset[PromptDataType]] = cast(
    "frozenset[frozenset[PromptDataType]]",
    frozenset({frozenset({"text"}), frozenset({"image_path"}), frozenset({"text", "image_path"})}),
)
_TEXT_OUTPUT: frozenset[frozenset[PromptDataType]] = cast(
    "frozenset[frozenset[PromptDataType]]",
    frozenset({frozenset({"text"})}),
)

_GPT_4O = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    supports_json_output=True,
    input_modalities=_TEXT_IMAGE_INPUT,
    output_modalities=_TEXT_OUTPUT,
)

_GPT_5 = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    supports_json_schema=True,
    supports_json_output=True,
    input_modalities=_TEXT_IMAGE_INPUT,
    output_modalities=_TEXT_OUTPUT,
)

_GPT_REALTIME_1_5 = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    supports_editable_history=True,
    input_modalities=frozenset(
        {
            frozenset({"text"}),
            frozenset({"audio_path"}),
            frozenset({"image_path"}),
            frozenset({"text", "audio_path"}),
            frozenset({"text", "image_path"}),
            frozenset({"audio_path", "image_path"}),
            frozenset({"text", "audio_path", "image_path"}),
        }
    ),
    output_modalities=frozenset(
        {
            frozenset({"text"}),
            frozenset({"audio_path"}),
            frozenset({"text", "audio_path"}),
        }
    ),
)

_TTS = TargetCapabilities(
    output_modalities=frozenset({frozenset({"audio_path"})}),
)

_SORA_2 = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    input_modalities=_TEXT_IMAGE_INPUT,
    output_modalities=frozenset({frozenset({"audio_path", "video_path"}), frozenset({"video_path"})}),
)

_KNOWN_CAPABILITIES: dict[str, TargetCapabilities] = {
    "gpt-4o": _GPT_4O,
    "gpt-5": _GPT_5,
    "gpt-5.1": _GPT_5,
    "gpt-5.4": _GPT_5,
    "gpt-realtime-1.5": _GPT_REALTIME_1_5,
    "tts": _TTS,
    "sora-2": _SORA_2,
}
