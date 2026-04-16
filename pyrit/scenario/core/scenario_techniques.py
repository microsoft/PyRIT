# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario attack technique definitions and registration.

Provides ``SCENARIO_TECHNIQUES`` (the standard catalog) and
``ScenarioTechniqueRegistrar`` (registers specs into the
``AttackTechniqueRegistry`` singleton).

To add a new technique, append a ``TechniqueSpec`` to ``SCENARIO_TECHNIQUES``.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import TechniqueSpec
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenario technique catalog
# ---------------------------------------------------------------------------

SCENARIO_TECHNIQUES: list[TechniqueSpec] = [
    TechniqueSpec(
        name="prompt_sending",
        attack_class=PromptSendingAttack,
        tags=["single_turn"],
    ),
    TechniqueSpec(
        name="role_play",
        attack_class=RolePlayAttack,
        tags=["single_turn"],
        extra_kwargs_builder=lambda _adv: {
            "role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value,
        },
    ),
    TechniqueSpec(
        name="many_shot",
        attack_class=ManyShotJailbreakAttack,
        tags=["multi_turn"],
    ),
    TechniqueSpec(
        name="tap",
        attack_class=TreeOfAttacksWithPruningAttack,
        tags=["multi_turn"],
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
        return registry.get("adversarial_chat")

    return OpenAIChatTarget(temperature=1.2)


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------


class ScenarioTechniqueRegistrar:
    """
    Registers ``TechniqueSpec`` entries into the ``AttackTechniqueRegistry``.

    Holds shared defaults (e.g. ``adversarial_chat``) so they're set once
    and applied to every technique that needs them.

    Typical usage from a scenario::

        ScenarioTechniqueRegistrar(adversarial_chat=self._adversarial_chat).register()
    """

    def __init__(self, *, adversarial_chat: PromptChatTarget | None = None) -> None:
        """
        Args:
            adversarial_chat: Shared adversarial chat target for techniques
                that require one. Defaults to ``get_default_adversarial_target()``.
        """
        self._adversarial_chat = adversarial_chat

    @property
    def adversarial_chat(self) -> PromptChatTarget:
        """Resolve the adversarial chat target (custom or default)."""
        if self._adversarial_chat is None:
            self._adversarial_chat = get_default_adversarial_target()
        return self._adversarial_chat

    def build_factory(self, spec: TechniqueSpec) -> AttackTechniqueFactory:
        """
        Build an ``AttackTechniqueFactory`` from a ``TechniqueSpec``.

        Automatically injects ``AttackAdversarialConfig`` when the attack
        class accepts ``attack_adversarial_config`` as a constructor parameter.

        Args:
            spec: The technique specification.

        Returns:
            AttackTechniqueFactory: A factory ready for registration.
        """
        kwargs: dict[str, Any] = {}

        if self._accepts_adversarial(spec.attack_class):
            kwargs["attack_adversarial_config"] = AttackAdversarialConfig(target=self.adversarial_chat)

        if spec.extra_kwargs_builder:
            kwargs.update(spec.extra_kwargs_builder(self.adversarial_chat))

        return AttackTechniqueFactory(
            attack_class=spec.attack_class,
            attack_kwargs=kwargs or None,
        )

    @staticmethod
    def _accepts_adversarial(attack_class: type) -> bool:
        """Check if an attack class accepts ``attack_adversarial_config``."""
        sig = inspect.signature(attack_class.__init__)
        return "attack_adversarial_config" in sig.parameters

    def register(
        self,
        *,
        techniques: list[TechniqueSpec] | None = None,
        registry: "AttackTechniqueRegistry | None" = None,
    ) -> None:
        """
        Register technique specs into the registry.

        Per-name idempotent: existing entries are not overwritten.

        Args:
            techniques: Specs to register. Defaults to ``SCENARIO_TECHNIQUES``.
            registry: Registry instance. Defaults to the singleton.
        """
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry

        if registry is None:
            registry = AttackTechniqueRegistry.get_registry_singleton()
        if techniques is None:
            techniques = SCENARIO_TECHNIQUES

        for spec in techniques:
            if spec.name not in registry:
                factory = self.build_factory(spec)
                registry.register_technique(name=spec.name, factory=factory, tags=spec.tags)

        logger.debug("Technique registration complete (%d total in registry)", len(registry))

