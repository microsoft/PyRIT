# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Human-readable suite execution summaries."""

from collections.abc import Mapping, Sequence

from colorama import Fore, Style

from pyrit.models import ScenarioResult, ScenarioSuiteExecutionSummary
from pyrit.output._formatting import _PrettyPrinterMixin
from pyrit.output.scenario_suite_result.base import ScenarioSuiteResultPrinterBase, SuiteReportEntry
from pyrit.output.sink import Sink

_STATE_COLORS = {
    "COMPLETED": Fore.GREEN,
    "FAILED": Fore.RED,
    "SUBMISSION_FAILED": Fore.RED,
}


class PrettyScenarioSuiteResultPrinter(_PrettyPrinterMixin, ScenarioSuiteResultPrinterBase):
    """Pretty printer for suite executions, styled like the scenario result report."""

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
    ) -> None:
        """
        Initialize the pretty suite printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Width of separators. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

    async def render_async(
        self,
        execution: ScenarioSuiteExecutionSummary,
        *,
        results: Mapping[str, ScenarioResult] | None = None,
    ) -> str:
        """
        Render the suite snapshot and per-entry statistics.

        Args:
            execution (ScenarioSuiteExecutionSummary): Suite status and ordered entry identities.
            results (Mapping[str, ScenarioResult] | None): Child results keyed by scenario result ID.
                When None, results are loaded by the printer's data source, if any. Defaults to None.

        Returns:
            str: The rendered suite report.

        Raises:
            ValueError: If snapshot identities or result attribution are inconsistent.
        """
        rows = await self._build_entries_async(execution=execution, results=results)
        lines = [self._render_header(execution)]

        lines.append(self._render_section_header("Suite Information"))
        lines.append(self._format_colored(f"{self._indent}📋 Suite Details", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}• Name: {execution.suite_name}", Fore.CYAN))
        lines.append(
            self._format_colored(f"{self._indent * 2}• Execution ID: {execution.suite_execution_id}", Fore.CYAN)
        )
        lines.append(self._format_colored(f"{self._indent * 2}• Origin: {execution.suite_origin}", Fore.CYAN))
        lines.append(
            self._format_colored(f"{self._indent * 2}• Suite Version: {execution.suite_version or 'N/A'}", Fore.CYAN)
        )
        lines.append(
            self._format_colored(
                f"{self._indent * 2}• Last Updated: {execution.updated_at.isoformat(sep=' ', timespec='seconds')}",
                Fore.CYAN,
            )
        )
        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}🎯 Target Information", Style.BRIGHT))
        target = self._get_target_info(rows)
        lines.append(self._format_colored(f"{self._indent * 2}• Target Type: {target.type or 'Unknown'}", Fore.CYAN))
        lines.append(self._format_colored(f"{self._indent * 2}• Target Model: {target.model or 'Unknown'}", Fore.CYAN))
        lines.append(
            self._format_colored(f"{self._indent * 2}• Target Endpoint: {target.endpoint or 'Unknown'}", Fore.CYAN)
        )
        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}📈 Summary", Style.BRIGHT))
        lines.append(
            self._format_colored(
                f"{self._indent * 2}• Status: {execution.status.value}",
                _STATE_COLORS.get(execution.status.value, Fore.YELLOW),
            )
        )
        lines.append(self._format_colored(f"{self._indent * 2}• Total Entries: {len(rows)}", Fore.GREEN))
        available = sum(row.statistics is not None for row in rows)
        lines.append(
            self._format_colored(f"{self._indent * 2}• Results Available: {available} of {len(rows)}", Fore.GREEN)
        )

        lines.append(self._render_section_header("Per-Entry Breakdown"))
        if not rows:
            lines.append(self._format_colored(f"{self._indent}No suite entries.", Fore.YELLOW))
        lines.extend(self._render_entry(row) for row in rows)
        lines.append(self._render_footer())
        return "".join(lines)

    def _render_header(self, execution: ScenarioSuiteExecutionSummary) -> str:
        """
        Render the boxed suite title.

        Returns:
            str: The rendered header.
        """
        return self._render_boxed_header(f"📦 SCENARIO SUITE RESULTS: {execution.suite_name}")

    def _render_entry(self, row: SuiteReportEntry) -> str:
        """
        Render one entry, including entries without results.

        Returns:
            str: The rendered entry block.
        """
        entry, counts = row.entry, row.statistics
        bullet = f"{self._indent * 2}• "
        lines = [
            "\n",
            self._format_colored(f"{self._indent}🔸 {entry.entry_position}. {entry.preset_name}", Style.BRIGHT),
            self._format_colored(
                f"{bullet}State: {entry.state.value}", _STATE_COLORS.get(entry.state.value, Fore.YELLOW)
            ),
        ]
        if entry.run is not None:
            lines.append(self._format_colored(f"{bullet}Scenario Result ID: {entry.run.scenario_result_id}", Fore.CYAN))
        if counts is None:
            lines.append(self._format_colored(f"{bullet}Results: unavailable", Fore.YELLOW))
        else:
            executions = str(counts.completed) if counts.planned is None else f"{counts.completed} of {counts.planned}"
            lines.append(self._format_colored(f"{bullet}Objective Executions: {executions}", Fore.GREEN))
            if counts.successes is not None:
                lines.append(
                    self._format_colored(
                        f"{bullet}Outcomes: {counts.successes} succeeded, {counts.failures} failed, "
                        f"{counts.errors} errors, {counts.undetermined} undetermined",
                        Fore.GREEN,
                    )
                )
            if counts.success_percentage is not None:
                lines.append(
                    self._format_colored(
                        f"{bullet}Success Rate: {counts.success_percentage}%",
                        self._get_rate_color(counts.success_percentage),
                    )
                )
        if row.error:
            lines.append(self._format_colored(f"{bullet}Error: {row.error}", Fore.RED))
        return "".join(lines)


class PrettyScenarioSuiteResultMemoryPrinter(PrettyScenarioSuiteResultPrinter):
    """Pretty suite printer that loads omitted child results from CentralMemory."""

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
    ) -> None:
        """
        Initialize the pretty suite printer with a CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Width of separators. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors)
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
