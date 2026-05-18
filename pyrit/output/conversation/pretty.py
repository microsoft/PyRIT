# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import textwrap

from colorama import Fore, Style

from pyrit.models import Message, MessagePiece, Score
from pyrit.output.conversation.base import ConversationPrinterBase
from pyrit.output.score.pretty import PrettyScorePrinter
from pyrit.output.sink import Sink

logger = logging.getLogger(__name__)


class PrettyConversationPrinter(ConversationPrinterBase):
    """
    Pretty printer for conversation message histories with ANSI-colored formatting.

    Contains all formatting logic. Subclasses implement ``_get_scores_async``
    and ``_display_image_async`` for data fetching.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        score_printer: PrettyScorePrinter | None = None,
    ) -> None:
        """
        Initialize the pretty conversation printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            score_printer (PrettyScorePrinter | None): Score printer for inline score rendering.
                Defaults to a new PrettyScorePrinter with matching settings.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors
        self._score_printer = score_printer or PrettyScorePrinter(
            sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors
        )

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_trace: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include reasoning traces. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
        if not messages:
            return self._format_colored(f"{self._indent} No messages to display.", Fore.YELLOW)

        lines: list[str] = []
        image_pieces: list[MessagePiece] = []
        turn_number = 0
        for message in messages:
            if message.api_role == "user":
                turn_number += 1
                lines.append("\n")
                lines.append(self._format_colored("─" * self._width, Fore.BLUE))
                lines.append(self._format_colored(f"🔹 Turn {turn_number} - USER", Style.BRIGHT, Fore.BLUE))
                lines.append(self._format_colored("─" * self._width, Fore.BLUE))
            elif message.api_role == "system":
                lines.append("\n")
                lines.append(self._format_colored("─" * self._width, Fore.MAGENTA))
                lines.append(self._format_colored("🔧 SYSTEM", Style.BRIGHT, Fore.MAGENTA))
                lines.append(self._format_colored("─" * self._width, Fore.MAGENTA))
            else:
                lines.append("\n")
                lines.append(self._format_colored("─" * self._width, Fore.YELLOW))
                role_label = "ASSISTANT (SIMULATED)" if message.is_simulated else message.api_role.upper()
                lines.append(self._format_colored(f"🔸 {role_label}", Style.BRIGHT, Fore.YELLOW))
                lines.append(self._format_colored("─" * self._width, Fore.YELLOW))

            for piece in message.message_pieces:
                if piece.original_value_data_type == "reasoning":
                    if include_reasoning_trace:
                        summary_text = self._extract_reasoning_summary(piece.original_value)
                        if summary_text:
                            lines.append(
                                self._format_colored(f"{self._indent}💭 Reasoning Summary:", Style.DIM, Fore.CYAN)
                            )
                            lines.append(self._render_wrapped_text(summary_text, Fore.CYAN))
                            lines.append("\n")
                    continue

                if piece.is_blocked():
                    lines.append(self._format_colored(f"{self._indent}🚫 BLOCKED BY TARGET", Style.BRIGHT, Fore.RED))
                    partial_content = piece.prompt_metadata.get("partial_content")
                    if partial_content:
                        lines.append(
                            self._format_colored(
                                f"{self._indent}📝 Partial content (before filter triggered):",
                                Style.DIM,
                                Fore.CYAN,
                            )
                        )
                        lines.append(self._render_wrapped_text(str(partial_content), Fore.YELLOW))
                    else:
                        lines.append(
                            self._format_colored(
                                f"{self._indent}Content was blocked by the target's content filter.",
                                Style.DIM,
                                Fore.RED,
                            )
                        )

                elif piece.converted_value != piece.original_value:
                    lines.append(self._format_colored(f"{self._indent} Original:", Fore.CYAN))
                    lines.append(self._render_wrapped_text(piece.original_value, Fore.WHITE))
                    lines.append("\n")
                    lines.append(self._format_colored(f"{self._indent} Converted:", Fore.CYAN))
                    lines.append(self._render_wrapped_text(piece.converted_value, Fore.WHITE))
                elif piece.api_role == "user":
                    lines.append(self._render_wrapped_text(piece.converted_value, Fore.BLUE))
                elif piece.api_role == "system":
                    lines.append(self._render_wrapped_text(piece.converted_value, Fore.MAGENTA))
                else:
                    lines.append(self._render_wrapped_text(piece.converted_value, Fore.YELLOW))

                image_pieces.append(piece)

                if include_scores:
                    scores = await self._get_scores_async(prompt_ids=[str(piece.id)])
                    if scores:
                        lines.append("\n")
                        lines.append(self._format_colored(f"{self._indent}📊 Scores:", Style.DIM, Fore.MAGENTA))
                        lines.extend(self._score_printer._render_score(score) for score in scores)

        lines.append("\n")
        lines.append(self._format_colored("─" * self._width, Fore.BLUE))

        for piece in image_pieces:
            await self._display_image_async(piece)

        return "".join(lines)

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

    def _render_wrapped_text(self, text: str, color: str) -> str:
        """
        Render text with proper wrapping and indentation, preserving newlines.

        Args:
            text (str): The text to render.
            color (str): Colorama color constant to apply.

        Returns:
            str: The rendered wrapped text.
        """
        lines: list[str] = []
        text_wrapper = textwrap.TextWrapper(
            width=self._width - len(self._indent),
            initial_indent="",
            subsequent_indent=self._indent,
            break_long_words=True,
            break_on_hyphens=True,
            expand_tabs=False,
            replace_whitespace=False,
        )

        text_lines = text.split("\n")
        for line_num, line in enumerate(text_lines):
            if line.strip():
                wrapped_lines = text_wrapper.wrap(line)
                for i, wrapped_line in enumerate(wrapped_lines):
                    if line_num == 0 and i == 0:
                        lines.append(self._format_colored(f"{self._indent}{wrapped_line}", color))
                    else:
                        lines.append(self._format_colored(f"{self._indent * 2}{wrapped_line}", color))
            else:
                lines.append(self._format_colored(f"{self._indent}", color))

        return "".join(lines)

    @staticmethod
    def _extract_reasoning_summary(reasoning_value: str) -> str:
        """
        Extract human-readable summary text from a reasoning piece's JSON value.

        Args:
            reasoning_value (str): The JSON string stored in the reasoning piece.

        Returns:
            str: The concatenated summary text, or empty string if no summary is present.
        """
        try:
            data = json.loads(reasoning_value)
        except (json.JSONDecodeError, TypeError):
            return ""

        summary = data.get("summary") if isinstance(data, dict) else None
        if not summary or not isinstance(summary, list):
            return ""

        parts = [item.get("text", "") for item in summary if isinstance(item, dict) and item.get("text")]
        return "\n".join(parts)


