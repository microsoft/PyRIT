# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
from abc import abstractmethod

from colorama import Fore, Style

from pyrit.models import AttackOutcome
from pyrit.models.scenario_result import ScenarioResult
from pyrit.printer.scenario_result.base import ScenarioResultPrinterBase
from pyrit.printer.scorer.base import ScorerPrinterBase


class ConsoleScenarioPrinterBase(ScenarioResultPrinterBase):
    """
    Console printer base for scenario results with enhanced formatting.

    Contains all formatting logic. Subclasses must provide a scorer_printer
    via the abstract property.
    """

    def __init__(
        self,
        *,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
    ) -> None:
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

    @property
    @abstractmethod
    def scorer_printer(self) -> ScorerPrinterBase:
        """Return the scorer printer instance."""

    def _print_colored(self, text: str, *colors: str) -> None:
        """
        Print text with color formatting if colors are enabled.

        Args:
            text (str): The text to print.
            *colors: Variable number of colorama color constants to apply.
        """
        if self._enable_colors and colors:
            color_prefix = "".join(colors)
            print(f"{color_prefix}{text}{Style.RESET_ALL}")
        else:
            print(text)

    def _print_section_header(self, title: str) -> None:
        """
        Print a section header with visual separation.

        Args:
            title (str): The section title to display.
        """
        print()
        self._print_colored(f"▼ {title}", Style.BRIGHT, Fore.CYAN)
        self._print_colored("─" * self._width, Fore.CYAN)

    async def print_summary_async(self, result: ScenarioResult) -> None:
        """
        Print a summary of the scenario result with per-group breakdown.

        Args:
            result (ScenarioResult): The scenario result to summarize.
        """
        self._print_header(result)

        self._print_section_header("Scenario Information")
        self._print_colored(f"{self._indent}📋 Scenario Details", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Name: {result.scenario_identifier.name}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Scenario Version: {result.scenario_identifier.version}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• PyRIT Version: {result.scenario_identifier.pyrit_version}", Fore.CYAN)

        if result.scenario_identifier.description:
            self._print_colored(f"{self._indent * 2}• Description:", Fore.CYAN)
            desc_indent = self._indent * 4
            available_width = 120 - len(desc_indent)
            wrapped_lines = textwrap.wrap(
                result.scenario_identifier.description, width=available_width, break_long_words=False
            )
            for line in wrapped_lines:
                self._print_colored(f"{desc_indent}{line}", Fore.CYAN)

        print()
        self._print_colored(f"{self._indent}🎯 Target Information", Style.BRIGHT)
        target_id = result.objective_target_identifier
        target_type = target_id.class_name if target_id else "Unknown"
        target_model = target_id.params.get("model_name", "Unknown") if target_id else "Unknown"
        target_endpoint = target_id.params.get("endpoint", "Unknown") if target_id else "Unknown"

        self._print_colored(f"{self._indent * 2}• Target Type: {target_type}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Target Model: {target_model}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Target Endpoint: {target_endpoint}", Fore.CYAN)

        scorer_identifier = result.objective_scorer_identifier
        if scorer_identifier:
            self.scorer_printer.print_objective_scorer(scorer_identifier=scorer_identifier)

        self._print_section_header("Overall Statistics")
        total_results = sum(len(results) for results in result.attack_results.values())
        total_strategies = len(result.get_strategies_used())
        overall_rate = result.objective_achieved_rate()

        self._print_colored(f"{self._indent}📈 Summary", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Total Strategies: {total_strategies}", Fore.GREEN)
        self._print_colored(f"{self._indent * 2}• Total Attack Results: {total_results}", Fore.GREEN)
        self._print_colored(
            f"{self._indent * 2}• Overall Success Rate: {overall_rate}%", self._get_rate_color(overall_rate)
        )

        objectives = result.get_objectives()
        self._print_colored(f"{self._indent * 2}• Unique Objectives: {len(objectives)}", Fore.GREEN)

        self._print_section_header("Per-Group Breakdown")
        display_groups = result.get_display_groups()

        for group_name, group_results in display_groups.items():
            total_group = len(group_results)
            if total_group == 0:
                group_rate = 0
            else:
                successful = sum(1 for r in group_results if r.outcome == AttackOutcome.SUCCESS)
                group_rate = int((successful / total_group) * 100)

            print()
            self._print_colored(f"{self._indent}🔸 Group: {group_name}", Style.BRIGHT)
            self._print_colored(f"{self._indent * 2}• Number of Results: {total_group}", Fore.YELLOW)
            self._print_colored(f"{self._indent * 2}• Success Rate: {group_rate}%", self._get_rate_color(group_rate))

        self._print_footer()

    def _print_header(self, result: ScenarioResult) -> None:
        """
        Print the header with scenario name.

        Args:
            result (ScenarioResult): The scenario result.
        """
        print()
        self._print_colored("=" * self._width, Fore.CYAN)
        header_text = f"📊 SCENARIO RESULTS: {result.scenario_identifier.name}"
        self._print_colored(header_text.center(self._width), Style.BRIGHT, Fore.CYAN)
        self._print_colored("=" * self._width, Fore.CYAN)

    def _print_footer(self) -> None:
        """Print a footer separator."""
        print()
        self._print_colored("=" * self._width, Fore.CYAN)
        print()

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


class ConsoleScenarioMemoryPrinter(ConsoleScenarioPrinterBase):
    """
    Framework console printer for scenario results.

    Provides the framework's ConsoleScorerMemoryPrinter for scorer information display.
    All formatting logic lives in ConsoleScenarioPrinterBase.
    """

    def __init__(
        self,
        *,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
    ) -> None:
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(width=width, indent_size=indent_size, enable_colors=enable_colors)
        from pyrit.printer.scorer.console import ConsoleScorerMemoryPrinter

        self._scorer_printer = ConsoleScorerMemoryPrinter(indent_size=indent_size, enable_colors=enable_colors)

    @property
    def scorer_printer(self) -> ScorerPrinterBase:
        """Return the scorer printer instance."""
        return self._scorer_printer
