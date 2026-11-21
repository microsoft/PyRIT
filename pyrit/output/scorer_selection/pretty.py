# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from colorama import Fore

from pyrit.models import ScenarioRunPlanGroupKind, ScorerSelectionEntry, ScorerSelectionReport
from pyrit.output._formatting import _PrettyPrinterMixin
from pyrit.output.base import PrinterBase
from pyrit.output.sink import Sink


class PrettyScorerSelectionReportPrinter(_PrettyPrinterMixin, PrinterBase):
    """
    Pretty printer for a scenario's scorer selection report.

    Each planned atomic group gets a line naming its selected scorer and what the
    selection required, then one line per candidate rejected before it.
    """

    def __init__(
        self, *, sink: Sink | None = None, width: int = 100, indent_size: int = 2, enable_colors: bool = True
    ) -> None:
        """
        Initialize the pretty scorer selection report printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Width of the header separators. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

    def _render_entry(self, entry: ScorerSelectionEntry) -> str:
        """
        Render one planned atomic group's selection.

        Args:
            entry (ScorerSelectionEntry): The group's selection.

        Returns:
            str: The rendered lines.
        """
        kind = "" if entry.group_kind is ScenarioRunPlanGroupKind.ATTACK else f" ({entry.group_kind.value})"
        lines = [
            self._format_colored(f"{entry.atomic_attack_name}{kind}: {entry.selected.candidate_label}", Fore.GREEN)
        ]
        details = [f"group {entry.display_group}"]
        if entry.technique is not None:
            details.append(f"technique {entry.technique}")
        needs = [capability.value for capability in entry.required_capabilities]
        if entry.required_evaluation is not None:
            needs.append(f"evaluation '{entry.required_evaluation}'")
        details.append(f"requires {', '.join(needs)}" if needs else "no requirements")
        lines.append(self._format_colored(f"{self._indent}{'; '.join(details)}", Fore.CYAN))
        for verdict in entry.rejected:
            reason_kind = "" if verdict.reason_kind is None else f" ({verdict.reason_kind.value})"
            lines.append(
                self._format_colored(
                    f"{self._indent}rejected #{verdict.candidate_index} {verdict.candidate_label}{reason_kind}: "
                    f"{verdict.reason}",
                    Fore.YELLOW,
                )
            )
        return "".join(lines)

    async def render_async(self, report: ScorerSelectionReport) -> str:
        """
        Render a scorer selection report and return it as a string.

        Args:
            report (ScorerSelectionReport): The report to render.

        Returns:
            str: The rendered report.
        """
        parts = [self._render_boxed_header("Scorer Selection")]
        if not report.entries:
            parts.append(self._format_colored("No atomic group selected its scorer from a pool."))
        else:
            parts.append(self._format_colored(f"{len(report.entries)} atomic group(s) selected from a pool."))
            parts.extend(self._render_entry(entry) for entry in report.entries)
        parts.append(self._render_footer())
        return "".join(parts)
