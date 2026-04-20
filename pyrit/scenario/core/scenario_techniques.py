# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario attack technique definitions and registration.

Provides ``SCENARIO_TECHNIQUES`` (the standard catalog) and
``register_scenario_techniques`` (registers specs into the
``AttackTechniqueRegistry`` singleton).

To add a new technique, append a ``TechniqueSpec`` to ``SCENARIO_TECHNIQUES``.
"""

from __future__ import annotations

import logging

from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.registry.object_registries.attack_technique_registry import TechniqueSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenario technique catalog
# ---------------------------------------------------------------------------

SCENARIO_TECHNIQUES: list[TechniqueSpec] = [
    TechniqueSpec(
        name="prompt_sending",
        attack_class=PromptSendingAttack,
        tags=["core", "single_turn", "default"],
    ),
    TechniqueSpec(
        name="role_play",
        attack_class=RolePlayAttack,
        tags=["core", "single_turn"],
        extra_kwargs_builder=lambda _adv: {
            "role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value,
        },
    ),
    TechniqueSpec(
        name="many_shot",
        attack_class=ManyShotJailbreakAttack,
        tags=["core", "multi_turn", "default"],
    ),
    TechniqueSpec(
        name="tap",
        attack_class=TreeOfAttacksWithPruningAttack,
        tags=["core", "multi_turn"],
        accepts_scorer_override=False,
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
    """
    from pyrit.registry import TargetRegistry

    registry = TargetRegistry.get_registry_singleton()
    if "adversarial_chat" in registry:
        target = registry.get("adversarial_chat")
        if not target.capabilities.includes(capability=CapabilityName.MULTI_TURN):
            raise ValueError(
                f"Registry entry 'adversarial_chat' must support multi-turn conversations, "
                f"but {type(target).__name__} does not."
            )
        return target  # type: ignore[return-value]

    return OpenAIChatTarget(temperature=1.2)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_scenario_techniques() -> None:
    """
    Register all ``SCENARIO_TECHNIQUES`` into the ``AttackTechniqueRegistry`` singleton.

    Per-name idempotent: existing entries are not overwritten.

    The registry always stores the **default** adversarial target. Scenarios
    that need a custom adversarial target should pass it at ``factory.create()``
    time via ``attack_adversarial_config_override``.
    """
    from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry

    adversarial_chat = get_default_adversarial_target()

    registry = AttackTechniqueRegistry.get_registry_singleton()
    registry.register_from_specs(SCENARIO_TECHNIQUES, adversarial_chat=adversarial_chat)

