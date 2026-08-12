# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import textwrap

from colorama import Fore, Style

from pyrit.models import Message, MessagePiece, Score
from pyrit.output._formatting import _PrettyPrinterMixin
from pyrit.output.conversation.base import ConversationPrinterBase
from pyrit.output.score.pretty import PrettyScorePrinter
from pyrit.output.sink import Sink

logger = logging.getLogger(__name__)


class PrettyConversationPrinter(_PrettyPrinterMixin, ConversationPrinterBase):
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
        blur_images: bool = False,
        blur_radius: int = 20,
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
            blur_images (bool): If True, apply a Gaussian blur to image outputs before
                displaying them. Useful for reducing reviewer exposure to unsafe imagery
                while still allowing the general content to be inspected. Defaults to False.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors
        self._blur_images = blur_images
        self._blur_radius = blur_radius
        self._score_printer = score_printer or PrettyScorePrinter(
            sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors
        )

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_summaries: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to include reasoning summaries. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
        if not messages:
            return self._format_colored(f"{self._indent} No messages to display.", Fore.YELLOW)

        lines: list[str] = []
        image_pieces: list[MessagePiece] = []
        turn_number = 0
        for message in messages:
            pieces = self._get_renderable_pieces(
                message=message,
                include_reasoning_summaries=include_reasoning_summaries,
            )
            if not pieces:
                continue

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

            reasoning_rendered = False
            response_heading_rendered = False
            for piece in pieces:
                if self._is_reasoning_piece(piece=piece):
                    rendered = self._render_reasoning_summary(self._get_reasoning_value(piece=piece))
                    if rendered:
                        lines.append(rendered)
                        reasoning_rendered = True
                    continue

                if reasoning_rendered and not response_heading_rendered and message.api_role == "assistant":
                    lines.append(self._render_response_heading())
                    response_heading_rendered = True

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

    def _render_reasoning_summary(self, reasoning_value: str) -> str:
        """
        Render a provider-generated reasoning summary in subdued gray.

        Args:
            reasoning_value (str): Serialized OpenAI Responses reasoning item.

        Returns:
            str: The labeled reasoning block, or a warning when extraction fails.
        """
        try:
            summary = self._extract_reasoning_summary(reasoning_value)
        except ValueError:
            return "".join(
                [
                    self._format_colored(
                        f"{self._indent}{self._REASONING_RENDER_WARNING}",
                        Style.BRIGHT,
                        Fore.RED,
                    ),
                    self._format_colored("", Fore.RED),
                ]
            )

        if not summary:
            summary = "[No reasoning summary was returned by the provider.]"

        label = "Provider-generated reasoning summary (not raw chain-of-thought)"

        return "".join(
            [
                self._format_colored(f"{self._indent}💭 Reasoning", Style.BRIGHT, Fore.LIGHTBLACK_EX),
                self._format_colored(f"{self._indent}{label}", Style.DIM, Fore.LIGHTBLACK_EX),
                self._render_wrapped_text(summary, Fore.LIGHTBLACK_EX),
                self._format_colored("", Fore.LIGHTBLACK_EX),
            ]
        )

    def _render_response_heading(self) -> str:
        """
        Render the boundary between reasoning and the model response.

        Returns:
            str: The formatted response heading.
        """
        return self._format_colored(f"{self._indent}💬 Response", Style.BRIGHT, Fore.YELLOW)


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
        blur_images: bool = False,
        blur_radius: int = 20,
    ) -> None:
        """
        Initialize the pretty conversation printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            score_printer (PrettyScorePrinter | None): Score printer for inline score rendering.
            blur_images (bool): If True, apply a Gaussian blur to image outputs before
                displaying them. Defaults to False.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
        """
        super().__init__(
            sink=sink,
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            score_printer=score_printer,
            blur_images=blur_images,
            blur_radius=blur_radius,
        )
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_summaries: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to include reasoning summaries. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
        return await super().render_async(
            messages, include_scores=include_scores, include_reasoning_summaries=include_reasoning_summaries
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

        Uses ``DataTypeSerializer.read_data_async`` for transparent storage access
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

        from pyrit.memory import ImagePathDataTypeSerializer

        try:
            serializer = ImagePathDataTypeSerializer(category="", prompt_text=piece.converted_value)
            image_bytes = await serializer.read_data_async()
        except Exception as e:
            logger.error(f"Failed to read image from {piece.converted_value}: {e}")
            return

        if self._blur_images:
            from pyrit.output._image_utils import blur_image_bytes

            image_bytes = blur_image_bytes(image_bytes=image_bytes, radius=self._blur_radius)

        from IPython.display import Image, display

        display(Image(data=image_bytes))
