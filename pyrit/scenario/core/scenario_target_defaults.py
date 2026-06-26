# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.registry import TargetRegistry

logger = logging.getLogger(__name__)


def get_default_scorer_target() -> PromptTarget:
    """
    Resolve the default objective scorer chat target.

    First checks the ``TargetRegistry`` for an ``"objective_scorer_chat"`` entry
    (populated by ``TargetInitializer`` from ``OBJECTIVE_SCORER_CHAT_*`` env vars).
    Falls back to a plain ``OpenAIChatTarget``

    Returns:
        PromptTarget: The resolved objective scorer chat target.

    Raises:
        ValueError: If the registered target does not support multi-turn.
    """
    return _get_default_chat_target(preferred_target_key="objective_scorer_chat")


def get_default_adversarial_target() -> PromptTarget:
    """
    Resolve the default adversarial chat target.

    First checks the ``TargetRegistry`` for an ``"adversarial_chat"`` entry
    (populated by ``TargetInitializer`` from ``ADVERSARIAL_CHAT_*`` env vars).
    Falls back to a default fallback target with temperature=1.2

    Returns:
        PromptTarget: The resolved adversarial chat target.

    Raises:
        ValueError: If the registered target does not support multi-turn.
    """
    return _get_default_chat_target(
        preferred_target_key="adversarial_chat",
        required_capabilities={CapabilityName.MULTI_TURN},
        fallback_temperature=1.2,
    )


def _get_default_chat_target(
    *,
    preferred_target_key: str,
    required_capabilities: set[CapabilityName] | None = None,
    fallback_temperature: float | None = None,
) -> PromptTarget:
    """
    Resolve a chat target from TargetRegistry with configurable fallback behavior.

    Resolution order:
    1. ``preferred_target_key`` entry from ``TargetRegistry``
    2. ``OpenAIChatTarget(...)`` with optional temperature

    Args:
        preferred_target_key (str): TargetRegistry key to resolve first.
        required_capabilities (set[CapabilityName] | None): Optional capabilities
            that a resolved target must support.
        fallback_temperature (float | None): Optional temperature for fallback
            ``OpenAIChatTarget`` construction.

    Returns:
        PromptTarget: The resolved chat target.

    Raises:
        ValueError: If the resolved target does not satisfy required capabilities.
        ValueError: If the registry entry exists but is not a PromptTarget.
    """
    registry = TargetRegistry.get_registry_singleton()
    target = registry.instances.get(preferred_target_key)
    if target is not None:
        # Check required capabilities first (fail fast)
        if required_capabilities:
            for capability in required_capabilities:
                if not target.capabilities.includes(capability=capability):
                    raise ValueError(f"Registry entry '{preferred_target_key}' must support {capability.value}.")

        # Then check type
        if not isinstance(target, PromptTarget):
            raise ValueError(
                f"Registry entry '{preferred_target_key}' must be a PromptTarget, but got {type(target).__name__}"
            )

        return target

    logger.warning(
        f"TargetRegistry entry '{preferred_target_key}' not found. Falling back to default OpenAIChatTarget."
    )
    return OpenAIChatTarget(temperature=fallback_temperature)
