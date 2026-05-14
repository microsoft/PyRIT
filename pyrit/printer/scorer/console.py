# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, Optional

from colorama import Fore, Style

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.scorer.base import ScorerPrinterBase


class ConsoleScorerPrinterBase(ScorerPrinterBase):
    """
    Console printer base for scorer information with enhanced formatting.

    Contains all formatting logic. Subclasses implement get_objective_metrics
    and get_harm_metrics for data fetching.
    """

    _SCORER_DISPLAY_PARAMS = frozenset({"scorer_type", "score_aggregator"})
    _TARGET_DISPLAY_PARAMS = frozenset({"model_name", "temperature"})

    def __init__(self, *, indent_size: int = 2, enable_colors: bool = True) -> None:
        """
        Initialize the console scorer printer.

        Args:
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
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
            higher_is_better (bool): If True, higher values are better.
            good_threshold (float): The threshold for "good" (green) values.
            bad_threshold (float): The threshold for "bad" (red) values.

        Returns:
            str: The colorama color constant to use.
        """
        if higher_is_better:
            if value >= good_threshold:
                return str(Fore.GREEN)
            if value < bad_threshold:
                return str(Fore.RED)
            return str(Fore.CYAN)
        if value <= good_threshold:
            return str(Fore.GREEN)
        if value > bad_threshold:
            return str(Fore.RED)
        return str(Fore.CYAN)

    def _compute_eval_hash(self, scorer_identifier: ComponentIdentifier) -> str:
        """
        Compute the evaluation hash for a scorer identifier.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.

        Returns:
            str: The evaluation hash string.
        """
        from pyrit.identifiers.evaluation_identifier import ScorerEvaluationIdentifier

        return ScorerEvaluationIdentifier(scorer_identifier).eval_hash

    def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
        """
        Print objective scorer information.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
        """
        print()
        self._print_colored(f"{self._indent}📊 Scorer Information", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}▸ Scorer Identifier", Fore.WHITE)
        self._print_scorer_info(scorer_identifier, indent_level=3)

        eval_hash = self._compute_eval_hash(scorer_identifier)
        metrics = self.get_objective_metrics(eval_hash=eval_hash)
        self._print_objective_metrics(metrics)

    def print_harm_scorer(self, scorer_identifier: ComponentIdentifier, *, harm_category: str) -> None:
        """
        Print harm scorer information.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
            harm_category (str): The harm category for looking up metrics.
        """
        print()
        self._print_colored(f"{self._indent}📊 Scorer Information", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}▸ Scorer Identifier", Fore.WHITE)
        self._print_scorer_info(scorer_identifier, indent_level=3)

        eval_hash = self._compute_eval_hash(scorer_identifier)
        metrics = self.get_harm_metrics(eval_hash=eval_hash, harm_category=harm_category)
        self._print_harm_metrics(metrics)

    def _print_scorer_info(self, scorer_identifier: ComponentIdentifier, *, indent_level: int = 2) -> None:
        """
        Print scorer information including nested sub-scorers.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            indent_level (int): Current indentation level.
        """
        indent = self._indent * indent_level

        self._print_colored(f"{indent}• Scorer Type: {scorer_identifier.class_name}", Fore.CYAN)

        for key, value in scorer_identifier.params.items():
            if key in self._SCORER_DISPLAY_PARAMS and value is not None:
                self._print_colored(f"{indent}• {key}: {value}", Fore.CYAN)

        prompt_target = scorer_identifier.get_child("prompt_target")
        if prompt_target:
            for key, value in prompt_target.params.items():
                if key in self._TARGET_DISPLAY_PARAMS and value is not None:
                    self._print_colored(f"{indent}• {key}: {value}", Fore.CYAN)

        sub_scorers = scorer_identifier.get_child_list("sub_scorers")
        if sub_scorers:
            self._print_colored(f"{indent}  └─ Composite of {len(sub_scorers)} scorer(s):", Fore.CYAN)
            for sub_scorer_id in sub_scorers:
                self._print_scorer_info(sub_scorer_id, indent_level=indent_level + 3)

    def _print_objective_metrics(self, metrics: Optional[Any]) -> None:
        """
        Print objective scorer evaluation metrics.

        Args:
            metrics: The metrics to print, or None if not available.
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

        accuracy_color = self._get_quality_color(
            metrics.accuracy, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
        )
        self._print_colored(f"{self._indent * 3}• Accuracy: {metrics.accuracy:.2%}", accuracy_color)

        if metrics.accuracy_standard_error is not None:
            self._print_colored(
                f"{self._indent * 3}• Accuracy Std Error: ±{metrics.accuracy_standard_error:.4f}", Fore.CYAN
            )

        if metrics.f1_score is not None:
            f1_color = self._get_quality_color(
                metrics.f1_score, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• F1 Score: {metrics.f1_score:.4f}", f1_color)

        if metrics.precision is not None:
            precision_color = self._get_quality_color(
                metrics.precision, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• Precision: {metrics.precision:.4f}", precision_color)

        if metrics.recall is not None:
            recall_color = self._get_quality_color(
                metrics.recall, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            self._print_colored(f"{self._indent * 3}• Recall: {metrics.recall:.4f}", recall_color)

        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=0.5, bad_threshold=3.0
            )
            self._print_colored(
                f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
            )

    def _print_harm_metrics(self, metrics: Optional[Any]) -> None:
        """
        Print harm scorer evaluation metrics.

        Args:
            metrics: The metrics to print, or None if not available.
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

        mae_color = self._get_quality_color(
            metrics.mean_absolute_error, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25
        )
        self._print_colored(f"{self._indent * 3}• Mean Absolute Error: {metrics.mean_absolute_error:.4f}", mae_color)

        if metrics.mae_standard_error is not None:
            self._print_colored(f"{self._indent * 3}• MAE Std Error: ±{metrics.mae_standard_error:.4f}", Fore.CYAN)

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

        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=1.0, bad_threshold=3.0
            )
            self._print_colored(
                f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
            )


class ConsoleScorerPrinter(ConsoleScorerPrinterBase):
    """
    Framework console printer for scorer information.

    Implements metrics fetching via the scorer evaluation registry (deferred import).
    All formatting logic lives in ConsoleScorerPrinterBase.
    """

    def get_objective_metrics(self, *, eval_hash: str) -> Any:
        """Fetch objective scorer evaluation metrics from the registry."""
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)

    def get_harm_metrics(self, *, eval_hash: str, harm_category: str) -> Any:
        """Fetch harm scorer evaluation metrics from the registry."""
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_eval_hash,
        )

        return find_harm_metrics_by_eval_hash(eval_hash=eval_hash, harm_category=harm_category)
