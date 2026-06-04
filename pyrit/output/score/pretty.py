# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap

from colorama import Fore, Style

from pyrit.models import Score
from pyrit.output.base import PrinterBase
from pyrit.output.sink import Sink


class PrettyScorePrinter(PrinterBase):
    """
    Pretty printer for individual Score objects with ANSI-colored formatting.

    Provides ``_render_score`` for inline use by other printers (e.g.,
    conversation and attack-result printers) and ``render_async`` /
    ``write_async`` for standalone rendering of a list of scores.
    """

    def __init__(
        self, *, sink: Sink | None = None, width: int = 100, indent_size: int = 2, enable_colors: bool = True
    ) -> None:
        """
        Initialize the pretty score printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(sink=sink)
        self._width = width
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

    def _render_score(self, score: Score, indent_level: int = 3) -> str:
        """
        Render a single score with proper formatting.

        Args:
            score (Score): Score object to be rendered.
            indent_level (int): Number of indent units to apply. Defaults to 3.

        Returns:
            str: The rendered score text.
        """
        lines: list[str] = []
        indent = self._indent * indent_level
        scorer_name = (score.scorer_class_identifier.class_name if score.scorer_class_identifier else None) or "Unknown"
        lines.append(f"{indent}Scorer: {scorer_name}\n")
        lines.append(self._format_colored(f"{indent}• Category: {score.score_category or 'N/A'}", Fore.LIGHTMAGENTA_EX))
        lines.append(self._format_colored(f"{indent}• Type: {score.score_type}", Fore.CYAN))

        if score.score_type == "true_false":
            score_color = Fore.GREEN if score.get_value() else Fore.RED
        else:
            score_color = Fore.YELLOW

        lines.append(self._format_colored(f"{indent}• Value: {score.score_value}", score_color))

        if score.score_rationale:
            lines.append(f"{indent}• Rationale:\n")
            rationale_wrapper = textwrap.TextWrapper(
                width=self._width - len(indent) - 2,
                initial_indent=indent + "  ",
                subsequent_indent=indent + "  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
            rationale_lines = score.score_rationale.split("\n")
            for line in rationale_lines:
                if line.strip():
                    wrapped_lines = rationale_wrapper.wrap(line)
                    lines.extend(self._format_colored(wrapped_line, Fore.WHITE) for wrapped_line in wrapped_lines)
                else:
                    lines.append(self._format_colored(f"{indent}  "))

        return "".join(lines)

    async def render_async(self, scores: list[Score], *, indent_level: int = 3) -> str:
        """
        Render a list of scores and return as a string.

        Args:
            scores (list[Score]): The scores to render.
            indent_level (int): Number of indent units to apply. Defaults to 3.

        Returns:
            str: The rendered scores text.
        """
        return "".join(self._render_score(score, indent_level=indent_level) for score in scores)
