# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Streaming attack strategies (barge-in over realtime audio targets)."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.streaming.barge_in import BargeInAttack, BargeInAttackContext

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "BargeInAttack": "pyrit.executor.attack.streaming.barge_in",
    "BargeInAttackContext": "pyrit.executor.attack.streaming.barge_in",
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
