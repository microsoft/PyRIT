# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueRegistry — Singleton registry of reusable attack technique factories.

Scenarios and initializers register technique factories (capturing technique-specific
config). Scenarios retrieve them via ``create_technique()``, which calls the factory
with the scenario's objective target and scorer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from pyrit.registry.object_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
    )
    from pyrit.prompt_target import PromptChatTarget, PromptTarget
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TechniqueSpec:
    """
    Declarative definition of an attack technique.

    Each spec describes one registrable technique. The registrar converts
    specs into ``AttackTechniqueFactory`` instances and registers them.

    Whether a technique receives an ``AttackAdversarialConfig`` is determined
    automatically: the registrar inspects the attack class constructor and
    injects one when ``attack_adversarial_config`` is an accepted parameter.

    Args:
        name: Registry name (must match the strategy enum value).
        attack_class: The ``AttackStrategy`` subclass.
        tags: Classification tags (e.g. ``["single_turn"]``).
        extra_kwargs_builder: Optional callback that returns additional kwargs
            for the factory. Receives the resolved adversarial target.
    """

    name: str
    attack_class: type
    tags: list[str] = field(default_factory=list)
    extra_kwargs_builder: Callable[["PromptChatTarget"], dict[str, Any]] | None = None


class AttackTechniqueRegistry(BaseInstanceRegistry["AttackTechniqueFactory"]):
    """
    Singleton registry of reusable attack technique factories.

    Scenarios and initializers register technique factories (capturing
    technique-specific config). Scenarios retrieve them via ``create_technique()``,
    which calls the factory with the scenario's objective target and scorer.
    """

    def register_technique(
        self,
        *,
        name: str,
        factory: AttackTechniqueFactory,
        tags: dict[str, str] | list[str] | None = None,
    ) -> None:
        """
        Register an attack technique factory.

        Args:
            name: The registry name for this technique.
            factory: The factory that produces attack techniques.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
        """
        self.register(factory, name=name, tags=tags)
        logger.debug(f"Registered attack technique factory: {name} ({factory.attack_class.__name__})")

    def get_factories(self) -> dict[str, "AttackTechniqueFactory"]:
        """
        Return all registered factories as a name→factory dict.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        return {name: entry.instance for name, entry in self._registry_items.items()}

    def create_technique(
        self,
        name: str,
        *,
        objective_target: PromptTarget,
        attack_scoring_config_override: AttackScoringConfig | None = None,
        attack_adversarial_config_override: AttackAdversarialConfig | None = None,
        attack_converter_config_override: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Retrieve a factory by name and produce a fresh attack technique.

        Args:
            name: The registry name of the technique.
            objective_target: The target to attack.
            attack_scoring_config_override: When non-None, replaces any scoring
                config baked into the factory.
            attack_adversarial_config_override: When non-None, replaces any
                adversarial config baked into the factory.
            attack_converter_config_override: When non-None, replaces any
                converter config baked into the factory.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.

        Raises:
            KeyError: If no technique is registered with the given name.
        """
        entry = self._registry_items.get(name)
        if entry is None:
            raise KeyError(f"No technique registered with name '{name}'")
        return entry.instance.create(
            objective_target=objective_target,
            attack_scoring_config_override=attack_scoring_config_override,
            attack_adversarial_config_override=attack_adversarial_config_override,
            attack_converter_config_override=attack_converter_config_override,
        )
