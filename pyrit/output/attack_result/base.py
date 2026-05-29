# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod

from pyrit.models import AttackOutcome, Message, Score
from pyrit.output.base import PrinterBase


class AttackResultPrinterBase(PrinterBase):
    """
    Abstract base class for printing attack results.

    Contains all formatting logic. Subclasses only need to implement
    the data-fetching methods: get_conversation_async and get_scores_async.

    Framework implementations fetch data via CentralMemory.
    Thin-client implementations can fetch data via REST endpoints.
    """

    @abstractmethod
    async def _get_conversation_async(self, conversation_id: str) -> list[Message]:
        """
        Fetch conversation messages for a given conversation ID.

        Args:
            conversation_id (str): The conversation ID to fetch messages for.

        Returns:
            list[Message]: The conversation messages.
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

    @staticmethod
    def _get_outcome_icon(outcome: AttackOutcome) -> str:
        """
        Get an icon for an outcome.

        Args:
            outcome (AttackOutcome): The attack outcome enum value.

        Returns:
            str: Unicode emoji string.
        """
        return {
            AttackOutcome.SUCCESS: "\u2705",
            AttackOutcome.FAILURE: "\u274c",
            AttackOutcome.UNDETERMINED: "\u2753",
        }.get(outcome, "")

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        """
        Format time in a human-readable way.

        Args:
            milliseconds (int): Time duration in milliseconds.

        Returns:
            str: Formatted time string (e.g., "500ms", "2.50s", "1m 30s").
        """
        if milliseconds < 1000:
            return f"{milliseconds}ms"

        if milliseconds < 60000:
            return f"{milliseconds / 1000:.2f}s"

        minutes = milliseconds // 60000
        seconds = (milliseconds % 60000) / 1000
        return f"{minutes}m {seconds:.0f}s"
