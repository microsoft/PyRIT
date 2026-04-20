# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Optional

from colorama import Fore, Style

from pyrit.identifiers import ScorerIdentifier
from pyrit.score.printer.scorer_printer import ScorerPrinter

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.scorer_metrics import (
        HarmScorerMetrics,
        ObjectiveScorerMetrics,
    )


class ConsoleScorerPrinter(ScorerPrinter):
    """
    Console printer for scorer information with enhanced formatting.

    This printer formats scorer details for console display with optional color coding,
    proper indentation, and visual hierarchy. Colors can be disabled for consoles
    that don't support ANSI characters.
    """

    def __init__(self, *, indent_size: int = 2, enable_colors: bool = True):
        """
        Initialize the console scorer printer.

        Args:
            indent_size (int): Number of spaces for indentation. Must be non-negative.
                Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. When False,
                all output will be plain text without colors. Defaults to True.

        Raises:
            ValueError: If indent_size < 0.
        """
        if indent_size < 0:
            raise ValueError("indent_size must be non-negative")
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

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

    def _get_quality_color(
        self, value: float, *, higher_is_better: bool, good_threshold: float, bad_threshold: float
    ) -> str:
        """
        Determine the color based on metric quality thresholds.

        Args:
            value (float): The metric value to evaluate.
            higher_is_better (bool): If True, higher values are better (e.g., accuracy).
                If False, lower values are better (e.g., MAE).
            good_threshold (float): The threshold for "good" (green) values.
            bad_threshold (float): The threshold for "bad" (red) values.

        Returns:
            str: The colorama color constant to use.
        """
        if higher_is_better:
            if value >= good_threshold:
                return Fore.GREEN  # type: ignore[no-any-return]
            elif value < bad_threshold:
                return Fore.RED  # type: ignore[no-any-return]
            return Fore.CYAN  # type: ignore[no-any-return]
        else:
            # Lower is better (e.g., MAE, score time)
            if value <= good_threshold:
                return Fore.GREEN  # type: ignore[no-any-return]
            elif value > bad_threshold:
                return Fore.RED  # type: ignore[no-any-return]
            return Fore.CYAN  # type: ignore[no-any-return]

    def print_objective_scorer(self, *, scorer_identifier: ScorerIdentifier) -> None:
        """
        Print objective scorer information including type, nested scorers, and evaluation metrics.

        This method displays:
        - Scorer type and identity information
        - Nested sub-scorers (for composite scorers)
        - Objective evaluation metrics (accuracy, precision, recall, F1) from the registry

        Args:
            scorer_identifier (ScorerIdentifier): The scorer identifier to print information for.
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_hash,
        )

        print()
        self._print_colored(f"{self._indent}📊 Scorer Information", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}▸ Scorer Identifier", Fore.WHITE)
        self._print_scorer_info(scorer_identifier, indent_level=3)

        # Look up metrics by hash
        scorer_hash = scorer_identifier.hash
        metrics = find_objective_metrics_by_hash(hash=scorer_hash)
        self._print_objective_metrics(metrics)

    def print_harm_scorer(self, scorer_identifier: ScorerIdentifier, *, harm_category: str) -> None:
        """
        Print harm scorer information including type, nested scorers, and evaluation metrics.

        This method displays:
        - Scorer type and identity information
        - Nested sub-scorers (for composite scorers)
        - Harm evaluation metrics (MAE, Krippendorff alpha) from the registry

        Args:
            scorer_identifier (ScorerIdentifier): The scorer identifier to print information for.
            harm_category (str): The harm category for looking up metrics (e.g., "hate_speech", "violence").
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_hash,
        )

        print()
        self._print_colored(f"{self._indent}📊 Scorer Information", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}▸ Scorer Identifier", Fore.WHITE)
        self._print_scorer_info(scorer_identifier, indent_level=3)

        # Look up metrics by hash and harm category
        scorer_hash = scorer_identifier.hash
        metrics = find_harm_metrics_by_hash(hash=scorer_hash, harm_category=harm_category)
        self._print_harm_metrics(metrics)

    def _print_scorer_info(self, scorer_identifier: ScorerIdentifier, *, indent_level: int = 2) -> None:
        """
        Print scorer information including nested sub-scorers.

        Args:
            scorer_identifier (ScorerIdentifier): The scorer identifier.
            indent_level (int): Current indentation level for nested display.
        """
        indent = self._indent * indent_level

        self._print_colored(f"{indent}• Scorer Type: {scorer_identifier.class_name}", Fore.CYAN)

        # Print target info if available
        if scorer_identifier.target_info:
            model_name = scorer_identifier.target_info.get("model_name", "Unknown")
            temperature = scorer_identifier.target_info.get("temperature")
            self._print_colored(f"{indent}• Target Model: {model_name}", Fore.CYAN)
            self._print_colored(f"{indent}• Temperature: {temperature}", Fore.CYAN)

        # Print score aggregator if available
        if scorer_identifier.score_aggregator:
            self._print_colored(f"{indent}• Score Aggregator: {scorer_identifier.score_aggregator}", Fore.CYAN)

        # Print scorer-specific params if available
        if scorer_identifier.scorer_specific_params:
            for key, value in scorer_identifier.scorer_specific_params.items():
                self._print_colored(f"{indent}• {key}: {value}", Fore.CYAN)

        # Check for sub_identifier (nested scorers)
        sub_identifier = scorer_identifier.sub_identifier
        if sub_identifier:
            # Handle list of sub-scorers (composite scorer)
            if isinstance(sub_identifier, list):
                self._print_colored(f"{indent}  └─ Composite of {len(sub_identifier)} scorer(s):", Fore.CYAN)
                for sub_scorer_id in sub_identifier:
                    self._print_scorer_info(sub_scorer_id, indent_level=indent_level + 3)

    def _print_objective_metrics(self, metrics: Optional["ObjectiveScorerMetrics"]) -> None:
        """
        Print objective scorer evaluation metrics.

        Args:
            metrics (Optional[ObjectiveScorerMetrics]): The metrics to print, or None if not available.
        """
        if metrics is None:
            print()
            self._print_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE)
            self._print_colored(
                f"{self._indent * 3}Official evaluation has not been run yet for this specific configuration",
                Fore.YELLOW,
            )
            return

        print()
        self._print_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE)

        # Accuracy: >= 0.9 is good, < 0.7 is bad
        accuracy_color = self._get_quality_color(
            metrics.accuracy, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
        )
        self._print_colored(f"{self._indent * 3}• Accuracy: {metrics.accuracy:.2%}", accuracy_color)

        if metrics.accuracy_standard_error is not None:
            self._print_colored(
                f"{self._indent * 3}• Accuracy Std Error: ±{metrics.accuracy_standard_error:.4f}", Fore.CYAN
            )

        # F1 Score: >= 0.9 is good, < 0.7 is bad
        if metrics.f1_score is not None:
            f1_color = self._get_quality_color(
                metrics.f1_score, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• F1 Score: {metrics.f1_score:.4f}", f1_color)

        # Precision: >= 0.9 is good, < 0.7 is bad
        if metrics.precision is not None:
            precision_color = self._get_quality_color(
                metrics.precision, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• Precision: {metrics.precision:.4f}", precision_color)

        # Recall: >= 0.9 is good, < 0.7 is bad
        if metrics.recall is not None:
            recall_color = self._get_quality_color(
                metrics.recall, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• Recall: {metrics.recall:.4f}", recall_color)

        # Average Score Time: < 0.5s is good, > 3.0s is bad
        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=0.5, bad_threshold=3.0
            )
            self._print_colored(
                f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
            )

    def _print_harm_metrics(self, metrics: Optional["HarmScorerMetrics"]) -> None:
        """
        Print harm scorer evaluation metrics.

        Args:
            metrics (Optional[HarmScorerMetrics]): The metrics to print, or None if not available.
        """
        if metrics is None:
            print()
            self._print_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE)
            self._print_colored(
                f"{self._indent * 3}Official evaluation has not been run yet for this specific configuration",
                Fore.YELLOW,
            )
            return

        print()
        self._print_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE)

        # MAE: <= 0.1 is good, > 0.25 is bad (lower is better)
        mae_color = self._get_quality_color(
            metrics.mean_absolute_error, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25
        )
        self._print_colored(f"{self._indent * 3}• Mean Absolute Error: {metrics.mean_absolute_error:.4f}", mae_color)

        if metrics.mae_standard_error is not None:
            self._print_colored(f"{self._indent * 3}• MAE Std Error: ±{metrics.mae_standard_error:.4f}", Fore.CYAN)

        # Krippendorff Alpha: >= 0.8 is strong agreement, < 0.6 is weak agreement
        if metrics.krippendorff_alpha_combined is not None:
            alpha_color = self._get_quality_color(
                metrics.krippendorff_alpha_combined, higher_is_better=True, good_threshold=0.8, bad_threshold=0.6
            )
            self._print_colored(
                f"{self._indent * 3}• Krippendorff Alpha (Combined): {metrics.krippendorff_alpha_combined:.4f}",
                alpha_color,
            )

        if metrics.krippendorff_alpha_model is not None:
            alpha_model_color = self._get_quality_color(
                metrics.krippendorff_alpha_model, higher_is_better=True, good_threshold=0.8, bad_threshold=0.6
            )
            self._print_colored(
                f"{self._indent * 3}• Krippendorff Alpha (Model): {metrics.krippendorff_alpha_model:.4f}",
                alpha_model_color,
            )

        # Average Score Time: < 1s is good, > 3.0s is bad
        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=1.0, bad_threshold=3.0
            )
            self._print_colored(
                f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
            )
