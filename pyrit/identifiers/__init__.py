# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — ``pyrit.identifiers`` was renamed to ``pyrit.models.identifiers`` in 0.14.

This module emits a ``DeprecationWarning`` (one per name per process) on first
access of each public symbol and returns the symbol from its new location.
The shim will be removed in 0.16.0.
"""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models import identifiers as _new

if TYPE_CHECKING:
    # Re-export the public names so static type checkers can resolve
    # ``from pyrit.identifiers import X``. At runtime the names are still
    # served lazily by ``__getattr__`` (which fires the DeprecationWarning).
    from pyrit.models.identifiers import (
        REGISTRY_NAME_PATTERN,
        TARGET_EVAL_PARAM_FALLBACKS,
        TARGET_EVAL_PARAMS,
        AtomicAttackEvaluationIdentifier,
        ChildEvalRule,
        ComponentIdentifier,
        EvaluationIdentifier,
        Identifiable,
        IdentifierFilter,
        IdentifierType,
        ObjectiveTargetEvaluationIdentifier,
        ScorerEvaluationIdentifier,
        ScorerIdentifier,
        build_atomic_attack_identifier,
        build_seed_identifier,
        class_name_to_snake_case,
        compute_eval_hash,
        config_hash,
        snake_case_to_class_name,
        validate_registry_name,
    )

__all__ = [
    "AtomicAttackEvaluationIdentifier",
    "build_atomic_attack_identifier",
    "build_seed_identifier",
    "ChildEvalRule",
    "class_name_to_snake_case",
    "ComponentIdentifier",
    "compute_eval_hash",
    "config_hash",
    "EvaluationIdentifier",
    "Identifiable",
    "IdentifierFilter",
    "IdentifierType",
    "ObjectiveTargetEvaluationIdentifier",
    "REGISTRY_NAME_PATTERN",
    "ScorerEvaluationIdentifier",
    "ScorerIdentifier",
    "snake_case_to_class_name",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "validate_registry_name",
]

_warned: set[str] = set()

# Names that have an additional deprecation warning at the new pyrit.models.identifiers path —
# for these, skip the shim's path-migration warning and let the deeper module's __getattr__
# emit the (more informative) name-deprecation warning pointing at the actual replacement
# class. Otherwise users would see two warnings on a single access.
_NAMES_DEPRECATED_AT_NEW_PATH = frozenset({"ScorerIdentifier"})


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers' has no attribute {name!r}")
    if name not in _NAMES_DEPRECATED_AT_NEW_PATH and name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.{name}",
            new_item=f"pyrit.models.identifiers.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
