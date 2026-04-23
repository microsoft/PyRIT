# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
from typing import Optional

from colorama import Fore, Style

from pyrit.models import AttackOutcome
from pyrit.models.scenario_result import ScenarioResult
from pyrit.scenario.printer.scenario_result_printer import ScenarioResultPrinter
from pyrit.score.printer import ConsoleScorerPrinter, ScorerPrinter


class ConsoleScenarioResultPrinter(ScenarioResultPrinter):
    """
    Console printer for scenario results with enhanced formatting.

    This printer formats scenario results for console display with optional color coding,
    proper indentation, and visual separators. Colors can be disabled for consoles
    that don't support ANSI characters.
    """

    def __init__(
        self,
        *,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        scorer_printer: Optional[ScorerPrinter] = None,
    ):
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Must be positive.
                Defaults to 100.
            indent_size (int): Number of spaces for indentation. Must be non-negative.
                Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. When False,
                all output will be plain text without colors. Defaults to True.
            scorer_printer (Optional[ScorerPrinter]): Printer for scorer information.
                If not provided, a ConsoleScorerPrinter with matching settings is created.

        Raises:
            ValueError: If width <= 0 or indent_size < 0.
        """
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors
        self._scorer_printer = scorer_printer or ConsoleScorerPrinter(
            indent_size=indent_size, enable_colors=enable_colors
        )

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

        Displays:
        - Scenario identification (name, version, PyRIT version)
        - Target and scorer information
        - Overall statistics
        - Per-group success rates and result counts

        Args:
            result (ScenarioResult): The scenario result to summarize
        """
        # Print header
        self._print_header(result)

        # Scenario information
        self._print_section_header("Scenario Information")
        self._print_colored(f"{self._indent}📋 Scenario Details", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Name: {result.scenario_identifier.name}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Scenario Version: {result.scenario_identifier.version}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• PyRIT Version: {result.scenario_identifier.pyrit_version}", Fore.CYAN)

        # Format description with text wrapping at 120 characters
        if result.scenario_identifier.description:
            self._print_colored(f"{self._indent * 2}• Description:", Fore.CYAN)
            desc_indent = self._indent * 4
            # Calculate available width for description text (total 120 - indent)
            available_width = 120 - len(desc_indent)
            # Wrap the description text and print each line
            wrapped_lines = textwrap.wrap(
                result.scenario_identifier.description, width=available_width, break_long_words=False
            )
            for line in wrapped_lines:
                self._print_colored(f"{desc_indent}{line}", Fore.CYAN)

        # Target information
        print()
        self._print_colored(f"{self._indent}🎯 Target Information", Style.BRIGHT)
        target_id = result.objective_target_identifier
        target_type = target_id.class_name if target_id else "Unknown"
        target_model = target_id.params.get("model_name", "Unknown") if target_id else "Unknown"
        target_endpoint = target_id.params.get("endpoint", "Unknown") if target_id else "Unknown"

        self._print_colored(f"{self._indent * 2}• Target Type: {target_type}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Target Model: {target_model}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Target Endpoint: {target_endpoint}", Fore.CYAN)

        # Scorer information - use ComponentIdentifier from result
        scorer_identifier = result.objective_scorer_identifier
        if scorer_identifier:
            self._scorer_printer.print_objective_scorer(scorer_identifier=scorer_identifier)

        # Overall statistics
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

        # Per-group breakdown
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

        # Print footer
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
        """
        Print a footer separator.
        """
        print()
        self._print_colored("=" * self._width, Fore.CYAN)
        print()

    def _get_rate_color(self, rate: int) -> str:
        """
        Get color based on success rate.

        Args:
            rate (int): Success rate percentage (0-100)

        Returns:
            str: Colorama color constant
        """
        if rate >= 75:
            return str(Fore.RED)  # High success (bad for security)
        if rate >= 50:
            return str(Fore.YELLOW)  # Medium success
        if rate >= 25:
            return str(Fore.CYAN)  # Low success
        return str(Fore.GREEN)  # Very low success (good for security)
