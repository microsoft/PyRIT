# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``TargetCapabilities`` — a declarative description of what a target supports.

This is canonical *data* (what modalities and behaviors a target natively
handles), so it lives in ``pyrit.models`` next to the other core models rather
than in the ``pyrit.prompt_target`` package. Handling concerns that depend on
the message-normalization machinery (``CapabilityHandlingPolicy``,
``UnsupportedCapabilityBehavior``) and the known-model capability profiles
(``get_known_capabilities``) stay in
``pyrit.prompt_target.common.target_capabilities``.

Capabilities describe a target but are deliberately **not** part of identity:
they are not modeled on the typed identifier projections in
``pyrit.models.identifiers``.
"""

from __future__ import annotations

from enum import Enum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)

# Immutable text-only default shared by input/output modality fields. ``cast`` is used because
# ty infers the literal ``"text"`` as ``str`` (widening), which is not assignable to the invariant
# ``frozenset[frozenset[PromptDataType]]`` element type.
_DEFAULT_TEXT_MODALITIES: frozenset[frozenset[PromptDataType]] = cast(
    "frozenset[frozenset[PromptDataType]]", frozenset({frozenset({"text"})})
)


class CapabilityName(str, Enum):
    """
    Canonical identifiers for target capabilities.

    This keeps capability identity in one place so policy, requirements, and
    normalization code do not duplicate string field names.
    """

    MULTI_TURN = "supports_multi_turn"
    MULTI_MESSAGE_PIECES = "supports_multi_message_pieces"
    JSON_SCHEMA = "supports_json_schema"
    JSON_OUTPUT = "supports_json_output"
    EDITABLE_HISTORY = "supports_editable_history"
    SYSTEM_PROMPT = "supports_system_prompt"
    STREAMING_AUDIO = "supports_streaming_audio"


class TargetCapabilities(BaseModel):
    """
    Describes the capabilities of a PromptTarget so that attacks
    and other components can adapt their behavior accordingly.

    Each target class defines default capabilities via the _DEFAULT_CONFIGURATION
    class attribute. Users can override individual capabilities per instance
    through constructor parameters, which is useful for targets whose
    capabilities depend on deployment configuration (e.g., Playwright, HTTP).

    Immutable (``frozen``) so a single capabilities object can be safely shared
    across targets and reused as a known-model profile.
    """

    model_config = ConfigDict(frozen=True)

    #: Whether the target natively supports multi-turn conversations
    #: (i.e., it accepts and uses conversation history or maintains state
    #: across turns via external mechanisms like WebSocket connections).
    supports_multi_turn: bool = False

    #: Whether the target natively supports multiple message pieces in a single request.
    supports_multi_message_pieces: bool = False

    #: Whether the target natively supports constraining output to a provided JSON schema.
    supports_json_schema: bool = False

    #: Whether the target natively supports JSON output (e.g., via a "json" response
    #: format), which ensures the output is valid JSON.
    supports_json_output: bool = False

    #: Whether the target allows the attack history to be modified. Implies that the
    #: target supports multi-turn interactions and that the attack history is not
    #: immutable once set.
    supports_editable_history: bool = False

    #: Whether the target natively supports system prompts.
    supports_system_prompt: bool = False

    #: Whether the target supports the streaming audio API: opening a long-lived
    #: streaming session via ``open_streaming_session`` that pushes user audio chunks,
    #: delivers VAD-committed audio to the attack for converter work, swaps committed
    #: items in place, and drives manual ``response.create`` turns. Required by
    #: ``BargeInAttack``.
    supports_streaming_audio: bool = False

    #: The input modalities supported by the target (e.g., "text", "image").
    input_modalities: frozenset[frozenset[PromptDataType]] = Field(default=_DEFAULT_TEXT_MODALITIES)

    #: The output modalities supported by the target (e.g., "text", "image").
    output_modalities: frozenset[frozenset[PromptDataType]] = Field(default=_DEFAULT_TEXT_MODALITIES)

    def includes(self, *, capability: CapabilityName) -> bool:
        """
        Return whether this target supports the given capability.

        Args:
            capability: The capability to check.

        Returns:
            bool: True if supported, otherwise False.
        """
        return bool(getattr(self, capability.value))
