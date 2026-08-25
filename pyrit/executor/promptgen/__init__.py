# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Prompt generator strategy imports."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.promptgen.anecdoctor import AnecdoctorContext, AnecdoctorGenerator, AnecdoctorResult
    from pyrit.executor.promptgen.core import (
        PromptGeneratorStrategy,
        PromptGeneratorStrategyContext,
        PromptGeneratorStrategyResult,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AnecdoctorContext": "pyrit.executor.promptgen.anecdoctor",
    "AnecdoctorGenerator": "pyrit.executor.promptgen.anecdoctor",
    "AnecdoctorResult": "pyrit.executor.promptgen.anecdoctor",
    "PromptGeneratorStrategy": "pyrit.executor.promptgen.core",
    "PromptGeneratorStrategyContext": "pyrit.executor.promptgen.core",
    "PromptGeneratorStrategyResult": "pyrit.executor.promptgen.core",
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
