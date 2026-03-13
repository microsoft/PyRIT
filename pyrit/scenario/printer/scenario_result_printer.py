# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.models.scenario_result import ScenarioResult


class ScenarioResultPrinter(ABC):
    """
    Abstract base class for printing scenario results.

    This interface defines the contract for printing scenario results in various formats.
    Implementations can render results to console, logs, files, or other outputs.
    """

    @abstractmethod
    async def print_summary_async(self, result: ScenarioResult) -> None:
        """
        Print a summary of the scenario result with per-strategy breakdown.

        Displays:
        - Scenario identification (name, version, PyRIT version)
        - Target information
        - Overall statistics
        - Per-strategy success rates and result counts

        Args:
            result (ScenarioResult): The scenario result to summarize
        """
