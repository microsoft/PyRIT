# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.display_response import display_image_response
from pyrit.memory import CentralMemory
from pyrit.models import Message, Score
from pyrit.printer.attack_result.console import ConsoleAttackPrinterBase


class ConsoleAttackResultPrinter(ConsoleAttackPrinterBase):
    """
    Framework console printer for attack results.

    Thin subclass that implements data-fetching via CentralMemory.
    All formatting logic lives in ConsoleAttackPrinterBase.
    """

    def __init__(self, *, width: int = 100, indent_size: int = 2, enable_colors: bool = True) -> None:
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        super().__init__(width=width, indent_size=indent_size, enable_colors=enable_colors)
        self._memory = CentralMemory.get_memory_instance()

    async def get_conversation_async(self, conversation_id: str) -> list[Message]:
        """
        Fetch conversation messages from CentralMemory.

        Args:
            conversation_id (str): The conversation ID to fetch.

        Returns:
            list[Message]: The conversation messages.
        """
        return list(self._memory.get_conversation(conversation_id=conversation_id))

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores from CentralMemory.

        Args:
            prompt_ids (list[str]): The message piece IDs to fetch scores for.

        Returns:
            list[Score]: The scores.
        """
        return self._memory.get_prompt_scores(prompt_ids=prompt_ids)

    async def display_image_async(self, piece: object) -> None:
        """
        Display images using PIL/IPython in notebook environments.

        Args:
            piece: The message piece that may contain image data.
        """
        await display_image_response(piece)
