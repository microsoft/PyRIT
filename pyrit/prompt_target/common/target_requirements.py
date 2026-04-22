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

    Requirements are satisfied either by native support on the target or
    by an ``ADAPT`` entry in the target's
    :class:`CapabilityHandlingPolicy`. Consumers that cannot tolerate
    adaptation should perform their own ``capabilities.includes(...)``
    check instead of declaring a requirement here.
    """

    required: frozenset[CapabilityName] = field(default_factory=frozenset)

    def validate(self, *, target: PromptTarget) -> None:
        """
        Validate that ``target`` can satisfy every required capability.

        Args:
            target (PromptTarget): The target to validate against.

        Raises:
            ValueError: If any required capability is not supported natively
                and has no ``ADAPT`` entry in the target's policy.
        """
        for capability in self.required:
            target.configuration.ensure_can_handle(capability=capability)


# Shared requirement used by scorers and converters that set a system prompt
# and drive a short multi-turn conversation. Adaptation is acceptable: the
# consumer only needs the behavior on the wire, not native support.
CHAT_CONSUMER_REQUIREMENTS = TargetRequirements(
    required=frozenset({CapabilityName.SYSTEM_PROMPT, CapabilityName.MULTI_TURN}),
)
