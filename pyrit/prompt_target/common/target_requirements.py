# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pyrit.prompt_target.common.target_capabilities import CapabilityName

if TYPE_CHECKING:
    from pyrit.prompt_target.common.prompt_target import PromptTarget


@dataclass(frozen=True)
class TargetRequirements:
    """
    Declarative description of what a consumer (attack, converter, scorer)
    requires from a target.

    The single source of truth for capability names is the
    :class:`CapabilityName` enum; this class is simply a typed wrapper
    around the set of capabilities a consumer needs.

    Two tiers of requirement are supported:

    * ``required`` \u2014 satisfied either by native support on the target or
      by an ``ADAPT`` entry in the target's
      :class:`CapabilityHandlingPolicy`. Use this when the consumer only
      needs the behavior to appear on the wire.
    * ``native_required`` \u2014 must be natively supported. Adaptation is
      rejected. Use this when adaptation would silently change the
      consumer's semantics (e.g. an attack that depends on the target
      remembering prior turns, where history-squash normalization would
      collapse the conversation into a single prompt).
    """

    required: frozenset[CapabilityName] = field(default_factory=frozenset)
    native_required: frozenset[CapabilityName] = field(default_factory=frozenset)

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
                natively and has no ``ADAPT`` entry in the target's policy.
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

        if errors:
            raise ValueError(
                f"Target does not satisfy {len(errors)} required capability(ies):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )


# Shared requirement used by scorers and converters that set a system prompt
# and drive a short multi-turn conversation. Adaptation is acceptable, native
# support is not required.
CHAT_CONSUMER_REQUIREMENTS = TargetRequirements(
    required=frozenset({CapabilityName.EDITABLE_HISTORY, CapabilityName.MULTI_TURN}),
)
