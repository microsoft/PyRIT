# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import Score
from pyrit.output.base import PrinterBase
from pyrit.output.sink import Sink


class MarkdownScorePrinter(PrinterBase):
    """
    Markdown printer for individual Score objects.

    Provides ``_format_score`` for inline use by other printers and
    ``render_async`` / ``write_async`` for standalone rendering.
    """

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the markdown score printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
        """
        super().__init__(sink=sink)

    def _format_score(self, score: Score, indent: str = "") -> str:
        """
        Format a score object as markdown with proper styling.

        Args:
            score (Score): The score object to format.
            indent (str): String prefix for indentation. Defaults to "".

        Returns:
            str: Formatted markdown representation of the score.
        """
        lines: list[str] = []

        score_value = score.get_value()
        if isinstance(score_value, bool):
            value_str = str(score_value)
        elif isinstance(score_value, (int, float)):
            value_str = f"**{score_value:.2f}**" if isinstance(score_value, float) else f"**{score_value}**"
        else:
            value_str = f"**{score_value}**"

        lines.append(f"{indent}- **Score Type:** {score.score_type}")
        lines.append(f"{indent}- **Value:** {value_str}")
        category_str = ", ".join(score.score_category) if score.score_category else "N/A"
        lines.append(f"{indent}- **Category:** {category_str}")

        if score.score_rationale:
            rationale_lines = score.score_rationale.split("\n")
            if len(rationale_lines) > 1:
                lines.append(f"{indent}- **Rationale:**")
                lines.extend(f"{indent}  {line}" for line in rationale_lines)
            else:
                lines.append(f"{indent}- **Rationale:** {score.score_rationale}")

        if score.score_metadata:
            lines.append(f"{indent}- **Metadata:** `{score.score_metadata}`")

        return "\n".join(lines)

    async def render_async(self, scores: list[Score], *, indent: str = "") -> str:
        """
        Render a list of scores as markdown and return as a string.

        Args:
            scores (list[Score]): The scores to render.
            indent (str): String prefix for indentation. Defaults to "".

        Returns:
            str: The rendered scores markdown text.
        """
        return "\n".join(self._format_score(score, indent=indent) for score in scores)
