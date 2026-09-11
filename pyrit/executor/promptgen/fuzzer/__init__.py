# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Fuzzer module for generating adversarial prompts through mutation and crossover operations."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.promptgen.fuzzer.fuzzer import FuzzerContext, FuzzerGenerator, FuzzerResult, FuzzerResultPrinter
    from pyrit.executor.promptgen.fuzzer.fuzzer_converter_base import FuzzerConverter
    from pyrit.executor.promptgen.fuzzer.fuzzer_crossover_converter import FuzzerCrossOverConverter
    from pyrit.executor.promptgen.fuzzer.fuzzer_expand_converter import FuzzerExpandConverter
    from pyrit.executor.promptgen.fuzzer.fuzzer_rephrase_converter import FuzzerRephraseConverter
    from pyrit.executor.promptgen.fuzzer.fuzzer_shorten_converter import FuzzerShortenConverter
    from pyrit.executor.promptgen.fuzzer.fuzzer_similar_converter import FuzzerSimilarConverter

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "FuzzerContext": "pyrit.executor.promptgen.fuzzer.fuzzer",
    "FuzzerConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_converter_base",
    "FuzzerCrossOverConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_crossover_converter",
    "FuzzerExpandConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_expand_converter",
    "FuzzerGenerator": "pyrit.executor.promptgen.fuzzer.fuzzer",
    "FuzzerRephraseConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_rephrase_converter",
    "FuzzerResult": "pyrit.executor.promptgen.fuzzer.fuzzer",
    "FuzzerResultPrinter": "pyrit.executor.promptgen.fuzzer.fuzzer",
    "FuzzerShortenConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_shorten_converter",
    "FuzzerSimilarConverter": "pyrit.executor.promptgen.fuzzer.fuzzer_similar_converter",
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
