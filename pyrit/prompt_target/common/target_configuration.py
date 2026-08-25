# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

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


# Default policy: preserve each capability's ordinary global behavior.
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
        normalizer_overrides: Mapping[CapabilityName, MessageListNormalizer[Any]] | None = None,
    ) -> None:
        """
        Build a target configuration and resolve the normalization pipeline.

        Args:
            capabilities (TargetCapabilities): The target's declared capabilities.
            policy (CapabilityHandlingPolicy | None): How to handle each missing
                capability. Defaults to each capability's ordinary global behavior.
            normalizer_overrides (Mapping[CapabilityName, MessageListNormalizer[Any]] | None):
                Optional overrides for specific capability normalizers.
        """
        self._capabilities = capabilities
        self._policy = policy or _DEFAULT_POLICY
        self._normalizer_overrides = dict(normalizer_overrides or {})
        self._pipeline = ConversationNormalizationPipeline.from_capabilities(
            capabilities=self._capabilities,
            policy=self._policy,
            normalizer_overrides=self._normalizer_overrides,
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

    @property
    def normalizer_overrides(self) -> Mapping[CapabilityName, MessageListNormalizer[Any]]:
        """Read-only view of construction-time normalizer overrides."""
        return MappingProxyType(self._normalizer_overrides)

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

        if self._pipeline.has_normalizer_for(capability=capability):
            return

        try:
            behavior = self._policy.get_behavior(capability=capability)
        except KeyError:
            raise ValueError(
                f"Target does not support '{capability.value}' and no handling policy exists for it."
            ) from None
        if behavior == UnsupportedCapabilityBehavior.RAISE:
            raise ValueError(f"Target does not support '{capability.value}' and the handling policy is RAISE.")
        raise ValueError(
            f"Target does not support '{capability.value}', but no default or configured normalizer can adapt it."
        )

    async def normalize_async(
        self,
        *,
        messages: list[Message],
        normalizer_overrides: Mapping[CapabilityName, MessageListNormalizer[Any]] | None = None,
    ) -> list[Message]:
        """
        Run the normalization pipeline over the given messages.

        Args:
            messages (list[Message]): The full conversation to normalize.
            normalizer_overrides: Per-send replacements for capability normalizers.

        Returns:
            list[Message]: The (possibly adapted) message list.
        """
        pipeline = self._pipeline
        if normalizer_overrides:
            merged_overrides = {**self._normalizer_overrides, **normalizer_overrides}
            pipeline = ConversationNormalizationPipeline.from_capabilities(
                capabilities=self._capabilities,
                policy=self._policy,
                normalizer_overrides=merged_overrides,
            )
        return await pipeline.normalize_async(messages=messages)

    def as_identifier_params(self) -> dict[str, Any]:
        """
        Return a deterministic, serializable representation of this configuration
        suitable for inclusion in a ``ComponentIdentifier``.

        The returned dict preserves the structure of ``TargetConfiguration``
        — capabilities, policy, and pipeline are kept as nested sub-dicts rather
        than flattened into the caller — so the identifier reflects the shape of
        the object it describes.

        Two configurations that behave identically must produce equal dicts;
        configurations that differ in any identity-bearing field must produce
        unequal dicts. Modality sets are flattened to sorted lists of sorted
        lists so ordering is stable across runs.

        Returns:
            dict[str, Any]: The identifier parameters for this configuration.
        """
        caps = self._capabilities
        return {
            "capabilities": self._capabilities_to_identifier_params(caps),
            # Only unsupported capabilities appear here. Policy entries for
            # natively-supported capabilities are moot (the behavior never
            # fires), and omitting them keeps identifiers stable when default
            # policies expand to cover more capabilities.
            "capability_policy": {
                capability.value: behavior.value
                for capability, behavior in self._policy.behaviors.items()
                if not caps.includes(capability=capability)
            },
            # Stable, ordered representation of the pipeline this configuration was
            # built with. Per-send ``normalizer_overrides`` are NOT reflected here:
            # ``normalize_async`` builds a throwaway pipeline and never mutates
            # ``self._pipeline``. Attack-owned overrides are represented by the
            # attack identifier instead.
            "normalization_pipeline": [
                f"{type(normalizer).__module__}.{type(normalizer).__qualname__}"
                for normalizer in self._pipeline.normalizers
            ],
        }

    @staticmethod
    def _capabilities_to_identifier_params(capabilities: TargetCapabilities) -> dict[str, Any]:
        """
        Project a ``TargetCapabilities`` instance into a deterministic dict
        suitable for inclusion in a ``ComponentIdentifier``.

        Fields are discovered dynamically via the pydantic model fields so new
        capability fields are picked up automatically. Set-valued fields (e.g.,
        the modality frozensets) are detected by type and normalized to sorted
        lists of sorted lists; all other fields are passed through as-is.

        Args:
            capabilities (TargetCapabilities): The capabilities to serialize.

        Returns:
            dict[str, Any]: Field-name to serialized-value mapping.
        """
        params: dict[str, Any] = {}
        for field_name in type(capabilities).model_fields:
            value = getattr(capabilities, field_name)
            # Normalize set-valued fields (e.g., modality frozensets) to a
            # deterministic representation. Handles both frozenset[frozenset[...]]
            # (modality combinations) and plain frozensets.
            if isinstance(value, (frozenset, set)):
                params[field_name] = sorted(
                    sorted(item) if isinstance(item, (frozenset, set)) else item for item in value
                )
            else:
                params[field_name] = value
        return params
