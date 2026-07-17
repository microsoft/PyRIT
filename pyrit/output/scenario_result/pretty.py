# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap

from colorama import Fore, Style

from pyrit.models import AttackOutcome, ScenarioResult
from pyrit.output.scenario_result.base import ScenarioResultPrinterBase
from pyrit.output.scorer.base import ScorerPrinterBase
from pyrit.output.sink import Sink


class PrettyScenarioResultPrinter(ScenarioResultPrinterBase):
    """
    Pretty printer for scenario results with ANSI-colored formatting.

    Contains all formatting logic. Subclasses must provide a scorer_printer
    via the abstract property.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        scorer_printer: ScorerPrinterBase | None = None,
        sort_groups_by_success_rate: bool = False,
    ) -> None:
        """
        Initialize the pretty scenario printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            scorer_printer (ScorerPrinterBase | None): Scorer printer for rendering scorer
                information. Defaults to None; leaf classes should provide a default.
            sort_groups_by_success_rate (bool): When True, the Per-Group Breakdown is sorted
                so that the group with the highest success rate appears first. Groups that tie
                on success rate retain their original relative order. Defaults to False, which
                preserves insertion order.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors
        self._scorer_printer = scorer_printer
        self._sort_groups_by_success_rate = sort_groups_by_success_rate

    def _format_colored(self, text: str, *colors: str) -> str:
        """
        Format text with color codes if colors are enabled.

        Args:
            text (str): The text to format.
            *colors: Variable number of colorama color constants to apply.

        Returns:
            str: The formatted line with trailing newline.
        """
        if self._enable_colors and colors:
            color_prefix = "".join(colors)
            return f"{color_prefix}{text}{Style.RESET_ALL}\n"
        return f"{text}\n"

    def _render_section_header(self, title: str) -> str:
        """
        Render a section header with visual separation.

        Args:
            title (str): The section title to display.

        Returns:
            str: The rendered section header.
        """
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored(f"▼ {title}", Style.BRIGHT, Fore.CYAN))
        lines.append(self._format_colored("─" * self._width, Fore.CYAN))
        return "".join(lines)

    def _render_header(self, result: ScenarioResult) -> str:
        """
        Render the header with scenario name.

        Args:
            result (ScenarioResult): The scenario result.

        Returns:
            str: The rendered header.
        """
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored("=" * self._width, Fore.CYAN))
        header_text = f"📊 SCENARIO RESULTS: {result.scenario_name}"
        lines.append(self._format_colored(header_text.center(self._width), Style.BRIGHT, Fore.CYAN))
        lines.append(self._format_colored("=" * self._width, Fore.CYAN))
        return "".join(lines)

    def _render_footer(self) -> str:
        """
        Render a footer separator.

        Returns:
            str: The rendered footer.
        """
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored("=" * self._width, Fore.CYAN))
        lines.append("\n")
        return "".join(lines)

    def _get_rate_color(self, rate: int) -> str:
        """
        Get color based on success rate.

        Args:
            rate (int): Success rate percentage (0-100).

        Returns:
            str: Colorama color constant.
        """
        if rate >= 75:
            return str(Fore.RED)
        if rate >= 50:
            return str(Fore.YELLOW)
        if rate >= 25:
            return str(Fore.CYAN)
        return str(Fore.GREEN)

    async def render_async(self, result: ScenarioResult) -> str:
        """
        Render the scenario result summary and return it as a string.

        Args:
            result (ScenarioResult): The scenario result to summarize.

        Returns:
            str: The rendered scenario result text.

        Raises:
            ValueError: If the result has an ``objective_scorer_identifier`` but no scorer printer
                is configured.
        """
        parts: list[str] = []

        lines: list[str] = []
        lines.append(self._render_header(result))

        lines.append(self._render_section_header("Scenario Information"))
        lines.append(self._format_colored(f"{self._indent}📋 Scenario Details", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}• Name: {result.scenario_name}", Fore.CYAN))
        lines.append(
            self._format_colored(f"{self._indent * 2}• Scenario Version: {result.scenario_version}", Fore.CYAN)
        )
        lines.append(self._format_colored(f"{self._indent * 2}• PyRIT Version: {result.pyrit_version}", Fore.CYAN))

        if result.scenario_description:
            lines.append(self._format_colored(f"{self._indent * 2}• Description:", Fore.CYAN))
            desc_indent = self._indent * 4
            available_width = 120 - len(desc_indent)
            wrapped_lines = textwrap.wrap(result.scenario_description, width=available_width, break_long_words=False)
            lines.extend(self._format_colored(f"{desc_indent}{line}", Fore.CYAN) for line in wrapped_lines)

        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}🎯 Target Information", Style.BRIGHT))
        target_id = result.objective_target_identifier
        target_type = target_id.class_name if target_id else "Unknown"
        target_model = (
            (target_id.params.get("underlying_model_name") or target_id.params.get("model_name") or "Unknown")
            if target_id
            else "Unknown"
        )
        target_endpoint = target_id.params.get("endpoint", "Unknown") if target_id else "Unknown"

        lines.append(self._format_colored(f"{self._indent * 2}• Target Type: {target_type}", Fore.CYAN))
        lines.append(self._format_colored(f"{self._indent * 2}• Target Model: {target_model}", Fore.CYAN))
        lines.append(self._format_colored(f"{self._indent * 2}• Target Endpoint: {target_endpoint}", Fore.CYAN))
        parts.append("".join(lines))

        scorer_identifier = result.objective_scorer_identifier
        if scorer_identifier:
            if self._scorer_printer is None:
                raise ValueError("scorer_printer is required when result has objective_scorer_identifier")
            parts.append(await self._scorer_printer.render_async(scorer_identifier=scorer_identifier))

        lines = []
        lines.append(self._render_section_header("Overall Statistics"))
        total_results = sum(len(results) for results in result.attack_results.values())
        total_techniques = len(result.get_techniques_used())
        overall_rate = result.objective_achieved_rate()

        lines.append(self._format_colored(f"{self._indent}📈 Summary", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}• Total Techniques: {total_techniques}", Fore.GREEN))
        lines.append(self._format_colored(f"{self._indent * 2}• Total Attack Results: {total_results}", Fore.GREEN))
        lines.append(
            self._format_colored(
                f"{self._indent * 2}• Overall Success Rate: {overall_rate}%", self._get_rate_color(overall_rate)
            )
        )

        objectives = result.get_objectives()
        lines.append(self._format_colored(f"{self._indent * 2}• Unique Objectives: {len(objectives)}", Fore.GREEN))

        lines.append(self._render_section_header("Per-Group Breakdown"))
        display_groups = result.get_display_groups()

        group_summaries: list[tuple[str, int, int]] = []
        for group_name, group_results in display_groups.items():
            total_group = len(group_results)
            if total_group == 0:
                group_rate = 0
            else:
                successful = sum(1 for r in group_results if r.outcome == AttackOutcome.SUCCESS)
                group_rate = int((successful / total_group) * 100)
            group_summaries.append((group_name, total_group, group_rate))

        if self._sort_groups_by_success_rate:
            # Stable sort so groups with equal rates retain their original relative order.
            group_summaries.sort(key=lambda item: item[2], reverse=True)

        for group_name, total_group, group_rate in group_summaries:
            lines.append("\n")
            lines.append(self._format_colored(f"{self._indent}🔸 Group: {group_name}", Style.BRIGHT))
            lines.append(self._format_colored(f"{self._indent * 2}• Number of Results: {total_group}", Fore.YELLOW))
            lines.append(
                self._format_colored(
                    f"{self._indent * 2}• Success Rate: {group_rate}%", self._get_rate_color(group_rate)
                )
            )

        lines.append(self._render_footer())
        parts.append("".join(lines))

        return "".join(parts)


class PrettyScenarioResultMemoryPrinter(PrettyScenarioResultPrinter):
    """
    Framework pretty printer for scenario results.

    Provides the framework's PrettyScorerMemoryPrinter for scorer information display.
    All formatting logic lives in PrettyScenarioResultPrinter.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        sort_groups_by_success_rate: bool = False,
    ) -> None:
        """
        Initialize the pretty scenario printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            sort_groups_by_success_rate (bool): When True, the Per-Group Breakdown is sorted
                so that the group with the highest success rate appears first. Defaults to False.
        """
        super().__init__(
            sink=sink,
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            sort_groups_by_success_rate=sort_groups_by_success_rate,
        )
        from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter

        scorer_printer = PrettyScorerMemoryPrinter(
            sink=self._sink, indent_size=indent_size, enable_colors=enable_colors
        )
        self._scorer_printer = scorer_printer

    async def render_async(self, result: ScenarioResult) -> str:
        """
        Render the scenario result summary and return it as a string.

        Args:
            result (ScenarioResult): The scenario result to summarize.

        Returns:
            str: The rendered scenario result text.
        """
        return await super().render_async(result)
