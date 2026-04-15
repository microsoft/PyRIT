# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.message_normalizer import (
    GenericSystemSquashNormalizer,
    HistorySquashNormalizer,
    MessageListNormalizer,
)
from pyrit.models import Message
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Single registry: add new normalizable capabilities here and nowhere else.
# Order in the list determines pipeline execution order.
# ---------------------------------------------------------------------------
_NORMALIZER_REGISTRY: list[tuple[CapabilityName, MessageListNormalizer[Message]]] = [
    (CapabilityName.SYSTEM_PROMPT, GenericSystemSquashNormalizer()),
    (CapabilityName.MULTI_TURN, HistorySquashNormalizer()),
]

# Derived constant — no manual maintenance required.
NORMALIZABLE_CAPABILITIES: frozenset[CapabilityName] = frozenset(cap for cap, _ in _NORMALIZER_REGISTRY)


class ConversationNormalizationPipeline:
    """
    Ordered sequence of message normalizers that adapt conversations when
    the target lacks certain capabilities.

    The pipeline is constructed via ``from_capabilities``, which resolves
    capabilities and policy into a concrete, ordered tuple of normalizers.
    ``normalize_async`` then simply executes that tuple in order.

    To add a new normalizable capability, add a single entry to
    ``_NORMALIZER_REGISTRY``.  ``NORMALIZABLE_CAPABILITIES``,
    pipeline ordering, and default normalizers are all derived from it.
    """

    def __init__(self, normalizers: tuple[MessageListNormalizer[Message], ...] = ()) -> None:
        """
        Initialize the normalization pipeline with an ordered sequence of normalizers.

        Args:
            normalizers (tuple[MessageListNormalizer[Message], ...]):
                Ordered normalizers to apply during ``normalize_async``.
                Defaults to an empty tuple (pass-through).
        """
        self._normalizers = normalizers

    @classmethod
    def from_capabilities(
        cls,
        *,
        capabilities: TargetCapabilities,
        policy: CapabilityHandlingPolicy,
        normalizer_overrides: dict[CapabilityName, MessageListNormalizer[Message]] | None = None,
    ) -> "ConversationNormalizationPipeline":
        """
        Resolve capabilities and policy into a concrete pipeline of normalizers.

        For each capability in ``_NORMALIZER_REGISTRY`` (in order):

        * If the target already supports the capability, no normalizer is added.
        * If the capability is missing and the policy is ``ADAPT``, the
          corresponding normalizer (from overrides or defaults) is added.
        * If the capability is missing and the policy is ``RAISE``, no
          normalizer is added (validation is deferred to
          ``TargetConfiguration.ensure_can_handle()``).

        Args:
            capabilities (TargetCapabilities): The target's declared capabilities.
            policy (CapabilityHandlingPolicy): How to handle each missing capability.
            normalizer_overrides (dict[CapabilityName, MessageListNormalizer[Message]] | None):
                Optional overrides for specific capability normalizers.
                Falls back to the defaults from ``_NORMALIZER_REGISTRY``.

        Returns:
            ConversationNormalizationPipeline: A pipeline with the resolved
            ordered tuple of normalizers.
        """
        overrides = normalizer_overrides or {}
        normalizers: list[MessageListNormalizer[Message]] = []

        for capability, default_normalizer in _NORMALIZER_REGISTRY:
            if capabilities.includes(capability=capability):
                continue

            behavior = policy.get_behavior(capability=capability)

            # RAISE capabilities are skipped here — no normalizer is added.
            # Validation is deferred to TargetConfiguration.ensure_can_handle(),
            # which should be called in the request flow once the full end-to-end
            # workflow is implemented.
            if behavior == UnsupportedCapabilityBehavior.ADAPT:
                normalizer = overrides.get(capability, default_normalizer)

                normalizers.append(normalizer)

        return cls(normalizers=tuple(normalizers))

    async def normalize_async(self, *, messages: list[Message]) -> list[Message]:
        """
        Run the pre-resolved normalizer sequence over the messages.

        Args:
            messages (list[Message]): The full conversation to normalize.

        Returns:
            list[Message]: The (possibly adapted) message list.
        """
        result = list(messages)
        for normalizer in self._normalizers:
            result = await normalizer.normalize_async(result)
        return result

    @property
    def normalizers(self) -> tuple[MessageListNormalizer[Message], ...]:
        """
        The ordered normalizers in this pipeline.

        Returns:
            tuple[MessageListNormalizer[Message], ...]: The normalizer sequence.
        """
        return self._normalizers
