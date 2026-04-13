# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueFactory — Deferred construction of AttackTechnique instances.

Captures technique-specific configuration at registration time and produces
fresh, fully-constructed attacks when scenario-specific params (objective target,
scorer) become available.
"""

from __future__ import annotations

import copy
import inspect
from typing import TYPE_CHECKING, Any

from pyrit.identifiers import ComponentIdentifier, Identifiable
from pyrit.scenario.core.attack_technique import AttackTechnique

if TYPE_CHECKING:
    from pyrit.executor.attack import AttackStrategy
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
    )
    from pyrit.models import SeedAttackTechniqueGroup
    from pyrit.prompt_target import PromptTarget


class AttackTechniqueFactory(Identifiable):
    """
    A factory that produces AttackTechnique instances on demand.

    Captures technique-specific configuration (converters, adversarial config,
    tree depth, etc.) at registration time. Produces fresh, fully-constructed
    attacks by calling the real constructor with the captured params plus
    scenario-specific objective_target and scoring config.

    Validates kwargs against the attack class constructor signature at
    construction time, catching typos and incompatible parameter names early.
    """

    def __init__(
        self,
        *,
        attack_class: type[AttackStrategy],
        attack_kwargs: dict[str, Any] | None = None,
        seed_technique: SeedAttackTechniqueGroup | None = None,
    ) -> None:
        """
        Initialize the factory with a technique-specific configuration.

        Args:
            attack_class: The AttackStrategy subclass to instantiate.
            attack_kwargs: Keyword arguments to pass to the attack constructor.
                Must not include ``objective_target`` (provided at create time).
            seed_technique: Optional technique seed group to attach to created techniques.

        Raises:
            TypeError: If any kwarg name is not a valid constructor parameter.
            ValueError: If ``objective_target`` is included in attack_kwargs.
        """
        self._attack_class = attack_class
        self._attack_kwargs = copy.deepcopy(attack_kwargs) if attack_kwargs else {}
        self._seed_technique = seed_technique

        self._validate_kwargs()

    def _validate_kwargs(self) -> None:
        """
        Validate that all kwargs are valid parameters for the attack class constructor.

        Uses ``inspect.signature`` on the attack class ``__init__``, which works through
        the ``@apply_defaults`` decorator (it uses ``functools.wraps``).

        Raises:
            TypeError: If any kwarg name is not a valid constructor parameter.
            ValueError: If ``objective_target`` is included in attack_kwargs.
        """
        if "objective_target" in self._attack_kwargs:
            raise ValueError(
                "objective_target must not be in attack_kwargs — "
                "it is provided at create() time."
            )

        sig = inspect.signature(self._attack_class.__init__)
        valid_params = {
            name
            for name, param in sig.parameters.items()
            if name != "self"
            and param.kind
            in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        }

        invalid = set(self._attack_kwargs) - valid_params
        if invalid:
            raise TypeError(
                f"Invalid kwargs for {self._attack_class.__name__}: {sorted(invalid)}. "
                f"Valid parameters: {sorted(valid_params)}"
            )

    @property
    def attack_class(self) -> type[AttackStrategy]:
        """The attack strategy class this factory produces."""
        return self._attack_class

    @property
    def seed_technique(self) -> SeedAttackTechniqueGroup | None:
        """The optional technique seed group."""
        return self._seed_technique

    def create(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig | None = None,
        attack_adversarial_config: AttackAdversarialConfig | None = None,
        attack_converter_config: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Create a fresh AttackTechnique bound to the given target and scorer.

        Each call produces a fully independent attack instance by calling the
        real constructor. Config objects are deep-copied to prevent shared
        mutable state between instances.

        Args:
            objective_target: The target to attack.
            attack_scoring_config: Optional scoring configuration.
                Overrides any scoring config in the frozen kwargs.
            attack_adversarial_config: Optional adversarial configuration.
                Overrides any adversarial config in the frozen kwargs.
            attack_converter_config: Optional converter configuration.
                Overrides any converter config in the frozen kwargs.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.
        """
        kwargs = copy.deepcopy(self._attack_kwargs)
        kwargs["objective_target"] = objective_target
        if attack_scoring_config is not None:
            kwargs["attack_scoring_config"] = attack_scoring_config
        if attack_adversarial_config is not None:
            kwargs["attack_adversarial_config"] = attack_adversarial_config
        if attack_converter_config is not None:
            kwargs["attack_converter_config"] = attack_converter_config

        attack = self._attack_class(**kwargs)
        return AttackTechnique(attack=attack, seed_technique=self._seed_technique)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity for this factory.

        Includes the attack class name and the sorted kwarg keys so that
        factories with different configurations are distinguishable.

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        kwargs_summary = sorted(self._attack_kwargs.keys())
        return ComponentIdentifier.of(
            self,
            params={
                "attack_class": self._attack_class.__name__,
                "kwargs_keys": kwargs_summary,
            },
        )
