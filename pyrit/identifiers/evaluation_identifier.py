# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deprecation shim — moved to pyrit.models.identifiers.evaluation_identifier in 0.14."""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers import evaluation_identifier as _new

if TYPE_CHECKING:
    from pyrit.models.identifiers.evaluation_identifier import (
        TARGET_EVAL_PARAM_FALLBACKS,
        TARGET_EVAL_PARAMS,
        AtomicAttackEvaluationIdentifier,
        ChildEvalRule,
        EvaluationIdentifier,
        ScorerEvaluationIdentifier,
        compute_eval_hash,
    )

__all__ = [
    "AtomicAttackEvaluationIdentifier",
    "ChildEvalRule",
    "compute_eval_hash",
    "EvaluationIdentifier",
    "ScorerEvaluationIdentifier",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
]

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers.evaluation_identifier' has no attribute {name!r}")
    if name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.evaluation_identifier.{name}",
            new_item=f"pyrit.models.identifiers.evaluation_identifier.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
