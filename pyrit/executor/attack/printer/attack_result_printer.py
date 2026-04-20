# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.models import AttackOutcome, AttackResult


class AttackResultPrinter(ABC):
    """
    Abstract base class for printing attack results.

    This interface defines the contract for printing attack results in various formats.
    Implementations can render results to console, logs, files, or other outputs.
    """

    @abstractmethod
    async def print_result_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> None:
        """
        Print the complete attack result.

        Args:
            result (AttackResult): The attack result to print
            include_auxiliary_scores (bool): Whether to include auxiliary scores in the output.
                Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations.
                For each pruned conversation, only the last message and its score are shown.
                Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial
                conversation (the red teaming LLM's reasoning). Only shown for successful
                attacks to avoid overwhelming output. Defaults to False.
        """
        pass

    @abstractmethod
    async def print_conversation_async(self, result: AttackResult, *, include_scores: bool = False) -> None:
        """
        Print only the conversation history.

        Args:
            result (AttackResult): The attack result containing the conversation to print
            include_scores (bool): Whether to include scores in the output.
                Defaults to False.
        """
        pass

    @abstractmethod
    async def print_summary_async(self, result: AttackResult) -> None:
        """
        Print a summary of the attack result without the full conversation.

        Args:
            result (AttackResult): The attack result to summarize
        """
        pass

    @staticmethod
    def _get_outcome_icon(outcome: AttackOutcome) -> str:
        """
        Get an icon for an outcome.

        Maps AttackOutcome enum values to appropriate Unicode emoji icons.

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

        Converts milliseconds to appropriate units (ms, s, or m + s) based
        on the magnitude of the value.

        Args:
            milliseconds (int): Time duration in milliseconds. Should be
                non-negative.

        Returns:
            str: Formatted time string (e.g., "500ms", "2.50s", "1m 30s").

        Raises:
            TypeError: If milliseconds is not an integer.
            ValueError: If milliseconds is negative.
        """
        if milliseconds < 1000:
            return f"{milliseconds}ms"

        if milliseconds < 60000:
            return f"{milliseconds / 1000:.2f}s"

        minutes = milliseconds // 60000
        seconds = (milliseconds % 60000) / 1000
        return f"{minutes}m {seconds:.0f}s"
