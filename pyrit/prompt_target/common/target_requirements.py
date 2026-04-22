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
    """

    # The set of capabilities the consumer requires.
    required_capabilities: frozenset[CapabilityName] = field(default_factory=frozenset)

    def validate(self, *, configuration: TargetConfiguration) -> None:
        """
        Validate that the target configuration can satisfy all requirements.

        Iterates over every required capability and delegates to
        ``TargetConfiguration.ensure_can_handle``, which checks native support
        first and then consults the handling policy. All violations are
        collected and reported in a single ``ValueError``.

        Args:
            configuration (TargetConfiguration): The target configuration to validate against.

        Raises:
            ValueError: If any required capability is missing and the policy
                does not allow adaptation.
        """
        errors: list[str] = []
        for capability in sorted(self.required_capabilities, key=lambda c: c.value):
            try:
                configuration.ensure_can_handle(capability=capability)
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            raise ValueError(
                f"Target does not satisfy {len(errors)} required capability(ies):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
