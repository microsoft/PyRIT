# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod

from pyrit.models import Message, MessagePiece, Score
from pyrit.output.base import PrinterBase


class ConversationPrinterBase(PrinterBase):
    """
    Abstract base class for printing conversation message histories.

    Subclasses implement data-fetching methods (``_get_scores_async``,
    ``_display_image_async``) and rendering via ``render_async``.
    """

    @abstractmethod
    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores for given prompt piece IDs.

        Args:
            prompt_ids (list[str]): The message piece IDs to fetch scores for.

        Returns:
            list[Score]: The scores associated with the given piece IDs.
        """

    async def _display_image_async(self, piece: MessagePiece) -> None:  # noqa: B027
        """
        Display an image from a message piece. No-op by default.

        Args:
            piece (MessagePiece): The message piece that may contain image data.
        """

    @abstractmethod
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
