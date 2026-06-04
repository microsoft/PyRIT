# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Identifiers module for PyRIT components."""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers.atomic_attack_identifier import (
    build_atomic_attack_identifier,
    build_seed_identifier,
)
from pyrit.models.identifiers.class_name_utils import (
    REGISTRY_NAME_PATTERN,
    class_name_to_snake_case,
    snake_case_to_class_name,
    validate_registry_name,
)
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, Identifiable, config_hash
from pyrit.models.identifiers.evaluation_identifier import (
    TARGET_EVAL_PARAM_FALLBACKS,
    TARGET_EVAL_PARAMS,
    AtomicAttackEvaluationIdentifier,
    ChildEvalRule,
    EvaluationIdentifier,
    ObjectiveTargetEvaluationIdentifier,
    ScorerEvaluationIdentifier,
    compute_eval_hash,
    compute_inner_attack_eval_hash,
)
from pyrit.models.identifiers.identifier_filters import IdentifierFilter, IdentifierType

if TYPE_CHECKING:
    # Type-only alias so static checkers can resolve ``from pyrit.models.identifiers import
    # ScorerIdentifier``. At runtime the symbol is served by ``__getattr__`` below so we can
    # emit a one-shot DeprecationWarning per process.
    ScorerIdentifier = ComponentIdentifier

__all__ = [
    "AtomicAttackEvaluationIdentifier",
    "build_atomic_attack_identifier",
    "build_seed_identifier",
    "ChildEvalRule",
    "class_name_to_snake_case",
    "ComponentIdentifier",
    "compute_eval_hash",
    "compute_inner_attack_eval_hash",
    "EvaluationIdentifier",
    "Identifiable",
    "ObjectiveTargetEvaluationIdentifier",
    "REGISTRY_NAME_PATTERN",
    "ScorerEvaluationIdentifier",
    "ScorerIdentifier",
    "snake_case_to_class_name",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "validate_registry_name",
    "config_hash",
    "IdentifierFilter",
    "IdentifierType",
]

# Deprecated rename aliases (pre-#1387 names that were collapsed into ComponentIdentifier).
# Served via ``__getattr__`` rather than as static module attributes so accessing them emits
# a one-shot DeprecationWarning per process. Will be removed in 0.16.0.
_DEPRECATED_RENAME_ALIASES: dict[str, Any] = {
    "ScorerIdentifier": ComponentIdentifier,
}

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_RENAME_ALIASES:
        target = _DEPRECATED_RENAME_ALIASES[name]
        if name not in _warned:
            print_deprecation_message(
                old_item=f"{__name__}.{name}",
                new_item=target,
                removed_in="0.16.0",
            )
            _warned.add(name)
        return target
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
