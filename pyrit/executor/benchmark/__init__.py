# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Benchmark modules."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.benchmark.fairness_bias import FairnessBiasBenchmark, FairnessBiasBenchmarkContext
    from pyrit.executor.benchmark.question_answering import (
        QuestionAnsweringBenchmark,
        QuestionAnsweringBenchmarkContext,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "FairnessBiasBenchmarkContext": "pyrit.executor.benchmark.fairness_bias",
    "FairnessBiasBenchmark": "pyrit.executor.benchmark.fairness_bias",
    "QuestionAnsweringBenchmarkContext": "pyrit.executor.benchmark.question_answering",
    "QuestionAnsweringBenchmark": "pyrit.executor.benchmark.question_answering",
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
