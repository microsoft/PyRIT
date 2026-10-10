# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Core executor module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.core.config import StrategyConverterConfig
    from pyrit.executor.core.strategy import (
        Strategy,
        StrategyContext,
        StrategyEvent,
        StrategyEventData,
        StrategyEventHandler,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "Strategy": "pyrit.executor.core.strategy",
    "StrategyEventHandler": "pyrit.executor.core.strategy",
    "StrategyEvent": "pyrit.executor.core.strategy",
    "StrategyEventData": "pyrit.executor.core.strategy",
    "StrategyContext": "pyrit.executor.core.strategy",
    "StrategyConverterConfig": "pyrit.executor.core.config",
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
