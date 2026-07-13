# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import contextlib
import logging
import os
from pathlib import Path

from pyrit.models import Message, MessagePiece, Score
from pyrit.output.conversation.base import ConversationPrinterBase
from pyrit.output.score.markdown import MarkdownScorePrinter
from pyrit.output.sink import Sink

logger = logging.getLogger(__name__)


class MarkdownConversationPrinter(ConversationPrinterBase):
    """
    Markdown printer for conversation message histories.

    Contains all formatting logic. Subclasses implement ``_get_scores_async``
    for data fetching.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        score_printer: MarkdownScorePrinter | None = None,
        blur_images: bool = False,
        blur_radius: int = 20,
        blurred_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """
        Initialize the markdown conversation printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            score_printer (MarkdownScorePrinter | None): Score printer for inline score rendering.
                Defaults to a new MarkdownScorePrinter with matching sink.
            blur_images (bool): If True, write a blurred copy of each referenced image
                and emit the markdown link pointing at the blurred copy. Defaults to False.

                Note: blurred files are cached by path. If the original image content
                changes but the blurred file already exists, the stale blurred copy
                is reused. Callers are responsible for cleaning up blurred artifacts.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
            blurred_dir (str | PathLike | None): Directory to write blurred copies into.
                When None (default), blurred files are written as ``<stem>_blurred.png``
                next to the original. When set, blurred files are written under this
                directory using the original basename plus ``_blurred.png``.
        """
        super().__init__(sink=sink)
        self._score_printer = score_printer or MarkdownScorePrinter(sink=sink)
        self._blur_images = blur_images
        self._blur_radius = blur_radius
        self._blurred_dir = os.fspath(blurred_dir) if blurred_dir is not None else None

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_trace: bool = False,
    ) -> str:
        """
        Render a list of messages as markdown and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Accepted for interface compatibility. Unused.

        Returns:
            str: The rendered conversation markdown text.
        """
        if not messages:
            return "*No messages to display*\n"

        markdown_lines: list[str] = []
        turn_number = 0

        for message in messages:
            if not message.message_pieces:
                continue

            message_role = message.get_piece().api_role

            if message_role == "system":
                markdown_lines.extend(self._format_system_message(message))
            elif message_role == "user":
                turn_number += 1
                markdown_lines.extend(await self._format_user_message_async(message=message, turn_number=turn_number))
            else:
                markdown_lines.extend(await self._format_assistant_message_async(message=message))

            if include_scores:
                markdown_lines.extend(await self._format_message_scores_async(message))

        return "\n".join(markdown_lines)

    def _format_system_message(self, message: Message) -> list[str]:
        """
        Format a system message as markdown.

        Args:
            message (Message): The system message to format.

        Returns:
            list[str]: Markdown strings for the system message.
        """
        lines = ["\n### System Message\n"]
        lines.extend(f"{piece.converted_value}\n" for piece in message.message_pieces)
        return lines

    async def _format_user_message_async(self, *, message: Message, turn_number: int) -> list[str]:
        """
        Format a user message as markdown with turn numbering.

        Args:
            message (Message): The user message to format.
            turn_number (int): The conversation turn number.

        Returns:
            list[str]: Markdown strings for the user message.
        """
        lines = [f"\n### Turn {turn_number}\n", "#### User\n"]

        for piece in message.message_pieces:
            lines.extend(await self._format_piece_content_async(piece=piece, show_original=True))

        return lines

    async def _format_assistant_message_async(self, *, message: Message) -> list[str]:
        """
        Format an assistant response message as markdown.

        Args:
            message (Message): The response message to format.

        Returns:
            list[str]: Markdown strings for the response message.
        """
        lines: list[str] = []
        piece = message.message_pieces[0]
        role_name = "Assistant (Simulated)" if piece.is_simulated else piece.api_role.capitalize()

        lines.append(f"\n#### {role_name}\n")

        for piece in message.message_pieces:
            lines.extend(await self._format_piece_content_async(piece=piece, show_original=False))

        return lines

    async def _format_piece_content_async(self, *, piece: MessagePiece, show_original: bool) -> list[str]:
        """
        Format a single piece content based on its data type.

        Args:
            piece (MessagePiece): The message piece to format.
            show_original (bool): Whether to show original value if different.

        Returns:
            list[str]: Markdown lines for this piece.
        """
        if piece.converted_value_data_type == "image_path":
            return self._format_image_content(image_path=piece.converted_value)
        if piece.converted_value_data_type == "audio_path":
            return self._format_audio_content(audio_path=piece.converted_value)
        if piece.has_error():
            return self._format_error_content(piece=piece)
        return self._format_text_content(piece=piece, show_original=show_original)

    def _format_text_content(self, *, piece: MessagePiece, show_original: bool) -> list[str]:
        """
        Format regular text content.

        Args:
            piece (MessagePiece): The message piece containing the text.
            show_original (bool): Whether to show original value if different.

        Returns:
            list[str]: Markdown lines for the text content.
        """
        lines: list[str] = []

        if show_original and piece.converted_value != piece.original_value:
            lines.append("**Original:**\n")
            lines.append(f"{piece.original_value}\n")
            lines.append("\n**Converted:**\n")

        lines.append(f"{piece.converted_value}\n")

        return lines

    def _format_image_content(self, *, image_path: str) -> list[str]:
        """
        Format image content as markdown.

        When ``blur_images`` is True and the blur succeeds, the markdown links to
        the blurred copy. When ``blur_images`` is True and the blur fails, a
        plain-text link to the original is emitted (not an inline image) so the
        reviewer is not silently exposed to the unblurred image.

        Args:
            image_path (str): The path to the image file.

        Returns:
            list[str]: Markdown lines for the image.
        """
        if self._blur_images:
            blurred = self._maybe_blur_image_on_disk(image_path=image_path)
            if blurred is None:
                # Blur was requested but failed — render a text link, not an inline image.
                link = self._format_link_path(image_path)
                return [f"[image (blur failed — original)]({link})\n"]
            display_path = blurred
        else:
            display_path = image_path

        posix_path = self._format_link_path(display_path)
        return [f"![Image]({posix_path})\n"]

    @staticmethod
    def _format_link_path(path: str) -> str:
        """Return a markdown-friendly link path for notebook renderers."""
        path_obj = Path(path).resolve()
        cwd = Path.cwd().resolve()
        try:
            # Prefer relative links (including ".." segments) so notebook markdown
            # renderers in VS Code/Jupyter can resolve local files consistently.
            relative_path = os.path.relpath(path_obj, cwd)
        except ValueError:
            # Windows cross-drive paths cannot be relativized; use a file URI
            # instead of a bare absolute path like "C:/..." that markdown often
            # treats as a malformed URL scheme.
            try:
                return path_obj.as_uri()
            except ValueError:
                return str(path_obj).replace("\\", "/")
        return relative_path.replace("\\", "/")

    def _maybe_blur_image_on_disk(self, *, image_path: str) -> str | None:
        """
        Produce a blurred copy of ``image_path`` and return its path.

        By default the blurred file is written as ``<stem>_blurred.png`` next to the
        original. When ``blurred_dir`` was supplied to the constructor, the blurred
        file is written under that directory using the original basename plus
        ``_blurred.png``. Existing blurred files are reused (cached by path). The
        write is atomic — bytes are written to a temp sibling then ``os.replace``\\d
        into place — so concurrent renders cannot observe a partial file. On any
        failure ``None`` is returned and a warning is logged so the caller can
        render a fail-safe link to the original instead of the original image.

        Args:
            image_path (str): The path to the source image file.

        Returns:
            str | None: The path to the blurred image, or ``None`` on failure.
        """
        try:
            blurred_path = Path(self._blurred_destination(image_path=image_path))
            if blurred_path.exists():
                logger.debug(f"Reusing cached blurred image at {blurred_path}")
                return str(blurred_path)

            blurred_path.parent.mkdir(parents=True, exist_ok=True)

            from pyrit.output._image_utils import blur_image_bytes

            with open(image_path, "rb") as f:
                original_bytes = f.read()
            blurred_bytes = blur_image_bytes(image_bytes=original_bytes, radius=self._blur_radius)

            temp_path = blurred_path.parent / f"{blurred_path.name}.tmp.{os.getpid()}"
            try:
                with open(temp_path, "wb") as f:
                    f.write(blurred_bytes)
                os.replace(temp_path, blurred_path)
            except Exception:
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()
                raise
            return str(blurred_path)
        except Exception as exc:
            logger.warning(f"Failed to write blurred image for {image_path}; falling back to a text link. Error: {exc}")
            return None

    def _blurred_destination(self, *, image_path: str) -> str:
        """
        Compute the destination path for a blurred copy of ``image_path``.

        Args:
            image_path (str): The path to the source image file.

        Returns:
            str: Path to the blurred file (sibling by default, or under ``blurred_dir``).
        """
        image_path_obj = Path(image_path)
        directory = Path(self._blurred_dir) if self._blurred_dir is not None else image_path_obj.parent
        return str(directory / f"{image_path_obj.stem}_blurred.png")

    def _format_audio_content(self, *, audio_path: str) -> list[str]:
        """
        Format audio content as HTML5 audio player.

        Args:
            audio_path (str): The path to the audio file.

        Returns:
            list[str]: Markdown lines for the audio player.
        """
        lines: list[str] = []
        lines.append("<audio controls>")
        audio_type = self._get_audio_mime_type(audio_path=audio_path)
        lines.append(f'<source src="{audio_path}" type="{audio_type}">')
        lines.append("Your browser does not support the audio element.")
        lines.append("</audio>\n")
        return lines

    @staticmethod
    def _get_audio_mime_type(*, audio_path: str) -> str:
        """
        Determine the MIME type for an audio file based on its file extension.

        Args:
            audio_path (str): The path to the audio file.

        Returns:
            str: The appropriate MIME type for the audio file.
        """
        if audio_path.lower().endswith(".wav"):
            return "audio/wav"
        if audio_path.lower().endswith(".ogg"):
            return "audio/ogg"
        if audio_path.lower().endswith(".m4a"):
            return "audio/mp4"
        return "audio/mpeg"

    def _format_error_content(self, *, piece: MessagePiece) -> list[str]:
        """
        Format error response content with proper styling.

        Args:
            piece (MessagePiece): The message piece containing the error.

        Returns:
            list[str]: Markdown lines for the error response.
        """
        lines: list[str] = []
        lines.append("**Error Response:**\n")
        lines.append(f"*Error Type: {piece.response_error}*\n")
        lines.append("```json")
        lines.append(piece.converted_value)
        lines.append("```\n")
        return lines

    async def _format_message_scores_async(self, message: Message) -> list[str]:
        """
        Format scores for all pieces in a message as markdown.

        Args:
            message (Message): The message containing pieces to format scores for.

        Returns:
            list[str]: Markdown strings for the scores.
        """
        lines: list[str] = []
        for piece in message.message_pieces:
            scores = await self._get_scores_async(prompt_ids=[str(piece.id)])
            if scores:
                lines.append("\n##### Scores\n")
                lines.extend(self._score_printer._format_score(score, indent="") for score in scores)
                lines.append("")
        return lines


class MarkdownConversationMemoryPrinter(MarkdownConversationPrinter):
    """
    Framework markdown printer for conversation histories.

    Implements data-fetching via CentralMemory (deferred import).
    All formatting logic lives in MarkdownConversationPrinter.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        score_printer: MarkdownScorePrinter | None = None,
        blur_images: bool = False,
        blur_radius: int = 20,
        blurred_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """
        Initialize the markdown conversation printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            score_printer (MarkdownScorePrinter | None): Score printer for inline score rendering.
            blur_images (bool): If True, write a blurred copy next to each image and
                link to it instead of the original. Defaults to False.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
            blurred_dir (str | PathLike | None): Directory to write blurred copies into.
                Defaults to None (sibling of the original).
        """
        super().__init__(
            sink=sink,
            score_printer=score_printer,
            blur_images=blur_images,
            blur_radius=blur_radius,
            blurred_dir=blurred_dir,
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
        Render a list of messages as markdown and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Accepted for interface compatibility. Unused.

        Returns:
            str: The rendered conversation markdown text.
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
