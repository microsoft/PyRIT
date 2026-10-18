# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Compound attack strategies that orchestrate multiple inner attack strategies."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.compound.sequential_attack import (
        SequenceCompletionPolicy,
        SequentialAttack,
        SequentialAttackResult,
        SequentialChildAttack,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "SequenceCompletionPolicy": "pyrit.executor.attack.compound.sequential_attack",
    "SequentialAttack": "pyrit.executor.attack.compound.sequential_attack",
    "SequentialAttackResult": "pyrit.executor.attack.compound.sequential_attack",
    "SequentialChildAttack": "pyrit.executor.attack.compound.sequential_attack",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
