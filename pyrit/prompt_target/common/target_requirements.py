# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.prompt_target.common.target_capabilities import CapabilityName
    from pyrit.prompt_target.common.target_configuration import TargetConfiguration


@dataclass(frozen=True)
class TargetRequirements:
    """
    Declarative description of what a consumer (attack, converter, scorer)
    requires from a target.

    Consumers define their requirements once and validate them against a
    ``TargetConfiguration`` at construction time. This replaces ad-hoc
    ``isinstance`` checks and scattered capability branching.

    Two levels of requirement are supported:

    * ``required_capabilities`` — the target must *handle* the capability,
      either natively or via an ``ADAPT`` policy (normalization pipeline).
      Use this when the consumer only cares that the behavior is available
      on the wire, regardless of how.
    * ``required_native_capabilities`` — the target must support the
      capability natively; adaptation via the normalization pipeline is
      not acceptable. Use this when adaptation would defeat the consumer's
      purpose (e.g. a multi-turn attack cannot run against a target whose
      history is squashed into a single prompt).
    """

    # Capabilities the consumer requires, native or adapted.
    required_capabilities: frozenset[CapabilityName] = field(default_factory=frozenset)

    # Capabilities the consumer requires to be natively supported. Adaptation
    # via the normalization pipeline is not acceptable for these capabilities.
    required_native_capabilities: frozenset[CapabilityName] = field(default_factory=frozenset)

    def validate(self, *, configuration: TargetConfiguration) -> None:
        """
        Validate that the target configuration can satisfy all requirements.

        For ``required_capabilities`` this delegates to
        ``TargetConfiguration.ensure_can_handle``, which accepts either
        native support or an ``ADAPT`` policy. For
        ``required_native_capabilities`` this checks
        ``TargetConfiguration.includes`` directly — adaptation is not
        acceptable. All violations are collected and reported in a single
        ``ValueError``.

        Args:
            configuration (TargetConfiguration): The target configuration to validate against.

        Raises:
            ValueError: If any required capability cannot be satisfied.
        """
        errors: list[str] = []
        for capability in sorted(self.required_capabilities, key=lambda c: c.value):
            try:
                configuration.ensure_can_handle(capability=capability)
            except ValueError as exc:
                errors.append(str(exc))
        for capability in sorted(self.required_native_capabilities, key=lambda c: c.value):
            if not configuration.includes(capability=capability):
                errors.append(
                    f"Target does not natively support '{capability.value}' "
                    "and adaptation is not acceptable for this consumer."
                )
        if errors:
            raise ValueError(
                f"Target does not satisfy {len(errors)} required capability(ies):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )


def _build_chat_consumer_requirements() -> TargetRequirements:
    # Imported lazily to avoid a hard import cycle with target_capabilities at
    # module load time (target_requirements only type-checks CapabilityName).
    from pyrit.prompt_target.common.target_capabilities import CapabilityName

    return TargetRequirements(
        required_capabilities=frozenset(
            {CapabilityName.SYSTEM_PROMPT, CapabilityName.MULTI_TURN}
        ),
    )


# Requirements declared by code paths that historically demanded a
# ``PromptChatTarget`` (converters and scorers that call ``set_system_prompt``
# and then send a short conversation). Adaptation via the normalization
# pipeline is acceptable here — the consumer only needs the *behavior*, not
# native support.
CHAT_CONSUMER_REQUIREMENTS: TargetRequirements = _build_chat_consumer_requirements()
