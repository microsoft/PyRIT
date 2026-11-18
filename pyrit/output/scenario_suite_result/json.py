# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Structured suite execution summaries."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pyrit.models import ScenarioResult, ScenarioSuiteExecutionSummary
from pyrit.output.scenario_suite_result.base import ScenarioSuiteResultPrinterBase, SuiteReportEntry
from pyrit.output.sink import Sink


class JsonScenarioSuiteResultPrinter(ScenarioSuiteResultPrinterBase):
    """Render the same snapshot entries and statistics as the pretty suite printer."""

    async def render_async(
        self,
        execution: ScenarioSuiteExecutionSummary,
        *,
        results: Mapping[str, ScenarioResult] | None = None,
    ) -> str:
        """
        Render suite status and per-entry results as JSON.

        Args:
            execution (ScenarioSuiteExecutionSummary): Suite status and ordered entry identities.
            results (Mapping[str, ScenarioResult] | None): Child results keyed by scenario result ID.
                When None, results are loaded by the printer's data source, if any. Defaults to None.

        Returns:
            str: The JSON report, with null for unavailable statistics.

        Raises:
            ValueError: If snapshot identities or result attribution are inconsistent.
        """
        rows = await self._build_entries_async(execution=execution, results=results)
        return json.dumps(self._build_payload(execution=execution, rows=rows), indent=2, ensure_ascii=True) + "\n"

    def _build_payload(
        self, *, execution: ScenarioSuiteExecutionSummary, rows: list[SuiteReportEntry]
    ) -> dict[str, Any]:
        """
        Build the structured report without serializing child conversations or internal error types.

        Returns:
            dict[str, Any]: Suite identity, snapshot status, and every entry's available statistics.
        """
        return {
            "view": "suite_overview",
            "suite": execution.model_dump(mode="json", exclude={"entries"}),
            "target": self._get_target_info(rows)._asdict(),
            "entries": [
                {
                    **row.entry.model_dump(mode="json", exclude={"run", "error", "error_type"}),
                    "scenario_result_id": row.entry.run.scenario_result_id if row.entry.run is not None else None,
                    "error": row.error,
                    "results_available": row.statistics is not None,
                    "statistics": row.statistics._asdict() if row.statistics is not None else None,
                }
                for row in rows
            ],
        }


class JsonScenarioSuiteResultMemoryPrinter(JsonScenarioSuiteResultPrinter):
    """JSON suite printer that loads omitted child results from CentralMemory."""

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the JSON suite printer with a CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
        """
        super().__init__(sink=sink)
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def _get_scenario_results_async(self, *, scenario_result_ids: list[str]) -> Sequence[ScenarioResult]:
        """
        Load child results from CentralMemory.

        Args:
            scenario_result_ids (list[str]): Non-empty IDs of the submitted child runs.

        Returns:
            Sequence[ScenarioResult]: The stored results.
        """
        return await self._memory.get_scenario_results_async(scenario_result_ids=scenario_result_ids)
