# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — ``pyrit.identifiers`` was renamed to ``pyrit.models.identifiers`` in 0.14.

This module emits a ``DeprecationWarning`` (one per name per process) on first
access of each public symbol and returns the symbol from its new location.
The shim will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models import identifiers as _new

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
    "REGISTRY_NAME_PATTERN",
    "ScorerEvaluationIdentifier",
    "snake_case_to_class_name",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "validate_registry_name",
]

_warned: set[str] = set()


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers' has no attribute {name!r}")
    if name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.{name}",
            new_item=f"pyrit.models.identifiers.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