class PrettyConversationMemoryPrinter(PrettyConversationPrinter):
    """
    Framework pretty printer for conversation histories.

    Implements data-fetching via CentralMemory (deferred import).
    All formatting logic lives in PrettyConversationPrinter.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        score_printer: PrettyScorePrinter | None = None,
    ) -> None:
        """
        Initialize the pretty conversation printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            score_printer (PrettyScorePrinter | None): Score printer for inline score rendering.
        """
        super().__init__(
            sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors, score_printer=score_printer
        )
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_trace: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include reasoning traces. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
        return await super().render_async(
            messages, include_scores=include_scores, include_reasoning_trace=include_reasoning_trace
        )

    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores from CentralMemory.

        Returns:
            list[Score]: The scores.
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))

    async def _display_image_async(self, piece: MessagePiece) -> None:
        """
        Display an image from a message piece in notebook environments.

        Uses ``DataTypeSerializer.read_data`` for transparent storage access
        (local disk or Azure Blob) and ``IPython.display.Image`` for rendering.
        No-op outside notebook environments.

        Args:
            piece (MessagePiece): The message piece that may contain image data.
        """
        if piece.converted_value_data_type != "image_path" or piece.response_error != "none":
            return

        from pyrit.common.notebook_utils import is_in_ipython_session

        if not is_in_ipython_session():
            return

        from pyrit.models.data_type_serializer import ImagePathDataTypeSerializer

        try:
            serializer = ImagePathDataTypeSerializer(category="", prompt_text=piece.converted_value)
            image_bytes = await serializer.read_data()
        except Exception as e:
            logger.error(f"Failed to read image from {piece.converted_value}: {e}")
            return

        from IPython.display import Image, display

        display(Image(data=image_bytes))
