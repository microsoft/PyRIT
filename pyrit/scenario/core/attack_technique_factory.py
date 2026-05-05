# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueFactory — Deferred construction of AttackTechnique instances.

Captures technique-specific configuration at registration time and produces
fresh, fully-constructed attacks when scenario-specific params (objective target,
scorer) become available.
"""

from __future__ import annotations

import inspect
import logging
import sys
import typing
from enum import Enum
from typing import TYPE_CHECKING, Any, Union

from pyrit.identifiers import ComponentIdentifier, Identifiable, build_seed_identifier
from pyrit.scenario.core.attack_technique import AttackTechnique

if TYPE_CHECKING:
    from pyrit.executor.attack import AttackStrategy
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
    )
    from pyrit.models import SeedAttackTechniqueGroup
    from pyrit.prompt_target import PromptChatTarget, PromptTarget

logger = logging.getLogger(__name__)


class ScorerOverridePolicy(str, Enum):
    """Policy for what to do when the scenario's scorer is incompatible with an attack's annotation."""

    SKIP = "skip"
    WARN = "warn"
    RAISE = "raise"


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
        adversarial_config: AttackAdversarialConfig | None = None,
        seed_technique: SeedAttackTechniqueGroup | None = None,
        scorer_override_policy: ScorerOverridePolicy = ScorerOverridePolicy.WARN,
    ) -> None:
        """
        Initialize the factory with a technique-specific configuration.

        Args:
            attack_class: The AttackStrategy subclass to instantiate.
            attack_kwargs: Keyword arguments to pass to the attack constructor.
                Must not include ``objective_target`` (provided at create time)
                or ``attack_adversarial_config`` (use ``adversarial_config`` instead).
            adversarial_config: Optional adversarial chat configuration. Stored
                separately and injected into the attack at ``create()`` time if
                the attack class accepts ``attack_adversarial_config``. Also
                exposed via the ``adversarial_chat`` property for seed-technique
                execution.
            seed_technique: Optional technique seed group to attach to created techniques.
            scorer_override_policy: What to do when a scenario's scorer is incompatible
                with the attack's ``attack_scoring_config`` type annotation. Defaults to WARN.

        Raises:
            TypeError: If any kwarg name is not a valid constructor parameter,
                or if the attack class constructor uses ``**kwargs``.
            ValueError: If ``objective_target`` or ``attack_adversarial_config``
                is included in attack_kwargs.
        """
        self._attack_class = attack_class
        self._attack_kwargs = dict(attack_kwargs) if attack_kwargs else {}
        self._adversarial_config = adversarial_config
        self._seed_technique = seed_technique
        self._scorer_override_policy = scorer_override_policy

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
            ValueError: If ``objective_target`` or ``attack_adversarial_config``
                is included in attack_kwargs.
        """
        if "objective_target" in self._attack_kwargs:
            raise ValueError("objective_target must not be in attack_kwargs — it is provided at create() time.")
        if "attack_adversarial_config" in self._attack_kwargs:
            raise ValueError(
                "attack_adversarial_config must not be in attack_kwargs — use the adversarial_config parameter instead."
            )

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

    @property
    def adversarial_chat(self) -> PromptChatTarget | None:
        """The adversarial chat target baked into this factory, or None."""
        return self._adversarial_config.target if self._adversarial_config else None

    def create(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config_override: AttackScoringConfig | None = None,
        attack_adversarial_config_override: AttackAdversarialConfig | None = None,
        attack_converter_config_override: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Create a fresh AttackTechnique bound to the given target.

        Each call produces a fully independent attack instance by calling the
        real constructor. Config objects frozen at factory construction time are
        deep-copied into every new instance.

        The ``*_override`` parameters let a caller **replace** a config that was
        baked into the factory at construction time.  When ``None`` (the
        default), the factory's original config is kept as-is — so baked-in
        converters, adversarial targets, etc. are preserved automatically.

        Override configs are only forwarded when the attack class constructor
        declares a matching parameter (without the ``_override`` suffix).
        This allows a single call site to safely pass all available overrides
        without breaking attacks that don't support them.

        Some attacks (e.g., TAP) create their own scoring config internally
        when none is provided.  Pass ``None`` (the default) for
        ``attack_scoring_config_override`` to let those attacks use their
        built-in defaults.

        Args:
            objective_target: The target to attack (always required at create time).
            attack_scoring_config_override: When non-None, replaces any scoring
                config baked into the factory.  Only forwarded if the attack
                class constructor accepts ``attack_scoring_config``.
            attack_adversarial_config_override: When non-None, replaces any
                adversarial config baked into the factory.  Only forwarded if
                the attack class constructor accepts ``attack_adversarial_config``.
            attack_converter_config_override: When non-None, replaces any
                converter config baked into the factory.  Only forwarded if
                the attack class constructor accepts ``attack_converter_config``.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.

        Raises:
            ValueError: If ``scorer_override_policy`` is RAISE and the override
                config is incompatible with the attack's type annotation.
        """
        kwargs = dict(self._attack_kwargs)
        kwargs["objective_target"] = objective_target

        # Only forward overrides when the attack class accepts the underlying param
        accepted_params = self._get_accepted_params()
        if attack_scoring_config_override is not None:
            if "attack_scoring_config" not in accepted_params:
                # Attack doesn't accept scoring config at all
                if self._scorer_override_policy == ScorerOverridePolicy.RAISE:
                    raise ValueError(
                        f"Scorer override provided but {self._attack_class.__name__} "
                        f"does not accept 'attack_scoring_config'."
                    )
                elif self._scorer_override_policy == ScorerOverridePolicy.WARN:
                    logger.warning(
                        f"Scorer override provided but {self._attack_class.__name__} "
                        f"does not accept 'attack_scoring_config'. Using attack's built-in scorer instead."
                    )
            else:
                required_type = self._get_scoring_config_type()
                if required_type is None or isinstance(attack_scoring_config_override, required_type):
                    kwargs["attack_scoring_config"] = attack_scoring_config_override
                elif self._scorer_override_policy == ScorerOverridePolicy.RAISE:
                    raise ValueError(
                        f"Scorer override of type {type(attack_scoring_config_override).__name__} is incompatible "
                        f"with {self._attack_class.__name__} which requires {required_type.__name__}."
                    )
                elif self._scorer_override_policy == ScorerOverridePolicy.WARN:
                    logger.warning(
                        f"Scorer override of type {type(attack_scoring_config_override).__name__} is incompatible "
                        f"with {self._attack_class.__name__} (requires {required_type.__name__}). "
                        f"Using attack's built-in scorer instead."
                    )
                # SKIP: silently use attack's built-in scorer
        if "attack_adversarial_config" in accepted_params:
            if attack_adversarial_config_override is not None:
                kwargs["attack_adversarial_config"] = attack_adversarial_config_override
            elif self._adversarial_config is not None:
                kwargs["attack_adversarial_config"] = self._adversarial_config
        if attack_converter_config_override is not None and "attack_converter_config" in accepted_params:
            kwargs["attack_converter_config"] = attack_converter_config_override

        attack = self._attack_class(**kwargs)
        return AttackTechnique(attack=attack, seed_technique=self._seed_technique)

    def _get_accepted_params(self) -> set[str]:
        """Return the set of keyword parameter names accepted by the attack class constructor."""
        sig = inspect.signature(self._attack_class.__init__)
        return {
            name
            for name, param in sig.parameters.items()
            if name != "self"
            and param.kind
            in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        }

    def _get_scoring_config_type(self) -> type | None:
        """
        Introspect the attack class to determine the required type for ``attack_scoring_config``.

        Resolves the type annotation (handling ``Optional[X]`` / ``X | None``) and returns
        the inner concrete type. Returns ``None`` if the annotation is the base
        ``AttackScoringConfig`` or cannot be resolved — meaning any config is accepted.

        Returns:
            The narrowed type if the annotation is narrower than the base, else None.
        """
        from pyrit.executor.attack.core.attack_config import AttackScoringConfig

        try:
            # get_type_hints resolves string annotations from __future__ annotations
            hints = typing.get_type_hints(
                self._attack_class.__init__,
                globalns=getattr(sys.modules.get(self._attack_class.__module__, None), "__dict__", None),
            )
        except Exception:
            return None

        annotation = hints.get("attack_scoring_config")
        if annotation is None:
            return None

        inner = self._unwrap_optional(annotation)
        if inner is None or inner is AttackScoringConfig:
            # Base type or unresolvable — any config is accepted
            return None
        return inner

    @staticmethod
    def _unwrap_optional(annotation: Any) -> type | None:
        """
        Unwrap ``Optional[X]``, ``X | None``, or ``Union[X, None]`` to extract X.

        Returns:
            The inner type X, or None if the annotation cannot be unwrapped to a single type.
        """
        # Handle Python 3.10+ union syntax (types.UnionType): X | None
        origin = typing.get_origin(annotation)
        if origin is Union or (hasattr(annotation, "__args__") and origin is None and hasattr(annotation, "__or__")):
            args = typing.get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return non_none[0]
            return None

        # types.UnionType from PEP 604 at runtime (3.10+)
        if hasattr(annotation, "__args__") and type(annotation).__name__ == "UnionType":
            args = annotation.__args__
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return non_none[0]
            return None

        # Plain type (not Optional)
        if isinstance(annotation, type):
            return annotation

        return None

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
        When a seed technique is present, its seeds are added as
        ``children["technique_seeds"]``.

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        kwargs_for_id = {k: self._serialize_value(v) for k, v in sorted(self._attack_kwargs.items())}
        params: dict[str, Any] = {
            "attack_class": self._attack_class.__name__,
            "kwargs": kwargs_for_id,
        }
        if self._adversarial_config is not None:
            params["adversarial_config"] = self._serialize_value(self._adversarial_config)

        children: dict[str, Any] = {}
        if self._seed_technique is not None:
            technique_seed_ids = [build_seed_identifier(seed) for seed in self._seed_technique.seeds]
            if technique_seed_ids:
                children["technique_seeds"] = technique_seed_ids

        return ComponentIdentifier.of(self, params=params, children=children)
