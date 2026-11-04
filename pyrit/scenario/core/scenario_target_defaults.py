# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.registry import TargetRegistry

_adversarial_target_override: ContextVar[PromptTarget | None] = ContextVar("adversarial_target_override", default=None)


class TargetRoleConfigurationError(ValueError):
    """A required named target role has not been configured."""

    def __init__(self, *, role: str) -> None:
        """Identify the missing role and its supported explicit setup paths."""
        self.role = role
        super().__init__(
            f"Required target role '{role}' is not configured. "
            "Create and select a target for this role, register a target under this name, "
            "or explicitly run the target initializer with this role's environment configuration."
        )


@contextmanager
def override_default_adversarial_target(target: PromptTarget | None) -> Generator[None, None, None]:
    """
    Override the adversarial fallback for this execution scope.

    Wrap scenario construction, initialization, and execution in this scope to
    cover both eager and lazy resolution. Explicit scenario/factory targets win;
    existing scenarios are not rewritten. Nested scopes restore their parent and
    separate asyncio tasks remain isolated. Scorer defaults are unchanged.

    Args:
        target (PromptTarget | None): The multi-turn target to use before registry defaults.
            None leaves the current scope unchanged, including any outer override.

    Yields:
        None: While the scoped default is active.

    Raises:
        ValueError: If a non-None target is not a PromptTarget or does not support multi-turn.
    """
    if target is None:
        yield
        return
    validate_default_adversarial_target(target)
    token = _adversarial_target_override.set(target)
    try:
        yield
    finally:
        _adversarial_target_override.reset(token)


def validate_default_adversarial_target(target: PromptTarget) -> None:
    """
    Validate a selected adversarial default without changing the current scope.

    Args:
        target (PromptTarget): The selected target, which must support multi-turn.
            None is not a selection and must be handled by the caller.

    Raises:
        ValueError: If target is not a PromptTarget or does not support multi-turn.
    """
    if not isinstance(target, PromptTarget):
        raise ValueError(f"Default adversarial target must be a PromptTarget, but got {type(target).__name__}.")
    if not target.capabilities.includes(capability=CapabilityName.MULTI_TURN):
        raise ValueError("Default adversarial target must support multi_turn.")


def get_default_scorer_target() -> PromptTarget:
    """
    Resolve the default objective scorer chat target.

    First checks the ``TargetRegistry`` for an ``"objective_scorer_chat"`` entry
    (populated by ``TargetInitializer`` from ``OBJECTIVE_SCORER_CHAT_*`` env vars).
    No provider is constructed when the role is unconfigured.

    Returns:
        PromptTarget: The resolved objective scorer chat target.

    Raises:
        ValueError: If the registered target does not support multi-turn.
    """
    return _get_default_chat_target(preferred_target_key="objective_scorer_chat")


def get_default_adversarial_target() -> PromptTarget:
    """
    Resolve the default adversarial chat target.

    First checks ``override_default_adversarial_target`` for a scoped default,
    then the ``TargetRegistry`` for an ``"adversarial_chat"`` entry
    (populated by ``TargetInitializer`` from ``ADVERSARIAL_CHAT_*`` env vars).
    No provider is constructed when the role is unconfigured.

    Returns:
        PromptTarget: The resolved adversarial chat target.

    Raises:
        ValueError: If the registered target does not support multi-turn.
    """
    scoped_target = _adversarial_target_override.get()
    if scoped_target is not None:
        return scoped_target
    return _get_default_chat_target(
        preferred_target_key="adversarial_chat",
        required_capabilities={CapabilityName.MULTI_TURN},
    )


def _get_default_chat_target(
    *,
    preferred_target_key: str,
    required_capabilities: set[CapabilityName] | None = None,
) -> PromptTarget:
    """
    Resolve an explicitly configured chat target from TargetRegistry.

    Resolution order:
    1. ``preferred_target_key`` entry from ``TargetRegistry``

    Args:
        preferred_target_key (str): TargetRegistry key to resolve first.
        required_capabilities (set[CapabilityName] | None): Optional capabilities
            that a resolved target must support.

    Returns:
        PromptTarget: The resolved chat target.

    Raises:
        ValueError: If the resolved target does not satisfy required capabilities.
        ValueError: If the registry entry exists but is not a PromptTarget.
        TargetRoleConfigurationError: If the required role is not registered.
    """
    registry = TargetRegistry.get_registry_singleton()
    target = registry.instances.get(preferred_target_key)
    if target is not None:
        if not isinstance(target, PromptTarget):
            raise ValueError(
                f"Registry entry '{preferred_target_key}' must be a PromptTarget, but got {type(target).__name__}"
            )
        # Check required capabilities first (fail fast)
        if required_capabilities:
            for capability in required_capabilities:
                if not target.capabilities.includes(capability=capability):
                    raise ValueError(f"Registry entry '{preferred_target_key}' must support {capability.value}.")

        return target

    raise TargetRoleConfigurationError(role=preferred_target_key)
