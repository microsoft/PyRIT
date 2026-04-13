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
from typing import TYPE_CHECKING, Optional, Union

from pyrit.identifiers import ComponentIdentifier
from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
    )
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


class AttackTechniqueRegistry(BaseInstanceRegistry["AttackTechniqueFactory", ComponentIdentifier]):
    """
    Singleton registry of reusable attack technique factories.

    Scenarios and initializers register technique factories (capturing
    technique-specific config). Scenarios retrieve them via ``create_technique()``,
    which calls the factory with the scenario's objective target and scorer.
    """

    @classmethod
    def get_registry_singleton(cls) -> AttackTechniqueRegistry:
        """
        Get the singleton instance of the AttackTechniqueRegistry.

        Returns:
            The singleton AttackTechniqueRegistry instance.
        """
        return super().get_registry_singleton()  # type: ignore[return-value]

    def register_technique(
        self,
        *,
        name: str,
        factory: AttackTechniqueFactory,
        tags: Optional[Union[dict[str, str], list[str]]] = None,
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

    def create_technique(
        self,
        name: str,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig | None = None,
        attack_adversarial_config: AttackAdversarialConfig | None = None,
        attack_converter_config: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Retrieve a factory by name and produce a fresh attack technique.

        Args:
            name: The registry name of the technique.
            objective_target: The target to attack.
            attack_scoring_config: Optional scoring configuration override.
            attack_adversarial_config: Optional adversarial configuration override.
            attack_converter_config: Optional converter configuration override.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.

        Raises:
            KeyError: If no technique is registered with the given name.
        """
        factory = self.get(name)
        if factory is None:
            raise KeyError(f"No technique registered with name '{name}'")
        return factory.create(
            objective_target=objective_target,
            attack_scoring_config=attack_scoring_config,
            attack_adversarial_config=attack_adversarial_config,
            attack_converter_config=attack_converter_config,
        )

    def _build_metadata(self, name: str, instance: AttackTechniqueFactory) -> ComponentIdentifier:
        """
        Build metadata for a technique factory.

        Args:
            name: The registry name of the factory.
            instance: The factory instance.

        Returns:
            ComponentIdentifier: The factory's identifier.
        """
        return instance.get_identifier()
