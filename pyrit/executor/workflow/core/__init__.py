# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Workflow components and strategies used by the PyRIT executor."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.workflow.core.workflow_strategy import WorkflowContext, WorkflowResult, WorkflowStrategy

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "WorkflowContext": "pyrit.executor.workflow.core.workflow_strategy",
    "WorkflowResult": "pyrit.executor.workflow.core.workflow_strategy",
    "WorkflowStrategy": "pyrit.executor.workflow.core.workflow_strategy",
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
