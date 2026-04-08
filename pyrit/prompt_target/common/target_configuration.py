# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.message_normalizer import MessageListNormalizer
from pyrit.models import Message
from pyrit.prompt_target.common.conversation_normalization_pipeline import ConversationNormalizationPipeline
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)

logger = logging.getLogger(__name__)

# Default policy: RAISE on all adaptable capabilities.
_DEFAULT_POLICY = CapabilityHandlingPolicy()


class TargetConfiguration:
    """
    Unified configuration that describes what a target supports, what to do
    when it doesn't, and how to adapt.

    Composes three concerns into a single object:

    * **TargetCapabilities** — declarative, immutable description of what the
      target natively supports.
    * **CapabilityHandlingPolicy** — per-capability behavior (ADAPT or RAISE)
      when a capability is missing.
    * **ConversationNormalizationPipeline** — ordered sequence of normalizers
      built from the gap between capabilities and policy.

    Each target defines defaults; callers can override policy or individual
    normalizers at creation time.
    """

    def __init__(
        self,
        *,
        capabilities: TargetCapabilities,
        policy: CapabilityHandlingPolicy | None = None,
        normalizer_overrides: dict[CapabilityName, MessageListNormalizer[Message]] | None = None,
    ) -> None:
        """
        Build a target configuration and resolve the normalization pipeline.

        Args:
            capabilities (TargetCapabilities): The target's declared capabilities.
            policy (CapabilityHandlingPolicy | None): How to handle each missing
                capability. Defaults to RAISE for all adaptable capabilities.
            normalizer_overrides (dict[CapabilityName, MessageListNormalizer[Message]] | None):
                Optional overrides for specific capability normalizers.

        Raises:
            ValueError: If a required capability is missing and the policy is RAISE.
        """
        self._capabilities = capabilities
        self._policy = policy or _DEFAULT_POLICY
        self._pipeline = ConversationNormalizationPipeline.from_capabilities(
            capabilities=self._capabilities,
            policy=self._policy,
            normalizer_overrides=normalizer_overrides,
        )

    @property
    def capabilities(self) -> TargetCapabilities:
        """The target's declared capabilities."""
        return self._capabilities

    @property
    def policy(self) -> CapabilityHandlingPolicy:
        """The handling policy for missing capabilities."""
        return self._policy

    @property
    def pipeline(self) -> ConversationNormalizationPipeline:
        """The resolved normalization pipeline."""
        return self._pipeline

    def includes(self, *, capability: CapabilityName) -> bool:
        """
        Check whether the target includes support for the given capability.

        Args:
            capability (CapabilityName): The capability to check.

        Returns:
            bool: True if the target supports it natively.
        """
        return self._capabilities.includes(capability=capability)

    def ensure_can_handle(self, *, capability: CapabilityName) -> None:
        """
        Validate that the target either supports the capability natively or
        has an ADAPT policy for it.

        Intended for use by consumers (attacks, converters, scorers) at
        construction time.

        Args:
            capability (CapabilityName): The required capability.

        Raises:
            ValueError: If the capability is missing and the policy is RAISE
                or no normalizer is available.
        """
        if self._capabilities.includes(capability=capability):
            return

        try:
            behavior = self._policy.get_behavior(capability=capability)
        except KeyError:
            raise ValueError(
                f"Target does not support '{capability.value}' and no handling policy exists for it."
            ) from None
        if behavior == UnsupportedCapabilityBehavior.RAISE:
            raise ValueError(f"Target does not support '{capability.value}' and the handling policy is RAISE.")

    async def normalize_async(self, *, messages: list[Message]) -> list[Message]:
        """
        Run the normalization pipeline over the given messages.

        Args:
            messages (list[Message]): The full conversation to normalize.

        Returns:
            list[Message]: The (possibly adapted) message list.
        """
        return await self._pipeline.normalize_async(messages=messages)
