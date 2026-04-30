# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario attack technique definitions and registration.

Provides ``SCENARIO_TECHNIQUES`` (the static catalog used for strategy enum
construction) and ``register_scenario_techniques`` (registers specs with
resolved live targets into the ``AttackTechniqueRegistry`` singleton).

To add a new technique, append an ``AttackTechniqueSpec`` to
``SCENARIO_TECHNIQUES``. If the technique requires an adversarial chat
target, it will be automatically resolved in ``build_scenario_techniques``
by inspecting the attack class constructor signature. To use a specific
adversarial chat target from ``TargetRegistry``, set
``adversarial_chat_key`` on the spec.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging

from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
    AttackTechniqueSpec,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static technique catalog
# ---------------------------------------------------------------------------
# Used for strategy enum construction (import-time safe — no live targets).
# Live dependencies (e.g. adversarial chat targets) are resolved later by
# build_scenario_techniques() at registration time.

SCENARIO_TECHNIQUES: list[AttackTechniqueSpec] = [
    AttackTechniqueSpec(
        name="prompt_sending",
        attack_class=PromptSendingAttack,
        strategy_tags=["core", "single_turn", "default"],
    ),
    AttackTechniqueSpec(
        name="role_play",
        attack_class=RolePlayAttack,
        strategy_tags=["core", "single_turn"],
        extra_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
    ),
    AttackTechniqueSpec(
        name="many_shot",
        attack_class=ManyShotJailbreakAttack,
        strategy_tags=["core", "multi_turn", "default"],
    ),
    AttackTechniqueSpec(
        name="tap",
        attack_class=TreeOfAttacksWithPruningAttack,
        strategy_tags=["core", "multi_turn"],
        accepts_scorer_override=False,
    ),
    AttackTechniqueSpec(
        name="red_teaming",
        attack_class=RedTeamingAttack,
        strategy_tags=["core", "multi_turn"],
    ),
]


# ---------------------------------------------------------------------------
# Default adversarial target
# ---------------------------------------------------------------------------


def get_default_adversarial_target() -> PromptChatTarget:
    """
    Resolve the default adversarial chat target.

    First checks the ``TargetRegistry`` for an ``"adversarial_chat"`` entry
    (populated by ``TargetInitializer`` from ``ADVERSARIAL_CHAT_*`` env vars).
    Falls back to a plain ``OpenAIChatTarget(temperature=1.2)`` using
    ``@apply_defaults`` resolution.

    Returns:
        PromptChatTarget: The resolved adversarial chat target.

    Raises:
        ValueError: If the registered target does not support multi-turn.
    """
    registry = TargetRegistry.get_registry_singleton()
    if "adversarial_chat" in registry:
        target = registry.get("adversarial_chat")
        if target:
            if not target.capabilities.includes(capability=CapabilityName.MULTI_TURN):
                raise ValueError(
                    f"Registry entry 'adversarial_chat' must support multi-turn conversations, "
                    f"but {type(target).__name__} does not."
                )
            return target

    return OpenAIChatTarget(temperature=1.2)


# ---------------------------------------------------------------------------
# Runtime spec builder
# ---------------------------------------------------------------------------


def build_scenario_techniques() -> list[AttackTechniqueSpec]:
    """
    Return a copy of ``SCENARIO_TECHNIQUES`` with ``adversarial_chat`` baked
    into each spec whose attack class accepts ``attack_adversarial_config``.

    This is a mechanical transform of the static catalog.

    Resolution order for each spec:

    1. If ``adversarial_chat_key`` is set, look it up in ``TargetRegistry``.
       Raises ``ValueError`` if the key is not found.
    2. Otherwise, if the attack class accepts ``attack_adversarial_config``,
       fill in the default from ``get_default_adversarial_target()``.
    3. Otherwise, pass through unchanged.

    Returns:
        list[AttackTechniqueSpec]: Specs ready for registration.

    Raises:
        ValueError: If a spec declares ``adversarial_chat_key`` but the key
            is not found in ``TargetRegistry``.
    """
    default_adversarial: PromptChatTarget | None = None

    result = []
    for spec in SCENARIO_TECHNIQUES:
        if spec.adversarial_chat_key:
            registry = TargetRegistry.get_registry_singleton()
            resolved = registry.get(spec.adversarial_chat_key)
            if resolved is None:
                raise ValueError(
                    f"Technique spec '{spec.name}' references adversarial_chat_key "
                    f"'{spec.adversarial_chat_key}', but no such entry exists in TargetRegistry."
                )
            result.append(
                dataclasses.replace(
                    spec,
                    adversarial_chat=resolved,
                    adversarial_chat_key=None,
                )
            )
        elif "attack_adversarial_config" in inspect.signature(spec.attack_class.__init__).parameters:
            if default_adversarial is None:
                default_adversarial = get_default_adversarial_target()
            result.append(dataclasses.replace(spec, adversarial_chat=default_adversarial))
        else:
            result.append(spec)
    return result


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_scenario_techniques() -> None:
    """
    Register all ``SCENARIO_TECHNIQUES`` into the ``AttackTechniqueRegistry`` singleton.

    Per-name idempotent: existing entries are not overwritten.

    Resolves the default adversarial target, bakes it into the specs that
    require it, then registers the resulting factories.
    """
    specs = build_scenario_techniques()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    registry.register_from_specs(specs)
