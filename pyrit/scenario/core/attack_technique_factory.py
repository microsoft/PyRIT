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
        attack_class: type[AttackStrategy[Any, Any]],
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
            TypeError: If any kwarg name is not a valid constructor parameter,
                or if the attack class constructor uses ``**kwargs``.
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
            TypeError: If any kwarg name is not a valid constructor parameter,
                or if the constructor uses ``**kwargs`` (all parameters must be
                explicitly named).
            ValueError: If ``objective_target`` is included in attack_kwargs.
        """
        if "objective_target" in self._attack_kwargs:
            raise ValueError("objective_target must not be in attack_kwargs — it is provided at create() time.")

        sig = inspect.signature(self._attack_class.__init__)

        # Reject constructors that accept **kwargs — we require explicitly named
        # parameters so that validation is meaningful.
        has_var_keyword = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values())
        if has_var_keyword:
            raise TypeError(
                f"{self._attack_class.__name__}.__init__ accepts **kwargs, which prevents "
                f"parameter validation. All attack constructor parameters must be explicitly named."
            )

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
    def attack_class(self) -> type[AttackStrategy[Any, Any]]:
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
        attack_scoring_config: AttackScoringConfig,
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
            attack_scoring_config: Scoring configuration for the attack.
            attack_adversarial_config: Optional adversarial configuration.
                Overrides any adversarial config in the frozen kwargs.
            attack_converter_config: Optional converter configuration.
                Overrides any converter config in the frozen kwargs.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.
        """
        kwargs = copy.deepcopy(self._attack_kwargs)
        kwargs["objective_target"] = objective_target
        kwargs["attack_scoring_config"] = attack_scoring_config
        if attack_adversarial_config is not None:
            kwargs["attack_adversarial_config"] = attack_adversarial_config
        if attack_converter_config is not None:
            kwargs["attack_converter_config"] = attack_converter_config

        attack = self._attack_class(**kwargs)
        return AttackTechnique(attack=attack, seed_technique=self._seed_technique)

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """
        Convert a value to a JSON-safe representation for identifier hashing.

        Primitives are included directly. Identifiable objects contribute their
        hash. Collections are serialized recursively. Other types fall back to
        their qualified class name.

        Returns:
            Any: A JSON-serializable representation of the value.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [AttackTechniqueFactory._serialize_value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): AttackTechniqueFactory._serialize_value(v) for k, v in sorted(value.items())}
        if isinstance(value, Identifiable):
            return value.get_identifier().hash
        return f"<{type(value).__qualname__}>"

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity for this factory.

        Includes the attack class name and kwargs with their serialized values
        so that factories with different configurations produce different hashes.

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        kwargs_for_id = {k: self._serialize_value(v) for k, v in sorted(self._attack_kwargs.items())}
        return ComponentIdentifier.of(
            self,
            params={
                "attack_class": self._attack_class.__name__,
                "kwargs": kwargs_for_id,
            },
        )
