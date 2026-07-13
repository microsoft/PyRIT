# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pyrit.prompt_target.common.target_capabilities import CapabilityName

if TYPE_CHECKING:
    from pyrit.models import PromptDataType
    from pyrit.prompt_target.common.prompt_target import PromptTarget


@dataclass(frozen=True)
class TargetRequirements:
    """
    Declarative description of what a consumer (attack, converter, scorer)
    requires from a target.

    The single source of truth for capability names is the
    ``CapabilityName`` enum; this class is simply a typed wrapper
    around the set of capabilities a consumer needs.

    Two tiers of requirement are supported:

    * ``required`` \u2014 satisfied either by native support on the target or
      by an ``ADAPT`` entry in the target's
      ``CapabilityHandlingPolicy``. Use this when the consumer only
      needs the behavior to appear on the wire.
    * ``native_required`` \u2014 must be natively supported. Adaptation is
      rejected. Use this when adaptation would silently change the
      consumer's semantics (e.g. an attack that depends on the target
      remembering prior turns, where history-squash normalization would
      collapse the conversation into a single prompt).

    Modality requirements are also supported:

    * ``required_input_modalities`` — each entry is a frozenset of
      ``PromptDataType`` values the consumer needs the target to
      accept. At least one of the target's input modality combos must be
      a superset of each required combo.
    * ``required_output_modalities`` — same semantics for outputs.
    """

    required: frozenset[CapabilityName] = field(default_factory=frozenset)
    native_required: frozenset[CapabilityName] = field(default_factory=frozenset)
    required_input_modalities: frozenset[frozenset[PromptDataType]] = field(default_factory=frozenset)
    required_output_modalities: frozenset[frozenset[PromptDataType]] = field(default_factory=frozenset)

    def validate(self, *, target: PromptTarget) -> None:
        """
        Validate that ``target`` can satisfy every declared requirement.

        All violations across both tiers are collected and reported in a
        single ``ValueError`` so callers see every missing capability at
        once, not just the first one.

        Args:
            target (PromptTarget): The target to validate against.

        Raises:
            ValueError: If any ``native_required`` capability is not natively
                supported, or if any ``required`` capability is not supported
                natively and has no ``ADAPT`` entry in the target's policy,
                or if the target's modalities do not satisfy
                ``required_input_modalities`` / ``required_output_modalities``.
        """
        errors: list[str] = [
            f"Target must natively support '{capability.value}'; adaptation is not acceptable for this consumer."
            for capability in sorted(self.native_required, key=lambda c: c.value)
            if not target.configuration.includes(capability=capability)
        ]

        for capability in sorted(self.required, key=lambda c: c.value):
            try:
                target.configuration.ensure_can_handle(capability=capability)
            except ValueError as exc:
                errors.append(str(exc))

        errors.extend(
            self._check_modalities(
                required=self.required_input_modalities,
                supported=target.configuration.capabilities.input_modalities,
                direction="input",
            )
        )
        errors.extend(
            self._check_modalities(
                required=self.required_output_modalities,
                supported=target.configuration.capabilities.output_modalities,
                direction="output",
            )
        )

        if errors:
            raise ValueError(
                f"Target does not satisfy {len(errors)} required capability(ies):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    @staticmethod
    def _check_modalities(
        *,
        required: frozenset[frozenset[PromptDataType]],
        supported: frozenset[frozenset[PromptDataType]],
        direction: str,
    ) -> list[str]:
        """Return error strings for each required modality combo not covered by *supported*."""
        return [
            f"Target must support {direction} modality {{{', '.join(sorted(combo))}}}; "
            f"supported: {[sorted(s) for s in sorted(supported, key=lambda s: sorted(s))]}."
            for combo in sorted(required, key=lambda c: sorted(c))
            if not any(combo <= sup for sup in supported)
        ]


def _build_chat_target_requirements() -> TargetRequirements:
    """
    Build the requirements for a chat-style target (multi-turn with editable history).

    Returns:
        TargetRequirements: The requirements for a chat-style target.
    """
    return TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY}))


CHAT_TARGET_REQUIREMENTS: TargetRequirements = _build_chat_target_requirements()
"""
Standard requirements for a chat-style target: must support multi-turn conversations
with an editable history. Consumers validate their target against
these requirements at construction time.
"""
