# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Scenario for executing one catalog technique."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios._dynamic_techniques import ExecuteTechnique
    from pyrit.scenario.scenarios.technique.execute import Execute

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "Execute": "pyrit.scenario.scenarios.technique.execute",
    "ExecuteTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(name=name, module_name=__name__, module_globals=globals(), exports=_LAZY_EXPORTS)


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
