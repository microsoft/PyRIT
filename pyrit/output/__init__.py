# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Output module for displaying attack, scenario, and scorer results.

This module provides:
- **Sink** classes that define where output goes (stdout, file, etc.)
- **PrinterBase** that all printers inherit from
- Domain printers for attack results, scenario results, and scorer information
- **Convenience functions** (e.g., ``print_attack_result_async``)

File names indicate output format (pretty.py = ANSI-colored, markdown.py = Markdown).
Abstract methods inside each printer determine the data source (memory, REST, fixtures).
"""

from pyrit.output.base import PrinterBase
from pyrit.output.helpers import (
    print_attack_result_async,
    print_conversation_async,
    print_scenario_result_async,
    print_score_async,
    print_scorer_async,
)
from pyrit.output.sink import FileSink, IPythonMarkdownSink, OutputFormat, Sink, StdoutSink, get_default_sink

__all__ = [
    "FileSink",
    "get_default_sink",
    "IPythonMarkdownSink",
    "OutputFormat",
    "print_attack_result_async",
    "print_conversation_async",
    "print_scenario_result_async",
    "print_score_async",
    "print_scorer_async",
    "PrinterBase",
    "Sink",
    "StdoutSink",
]
