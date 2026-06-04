# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backward-compatibility shim.

``AttackResult`` and ``AttackOutcome`` now live in ``pyrit.models.results``.
Import from there (or from ``pyrit.models``) instead. This module re-exports the
public names so existing ``from pyrit.models.attack_result import ...`` imports
keep working.
"""

from typing import Any

from pyrit.models.results import attack_result as _attack_result
from pyrit.models.results.attack_result import AttackOutcome, AttackResult, AttackResultT


def __getattr__(name: str) -> Any:
    return getattr(_attack_result, name)


__all__ = ["AttackOutcome", "AttackResult", "AttackResultT"]
