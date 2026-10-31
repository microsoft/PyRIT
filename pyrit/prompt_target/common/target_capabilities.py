# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Literal, NoReturn, cast

from pyrit.models.literals import PromptDataType
from pyrit.models.target.target_capabilities import CapabilityName, TargetCapabilities
from pyrit.prompt_target.common.tool_call_history import TOOL_CALL_INPUT_MODALITIES

__all__ = [
    "CapabilityHandlingPolicy",
    "CapabilityName",
    "OpenAIModelSampling",
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
    * Non-adaptable capabilities (e.g. ``supports_multi_message_pieces``) are not
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
            non-adaptable capabilities (e.g., supports_multi_message_pieces).
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


class OpenAIModelSampling:
    """Resolve OpenAI sampling support without duplicating complete capability profiles."""

    _RULES: ClassVar[
        tuple[tuple[frozenset[str], Literal["supported", "requires_none", "unsupported"], str | None], ...]
    ] = (
        (
            frozenset(
                {
                    "gpt-4",
                    "gpt-4-0314",
                    "gpt-4-0613",
                    "gpt-4-32k",
                    "gpt-4-32k-0314",
                    "gpt-4-32k-0613",
                    "gpt-4-turbo",
                    "gpt-4-turbo-preview",
                    "gpt-4-1106-preview",
                    "gpt-4-0125-preview",
                    "gpt-4-vision-preview",
                    "gpt-4o",
                    "gpt-4o-mini",
                    "gpt-4.1",
                    "gpt-4.1-mini",
                    "gpt-4.1-nano",
                }
            ),
            "supported",
            None,
        ),
        (frozenset({"gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"}), "requires_none", "none"),
        (
            frozenset({"gpt-5.5", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}),
            "requires_none",
            "medium",
        ),
        (
            frozenset(
                {
                    "gpt-5",
                    "gpt-5-mini",
                    "gpt-5-nano",
                    "gpt-5-pro",
                    "gpt-5.2-pro",
                    "gpt-5.4-pro",
                    "gpt-5.5-pro",
                    "gpt-5-codex",
                    "gpt-5.1-codex",
                    "gpt-5.1-codex-mini",
                    "gpt-5.1-codex-max",
                    "gpt-5.2-codex",
                    "gpt-5.3-codex",
                    "o1",
                    "o1-mini",
                    "o1-preview",
                    "o1-pro",
                    "o3",
                    "o3-mini",
                    "o3-pro",
                    "o4-mini",
                    "gpt-4o-audio-preview",
                    "gpt-4o-mini-audio-preview",
                    "gpt-4o-realtime-preview",
                    "gpt-4o-mini-realtime-preview",
                }
            ),
            "unsupported",
            None,
        ),
    )
    _SNAPSHOT_SUFFIX: ClassVar[re.Pattern[str]] = re.compile(r"-\d{4}-\d{2}-\d{2}$")

    @classmethod
    def supports_temperature(
        cls, *, model_name: object, reasoning_effort: object = None, declared_support: bool | None = None
    ) -> bool:
        """
        Resolve support using the model's sampling rule and effective reasoning effort.

        Args:
            model_name (object): Effective model or deployment name from the request configuration.
            reasoning_effort (object): Effective effort, or None to use the documented model default.
            declared_support (bool | None): Explicit capability declaration. False disables support;
                True permits unrecognized deployments but cannot bypass a known model's restrictions.

        Returns:
            bool: Whether this model configuration accepts a runtime temperature.
        """
        if declared_support is False or not isinstance(model_name, str):
            return False
        # Only dated snapshots inherit a named model's rule, not arbitrary prefixed variants.
        model = cls._SNAPSHOT_SUFFIX.sub("", model_name)
        for models, mode, default_effort in cls._RULES:
            if model in models:
                if mode == "unsupported":
                    return False
                if mode == "requires_none":
                    return (default_effort if reasoning_effort is None else reasoning_effort) == "none"
                return reasoning_effort in (None, "none")
        return declared_support is True and reasoning_effort in (None, "none")


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
    input_modalities=_TEXT_IMAGE_INPUT | TOOL_CALL_INPUT_MODALITIES,
    output_modalities=_TEXT_OUTPUT,
)

_GPT_5 = TargetCapabilities(
    supports_multi_turn=True,
    supports_multi_message_pieces=True,
    supports_system_prompt=True,
    supports_json_schema=True,
    supports_json_output=True,
    supports_editable_history=True,
    input_modalities=_TEXT_IMAGE_INPUT | TOOL_CALL_INPUT_MODALITIES,
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
    profile = _KNOWN_CAPABILITIES.get(underlying_model)
    if profile is None:
        return None
    return profile.model_copy(
        update={"supports_temperature": OpenAIModelSampling.supports_temperature(model_name=underlying_model)}
    )
