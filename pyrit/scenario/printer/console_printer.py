# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.printer.scenario_result.console import ConsoleScenarioPrinterBase
from pyrit.printer.scorer.base import ScorerPrinterBase
from pyrit.score.printer import ConsoleScorerPrinter


class ConsoleScenarioResultPrinter(ConsoleScenarioPrinterBase):
    """
    Framework console printer for scenario results.

    Thin subclass that provides the framework's ConsoleScorerPrinter
    for scorer information. All formatting logic lives in ConsoleScenarioPrinterBase.
    """

    def __init__(
        self,
        *,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        scorer_printer: Optional[ScorerPrinterBase] = None,
    ) -> None:
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            scorer_printer (Optional[ScorerPrinterBase]): Printer for scorer information.
                If not provided, a ConsoleScorerPrinter with matching settings is created.
        """
        if scorer_printer is None:
            scorer_printer = ConsoleScorerPrinter(indent_size=indent_size, enable_colors=enable_colors)
        super().__init__(
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            scorer_printer=scorer_printer,
        )
