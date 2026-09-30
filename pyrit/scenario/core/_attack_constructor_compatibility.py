# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import inspect
import logging
import sys
import typing
from enum import Enum
from typing import Any, Union

from pyrit.executor.attack.core.attack_config import AttackScoringConfig

logger = logging.getLogger(__name__)


class ScorerOverridePolicy(str, Enum):
    """Policy for what to do when the scenario's scorer is incompatible with an attack's annotation."""

    SKIP = "skip"
    WARN = "warn"
    RAISE = "raise"


class _ConstructorCompatibilityHelper:
    """Evaluates constructor compatibility and extracts type annotations for an attack class."""

    def __init__(
        self,
        *,
        attack_class: type,
        scorer_override_policy: ScorerOverridePolicy,
    ) -> None:
        self._attack_class = attack_class
        self._scorer_override_policy = scorer_override_policy

        self.accepted_params = self._derive_accepted_params()

    @property
    def scoring_config_type(self) -> type | None:
        """The required ``attack_scoring_config`` subtype, or ``None`` if any config is accepted."""
        return self._derive_scoring_config_type()

    def _derive_accepted_params(self) -> set[str]:
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

    def should_apply_scoring_config(self, attack_scoring_config: AttackScoringConfig) -> bool:
        """
        Determine whether the scoring config should be forwarded to the attack constructor.

        Checks two conditions:
        1. The attack class accepts an ``attack_scoring_config`` parameter.
        2. The provided config is type-compatible with the attack's annotation.

        When either condition fails, the ``scorer_override_policy`` determines
        behavior: RAISE raises ValueError, WARN logs and returns False, SKIP
        silently returns False.

        Args:
            attack_scoring_config: The scoring config to evaluate.

        Returns:
            True if the config should be applied, False otherwise.

        Raises:
            ValueError: If the policy is RAISE and the config cannot be applied.
        """
        if "attack_scoring_config" not in self.accepted_params:
            self._apply_scorer_policy(
                f"Scorer config provided but {self._attack_class.__name__} does not accept 'attack_scoring_config'."
            )
            return False

        if self.scoring_config_type is None or isinstance(attack_scoring_config, self.scoring_config_type):
            return True

        self._apply_scorer_policy(
            f"Scorer config of type {type(attack_scoring_config).__name__} is incompatible "
            f"with {self._attack_class.__name__} (requires {self.scoring_config_type.__name__})."
        )
        return False

    def _apply_scorer_policy(self, message: str) -> None:
        """
        Apply the scorer override policy for an incompatibility.

        Args:
            message: Description of the incompatibility.

        Raises:
            ValueError: If the policy is RAISE.
        """
        if self._scorer_override_policy == ScorerOverridePolicy.RAISE:
            raise ValueError(message)
        if self._scorer_override_policy == ScorerOverridePolicy.WARN:
            logger.warning(message)

    def _derive_scoring_config_type(self) -> type | None:
        """
        Introspect the attack class to determine the required type for ``attack_scoring_config``.

        Resolves the type annotation (handling ``X | None`` / ``X | None``) and returns
        the inner concrete type. Returns ``None`` if the annotation is the base
        ``AttackScoringConfig`` or cannot be resolved — meaning any config is accepted.

        Returns:
            The narrowed type if the annotation is narrower than the base, else None.
        """
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
        if not issubclass(inner, AttackScoringConfig):
            return None
        return inner

    @staticmethod
    def _unwrap_optional(annotation: Any) -> type | None:
        """
        Unwrap a union containing one concrete type and ``None`` to extract the concrete type.

        Returns:
            The inner type X, or None if the annotation cannot be unwrapped to a single type.
        """
        # Handle typing.Union and Optional annotations.
        origin = typing.get_origin(annotation)
        if origin is Union or (hasattr(annotation, "__args__") and origin is None and hasattr(annotation, "__or__")):
            args = typing.get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            candidate = non_none[0] if len(non_none) == 1 else None
            return candidate if isinstance(candidate, type) else None

        # Handle PEP 604 unions (X | None).
        if hasattr(annotation, "__args__") and type(annotation).__name__ == "UnionType":
            args = annotation.__args__
            non_none = [a for a in args if a is not type(None)]
            candidate = non_none[0] if len(non_none) == 1 else None
            return candidate if isinstance(candidate, type) else None

        # Plain type (not Optional)
        if isinstance(annotation, type):
            return annotation

        return None
