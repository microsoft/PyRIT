# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from abc import abstractmethod

from pyrit.models.scenario_result import ScenarioResult
from pyrit.output.base import PrinterBase


class ScenarioResultPrinterBase(PrinterBase):
    """
    Abstract base class for printing scenario results.

    Contains formatting logic. Subclasses may need to provide scorer
    printer implementations via get_scorer_printer().
    """

    @abstractmethod
    async def render_async(self, result: ScenarioResult) -> str:
        """
        Render a scenario result summary and return it as a string.

        Args:
            result (ScenarioResult): The scenario result to summarize.

        Returns:
            str: The rendered scenario result text.
        """

    async def print_summary_async(self, result: ScenarioResult) -> None:
        """
        Deprecated. Use write_async instead.

        Args:
            result (ScenarioResult): The scenario result to summarize.
        """
        warnings.warn("print_summary_async is deprecated, use write_async instead", DeprecationWarning, stacklevel=2)
        await self.write_async(result)
