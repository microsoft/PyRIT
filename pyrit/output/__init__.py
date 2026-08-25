# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Output module for displaying attack, scenario, and scorer results.

This module provides:
- **Sink** classes that define where output goes (stdout, file, etc.)
- **PrinterBase** that all printers inherit from
- Domain printers for attack results, scenario results, and scorer information
- **Convenience functions** (e.g., ``output_attack_async``)

File names indicate output format (pretty.py = ANSI-colored, markdown.py = Markdown).
Abstract methods inside each printer determine the data source (memory, REST, fixtures).
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.output.base import PrinterBase
    from pyrit.output.helpers import (
        output_attack_async,
        output_conversation_async,
        output_scenario_async,
        output_score_async,
        output_scorer_async,
    )
    from pyrit.output.sink import FileSink, IPythonMarkdownSink, OutputFormat, Sink, StdoutSink, get_default_sink

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "FileSink": "pyrit.output.sink",
    "get_default_sink": "pyrit.output.sink",
    "IPythonMarkdownSink": "pyrit.output.sink",
    "OutputFormat": "pyrit.output.sink",
    "output_attack_async": "pyrit.output.helpers",
    "output_conversation_async": "pyrit.output.helpers",
    "output_scenario_async": "pyrit.output.helpers",
    "output_score_async": "pyrit.output.helpers",
    "output_scorer_async": "pyrit.output.helpers",
    "PrinterBase": "pyrit.output.base",
    "Sink": "pyrit.output.sink",
    "StdoutSink": "pyrit.output.sink",
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
