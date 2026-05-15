# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Printer module for displaying attack, scenario, and scorer results.

This module provides:
- **Sink** classes that define where output goes (stdout, file, etc.)
- **PrinterBase** that all printers inherit from
- Domain printers for attack results, scenario results, and scorer information
- **Convenience functions** in each subpackage (e.g., ``print_attack_result_async``)

File names indicate output format (pretty.py = ANSI-colored, markdown.py = Markdown).
Abstract methods inside each printer determine the data source (memory, REST, fixtures).
"""
