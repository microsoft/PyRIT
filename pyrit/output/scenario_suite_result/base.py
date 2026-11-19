# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Rendering contract for a suite execution snapshot and its child scenario results."""

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from pydantic import ValidationError

from pyrit.models import (
    ComponentIdentifier,
    ScenarioResult,
    ScenarioSuiteExecutionEntrySummary,
    ScenarioSuiteExecutionMetadata,
    ScenarioSuiteExecutionSummary,
)
from pyrit.output._derivation import SuiteEntryStatistics, TargetInfo, resolve_target_info, suite_entry_statistics
from pyrit.output.base import PrinterBase


class SuiteReportEntry(NamedTuple):
    """One snapshot entry with its result statistics, if available."""

    entry: ScenarioSuiteExecutionEntrySummary
    statistics: SuiteEntryStatistics | None
    objective_target_identifier: ComponentIdentifier | None = None

    @property
    def error(self) -> str | None:
        """The available submission or child-run error message."""
        return self.entry.error or (self.entry.run.error if self.entry.run else None)


class ScenarioSuiteResultPrinterBase(PrinterBase):
    """
    Render suite results without owning execution.

    Format classes render supplied results. Memory-backed leaf classes override
    ``_get_scenario_results_async`` to load child results the caller omits.
    """

    @abstractmethod
    async def render_async(
        self,
        execution: ScenarioSuiteExecutionSummary,
        *,
        results: Mapping[str, ScenarioResult] | None = None,
    ) -> str:
        """
        Render all entries in the supplied execution snapshot.

        Args:
            execution (ScenarioSuiteExecutionSummary): Suite status and ordered entry identities.
            results (Mapping[str, ScenarioResult] | None): Child results keyed by scenario result ID.
                When None, results are loaded by ``_get_scenario_results_async``. Entries without
                a result are shown as unavailable. Defaults to None.

        Returns:
            str: The rendered suite report.
        """

    async def _get_scenario_results_async(self, *, scenario_result_ids: list[str]) -> Sequence[ScenarioResult]:
        """
        Load child results; format classes have no data source.

        Args:
            scenario_result_ids (list[str]): Non-empty IDs of the submitted child runs.

        Returns:
            Sequence[ScenarioResult]: The available results.
        """
        return ()

    async def _build_entries_async(
        self,
        *,
        execution: ScenarioSuiteExecutionSummary,
        results: Mapping[str, ScenarioResult] | None,
    ) -> list[SuiteReportEntry]:
        """
        Validate attribution and summarize every entry, including ones without results.

        Args:
            execution (ScenarioSuiteExecutionSummary): Snapshot whose status and entries are authoritative.
            results (Mapping[str, ScenarioResult] | None): Child results keyed by scenario result ID,
                or None to load them through ``_get_scenario_results_async``.

        Returns:
            list[SuiteReportEntry]: Entries in declared position order.

        Raises:
            ValueError: If snapshot identities are ambiguous or a result does not belong to its entry.
        """
        entries = sorted(execution.entries, key=lambda entry: entry.entry_position)
        run_ids = [entry.run.scenario_result_id for entry in entries if entry.run is not None]
        for identities in ([entry.entry_id for entry in entries], [entry.entry_position for entry in entries], run_ids):
            if len(identities) != len(set(identities)):
                raise ValueError("Suite snapshot contains duplicate entry IDs, positions, or scenario result IDs.")
        if results is None:
            # An empty ID filter would match every stored scenario result.
            loaded = await self._get_scenario_results_async(scenario_result_ids=run_ids) if run_ids else ()
            results = {str(result.id): result for result in loaded}
        if results.keys() - set(run_ids):
            raise ValueError("Supplied results include scenario result IDs not present in the suite snapshot.")
        report = []
        for entry in entries:
            result = results.get(entry.run.scenario_result_id) if entry.run is not None else None
            if result is not None:
                self._validate_result(execution=execution, entry=entry, result=result)
            report.append(
                SuiteReportEntry(
                    entry=entry,
                    statistics=suite_entry_statistics(result) if result is not None else None,
                    objective_target_identifier=result.objective_target_identifier if result is not None else None,
                )
            )
        return report

    @staticmethod
    def _get_target_info(rows: Sequence[SuiteReportEntry]) -> TargetInfo:
        """
        Resolve the shared suite target from the first available child target identifier.

        Args:
            rows (Sequence[SuiteReportEntry]): Validated entries in suite order.

        Returns:
            TargetInfo: The target's display fields, or None fields when unavailable.
        """
        return resolve_target_info(
            next((row.objective_target_identifier for row in rows if row.objective_target_identifier is not None), None)
        )

    @staticmethod
    def _validate_result(
        *,
        execution: ScenarioSuiteExecutionSummary,
        entry: ScenarioSuiteExecutionEntrySummary,
        result: ScenarioResult,
    ) -> None:
        """
        Match both the result ID and the persisted suite attribution to the snapshot.

        Raises:
            ValueError: If the result ID or suite attribution differs from the snapshot or is malformed.
        """
        expected = ScenarioSuiteExecutionMetadata(
            suite_execution_id=execution.suite_execution_id,
            suite_name=execution.suite_name,
            suite_version=execution.suite_version,
            suite_entry_id=entry.entry_id,
            entry_position=entry.entry_position,
            preset_name=entry.preset_name,
            preset_version=entry.preset_version,
        )
        try:
            actual = ScenarioSuiteExecutionMetadata.from_metadata(result.metadata)
        except ValidationError:
            actual = None
        if entry.run is None or str(result.id) != entry.run.scenario_result_id or actual != expected:
            raise ValueError(f"Scenario result '{result.id}' does not match suite entry '{entry.entry_id}'.")
