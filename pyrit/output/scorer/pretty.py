# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

from colorama import Fore, Style

from pyrit.identifiers import ComponentIdentifier
from pyrit.output.scorer.base import ScorerPrinterBase
from pyrit.output.sink import Sink


class PrettyScorerPrinter(ScorerPrinterBase):
    """
    Pretty printer for scorer information with ANSI-colored formatting.

    Contains all formatting logic. Subclasses implement _get_objective_metrics
    and _get_harm_metrics for data fetching.
    """

    _SCORER_DISPLAY_PARAMS = frozenset({"scorer_type", "score_aggregator"})
    _TARGET_DISPLAY_PARAMS = frozenset({"model_name", "temperature"})

    def __init__(self, *, sink: Sink | None = None, indent_size: int = 2, enable_colors: bool = True) -> None:
        """
        Initialize the pretty scorer printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.

        Raises:
            ValueError: If indent_size is negative.
        """
        super().__init__(sink=sink)
        if indent_size < 0:
            raise ValueError("indent_size must be non-negative")
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

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

    def _render_scorer_info(self, scorer_identifier: ComponentIdentifier, *, indent_level: int = 2) -> str:
        """
        Render scorer information including nested sub-scorers.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            indent_level (int): Current indentation level.

        Returns:
            str: The rendered scorer info text.
        """
        lines: list[str] = []
        indent = self._indent * indent_level

        lines.append(self._format_colored(f"{indent}• Scorer Type: {scorer_identifier.class_name}", Fore.CYAN))

        for key, value in scorer_identifier.params.items():
            if key in self._SCORER_DISPLAY_PARAMS and value is not None:
                lines.append(self._format_colored(f"{indent}• {key}: {value}", Fore.CYAN))

        prompt_target = scorer_identifier.get_child("prompt_target")
        if prompt_target:
            for key, value in prompt_target.params.items():
                if key in self._TARGET_DISPLAY_PARAMS and value is not None:
                    lines.append(self._format_colored(f"{indent}• {key}: {value}", Fore.CYAN))

        sub_scorers = scorer_identifier.get_child_list("sub_scorers")
        if sub_scorers:
            lines.append(self._format_colored(f"{indent}  └─ Composite of {len(sub_scorers)} scorer(s):", Fore.CYAN))
            lines.extend(
                self._render_scorer_info(sub_scorer_id, indent_level=indent_level + 3) for sub_scorer_id in sub_scorers
            )

        return "".join(lines)

    def _render_objective_metrics(self, metrics: Any | None) -> str:
        """
        Render objective scorer evaluation metrics.

        Args:
            metrics: The metrics to render, or None if not available.

        Returns:
            str: The rendered metrics text.
        """
        lines: list[str] = []

        if metrics is None:
            lines.append("\n")
            lines.append(self._format_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE))
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}Official evaluation has not been run yet for this specific configuration",
                    Fore.YELLOW,
                )
            )
            return "".join(lines)

        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE))

        accuracy_color = self._get_quality_color(
            metrics.accuracy, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
        )
        lines.append(self._format_colored(f"{self._indent * 3}• Accuracy: {metrics.accuracy:.2%}", accuracy_color))

        if metrics.accuracy_standard_error is not None:
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}• Accuracy Std Error: ±{metrics.accuracy_standard_error:.4f}", Fore.CYAN
                )
            )

        if metrics.f1_score is not None:
            f1_color = self._get_quality_color(
                metrics.f1_score, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            lines.append(self._format_colored(f"{self._indent * 3}• F1 Score: {metrics.f1_score:.4f}", f1_color))

        if metrics.precision is not None:
            precision_color = self._get_quality_color(
                metrics.precision, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            lines.append(
                self._format_colored(f"{self._indent * 3}• Precision: {metrics.precision:.4f}", precision_color)
            )

        if metrics.recall is not None:
            recall_color = self._get_quality_color(
                metrics.recall, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7
            )
            lines.append(self._format_colored(f"{self._indent * 3}• Recall: {metrics.recall:.4f}", recall_color))

        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=0.5, bad_threshold=3.0
            )
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
                )
            )

        return "".join(lines)

    def _render_harm_metrics(self, metrics: Any | None) -> str:
        """
        Render harm scorer evaluation metrics.

        Args:
            metrics: The metrics to render, or None if not available.

        Returns:
            str: The rendered metrics text.
        """
        lines: list[str] = []

        if metrics is None:
            lines.append("\n")
            lines.append(self._format_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE))
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}Official evaluation has not been run yet for this specific configuration",
                    Fore.YELLOW,
                )
            )
            return "".join(lines)

        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent * 2}▸ Performance Metrics", Fore.WHITE))

        mae_color = self._get_quality_color(
            metrics.mean_absolute_error, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25
        )
        lines.append(
            self._format_colored(
                f"{self._indent * 3}• Mean Absolute Error: {metrics.mean_absolute_error:.4f}", mae_color
            )
        )

        if metrics.mae_standard_error is not None:
            lines.append(
                self._format_colored(f"{self._indent * 3}• MAE Std Error: ±{metrics.mae_standard_error:.4f}", Fore.CYAN)
            )

        if metrics.krippendorff_alpha_combined is not None:
            alpha_color = self._get_quality_color(
                metrics.krippendorff_alpha_combined, higher_is_better=True, good_threshold=0.8, bad_threshold=0.6
            )
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}• Krippendorff Alpha (Combined): {metrics.krippendorff_alpha_combined:.4f}",
                    alpha_color,
                )
            )

        if metrics.krippendorff_alpha_model is not None:
            alpha_model_color = self._get_quality_color(
                metrics.krippendorff_alpha_model, higher_is_better=True, good_threshold=0.8, bad_threshold=0.6
            )
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}• Krippendorff Alpha (Model): {metrics.krippendorff_alpha_model:.4f}",
                    alpha_model_color,
                )
            )

        if metrics.average_score_time_seconds is not None:
            time_color = self._get_quality_color(
                metrics.average_score_time_seconds, higher_is_better=False, good_threshold=1.0, bad_threshold=3.0
            )
            lines.append(
                self._format_colored(
                    f"{self._indent * 3}• Average Score Time: {metrics.average_score_time_seconds:.2f}s", time_color
                )
            )

        return "".join(lines)

    async def render_async(self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None) -> str:
        """
        Render scorer information and return it as a string.

        Auto-detects scorer type: if harm_category is provided, renders harm
        metrics; otherwise renders objective metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.

        Returns:
            str: The rendered scorer information text.
        """
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}📊 Scorer Information", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}▸ Scorer Identifier", Fore.WHITE))
        lines.append(self._render_scorer_info(scorer_identifier, indent_level=3))

        if harm_category is not None:
            metrics = self._get_harm_metrics(scorer_identifier=scorer_identifier, harm_category=harm_category)
            lines.append(self._render_harm_metrics(metrics))
        else:
            metrics = self._get_objective_metrics(scorer_identifier=scorer_identifier)
            lines.append(self._render_objective_metrics(metrics))

        return "".join(lines)


class PrettyScorerMemoryPrinter(PrettyScorerPrinter):
    """
    Framework pretty printer for scorer information.

    Implements metrics fetching via the scorer evaluation registry (deferred import).
    All formatting logic lives in PrettyScorerPrinter.
    """

    async def render_async(self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None) -> str:
        """
        Render scorer information and return it as a string.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.

        Returns:
            str: The rendered scorer information text.
        """
        return await super().render_async(scorer_identifier=scorer_identifier, harm_category=harm_category)

    def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier) -> Any:
        """
        Fetch objective scorer evaluation metrics from the registry.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.

        Returns:
            ObjectiveScorerMetrics or None: The metrics, or None if not found.
        """
        from pyrit.identifiers.evaluation_identifier import ScorerEvaluationIdentifier
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        eval_hash = ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)

    def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics from the registry.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str): The harm category to look up.

        Returns:
            HarmScorerMetrics or None: The metrics, or None if not found.
        """
        from pyrit.identifiers.evaluation_identifier import ScorerEvaluationIdentifier
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_eval_hash,
        )

        eval_hash = ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        return find_harm_metrics_by_eval_hash(eval_hash=eval_hash, harm_category=harm_category)
