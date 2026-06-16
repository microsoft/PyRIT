# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import NoReturn, cast

from pyrit.models.literals import PromptDataType
from pyrit.models.target_capabilities import CapabilityName, TargetCapabilities

__all__ = [
    "CapabilityHandlingPolicy",
    "CapabilityName",
    "TargetCapabilities",
    "UnsupportedCapabilityBehavior",
    "get_known_capabilities",
]


class UnsupportedCapabilityBehavior(str, Enum):
    """
    Defines what happens when a caller requires a capability the target does not support.

    ADAPT: apply a normalization step to work around the unsupported capability.
    RAISE: fail immediately with an error.
    """

    ADAPT = "adapt"
    RAISE = "raise"


@dataclass(frozen=True)
class CapabilityHandlingPolicy:
    """
    Per-capability policy consulted only when a capability is unsupported.

    Design invariants
    -----------------
    * The policy is never consulted if the capability is already supported.
    * Non-adaptable capabilities (e.g. ``supports_editable_history``) are not
      represented here; requesting them on a target that lacks them always
      raises immediately.
    """

    behaviors: Mapping[CapabilityName, UnsupportedCapabilityBehavior] = field(
        default_factory=lambda: {
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.ADAPT,
        }
    )

    def get_behavior(self, *, capability: CapabilityName) -> UnsupportedCapabilityBehavior:
        """
        Return the configured handling behavior for a capability.

        Args:
            capability: The capability to look up.

        Returns:
            UnsupportedCapabilityBehavior: The configured behavior.

        Raises:
            KeyError: If no behavior exists for the capability. This occurs for
            non-adaptable capabilities (e.g., supports_editable_history).
        """
        try:
            return self.behaviors[capability]
        except KeyError:
            supported = ", ".join(sorted(cap.value for cap in self.behaviors))
            raise KeyError(
                f"No policy for capability '{capability.value}'. Supported capabilities: {supported}."
            ) from None

    def __getattr__(self, name: str) -> NoReturn:
        """
        Guard against accessing policies for non-adaptable or unknown capabilities.

        Raises:
            AttributeError: If the capability is not part of this policy.
        """
        for capability in CapabilityName:
            if capability.value == name:
                supported_names = ", ".join(sorted(cap.value for cap in self.behaviors))
                raise AttributeError(
                    f"'{type(self).__name__}' has no policy for '{name}'. "
                    f"Only the following capabilities have handling policies: "
                    f"{supported_names}."
                )

        raise AttributeError(name)

    def __post_init__(self) -> None:
        """Create a defensive read-only copy of the behaviors mapping."""
        # object.__setattr__ is required because the dataclass is frozen.
        object.__setattr__(self, "behaviors", MappingProxyType(dict(self.behaviors)))


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
    supports_system_prompt=True,
    supports_json_output=True,
    supports_editable_history=True,
    input_modalities=_TEXT_IMAGE_INPUT,
    output_modalities=_TEXT_OUTPUT,
)

_GPT_5 = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    supports_system_prompt=True,
    supports_json_schema=True,
    supports_json_output=True,
    supports_editable_history=True,
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


def get_known_capabilities(underlying_model: str) -> TargetCapabilities | None:
    """
    Return the known capabilities for a specific underlying model, or None if unrecognized.

    Args:
        underlying_model (str): The underlying model name (e.g., "gpt-4o").

    Returns:
        TargetCapabilities | None: The known capabilities for the model, or None if the model
        is not recognized.
    """
    return _KNOWN_CAPABILITIES.get(underlying_model)
