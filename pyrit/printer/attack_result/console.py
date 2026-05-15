# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import textwrap
from datetime import datetime, timezone
from typing import Any

from colorama import Back, Fore, Style

from pyrit.models import AttackOutcome, AttackResult, ConversationType, Message, MessagePiece, Score
from pyrit.printer.attack_result.base import AttackResultPrinterBase


class ConsoleAttackPrinterBase(AttackResultPrinterBase):
    """
    Console printer base for attack results with enhanced formatting.

    Contains all formatting logic. Subclasses implement get_conversation_async
    and get_scores_async for data fetching.
    """

    def __init__(self, *, width: int = 100, indent_size: int = 2, enable_colors: bool = True) -> None:
        """
        Initialize the console printer.

        Args:
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

    def _print_colored(self, text: str, *colors: str) -> None:
        """
        Print text with color formatting if colors are enabled.

        Args:
            text (str): The text to print.
            *colors: Variable number of colorama color constants to apply.
        """
        if self._enable_colors and colors:
            color_prefix = "".join(colors)
            print(f"{color_prefix}{text}{Style.RESET_ALL}")
        else:
            print(text)

    async def print_result_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> None:
        """
        Print the complete attack result to console.

        Args:
            result (AttackResult): The attack result to print.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.
        """
        self._print_header(result)
        await self.print_summary_async(result)

        self._print_section_header("Conversation History with Objective Target")
        await self.print_conversation_async(result, include_scores=include_auxiliary_scores)

        if include_pruned_conversations:
            await self._print_pruned_conversations_async(result)

        if include_adversarial_conversation:
            await self._print_adversarial_conversation_async(result)

        if result.metadata:
            self._print_metadata(result.metadata)

        self._print_footer()

    async def print_conversation_async(
        self, result: AttackResult, *, include_scores: bool = False, include_reasoning_trace: bool = False
    ) -> None:
        """
        Print the conversation history to console.

        Args:
            result (AttackResult): The attack result containing the conversation_id.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include model reasoning trace. Defaults to False.
        """
        if not result.conversation_id:
            self._print_colored(f"{self._indent} No conversation ID available", Fore.YELLOW)
            return

        messages = await self.get_conversation_async(result.conversation_id)

        if not messages:
            self._print_colored(f"{self._indent} No conversation found for ID: {result.conversation_id}", Fore.YELLOW)
            return

        await self.print_messages_async(
            messages=messages,
            include_scores=include_scores,
            include_reasoning_trace=include_reasoning_trace,
        )

    async def print_messages_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_trace: bool = False,
    ) -> None:
        """
        Print a list of messages to console with enhanced formatting.

        Args:
            messages (list): List of Message objects to print.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include model reasoning trace. Defaults to False.
        """
        if not messages:
            self._print_colored(f"{self._indent} No messages to display.", Fore.YELLOW)
            return

        turn_number = 0
        for message in messages:
            if message.api_role == "user":
                turn_number += 1
                print()
                self._print_colored("─" * self._width, Fore.BLUE)
                self._print_colored(f"🔹 Turn {turn_number} - USER", Style.BRIGHT, Fore.BLUE)
                self._print_colored("─" * self._width, Fore.BLUE)
            elif message.api_role == "system":
                print()
                self._print_colored("─" * self._width, Fore.MAGENTA)
                self._print_colored("🔧 SYSTEM", Style.BRIGHT, Fore.MAGENTA)
                self._print_colored("─" * self._width, Fore.MAGENTA)
            else:
                print()
                self._print_colored("─" * self._width, Fore.YELLOW)
                role_label = "ASSISTANT (SIMULATED)" if message.is_simulated else message.api_role.upper()
                self._print_colored(f"🔸 {role_label}", Style.BRIGHT, Fore.YELLOW)
                self._print_colored("─" * self._width, Fore.YELLOW)

            for piece in message.message_pieces:
                if piece.original_value_data_type == "reasoning":
                    if include_reasoning_trace:
                        summary_text = self._extract_reasoning_summary(piece.original_value)
                        if summary_text:
                            self._print_colored(f"{self._indent}💭 Reasoning Summary:", Style.DIM, Fore.CYAN)
                            self._print_wrapped_text(summary_text, Fore.CYAN)
                            print()
                    continue

                if piece.is_blocked():
                    self._print_colored(f"{self._indent}🚫 BLOCKED BY TARGET", Style.BRIGHT, Fore.RED)
                    partial_content = piece.prompt_metadata.get("partial_content")
                    if partial_content:
                        self._print_colored(
                            f"{self._indent}📝 Partial content (before filter triggered):",
                            Style.DIM,
                            Fore.CYAN,
                        )
                        self._print_wrapped_text(str(partial_content), Fore.YELLOW)
                    else:
                        self._print_colored(
                            f"{self._indent}Content was blocked by the target's content filter.",
                            Style.DIM,
                            Fore.RED,
                        )

                elif piece.converted_value != piece.original_value:
                    self._print_colored(f"{self._indent} Original:", Fore.CYAN)
                    self._print_wrapped_text(piece.original_value, Fore.WHITE)
                    print()
                    self._print_colored(f"{self._indent} Converted:", Fore.CYAN)
                    self._print_wrapped_text(piece.converted_value, Fore.WHITE)
                elif piece.api_role == "user":
                    self._print_wrapped_text(piece.converted_value, Fore.BLUE)
                elif piece.api_role == "system":
                    self._print_wrapped_text(piece.converted_value, Fore.MAGENTA)
                else:
                    self._print_wrapped_text(piece.converted_value, Fore.YELLOW)

                await self.display_image_async(piece)

                if include_scores:
                    scores = await self.get_scores_async(prompt_ids=[str(piece.id)])
                    if scores:
                        print()
                        self._print_colored(f"{self._indent}📊 Scores:", Style.DIM, Fore.MAGENTA)
                        for score in scores:
                            self._print_score(score)

        print()
        self._print_colored("─" * self._width, Fore.BLUE)

    def _extract_reasoning_summary(self, reasoning_value: str) -> str:
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

    async def print_summary_async(self, result: AttackResult) -> None:
        """
        Print a summary of the attack result.

        Args:
            result (AttackResult): The attack result to summarize.
        """
        self._print_section_header("Attack Summary")

        self._print_colored(f"{self._indent}📋 Basic Information", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Objective: {result.objective}", Fore.CYAN)

        attack_type = "Unknown"
        attack_strategy_id = result.get_attack_strategy_identifier()
        if attack_strategy_id:
            attack_type = attack_strategy_id.class_name

        self._print_colored(f"{self._indent * 2}• Attack Type: {attack_type}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Conversation ID: {result.conversation_id}", Fore.CYAN)

        print()
        self._print_colored(f"{self._indent}⚡ Execution Metrics", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Turns Executed: {result.executed_turns}", Fore.GREEN)
        self._print_colored(
            f"{self._indent * 2}• Execution Time: {self._format_time(result.execution_time_ms)}", Fore.GREEN
        )

        print()
        self._print_colored(f"{self._indent}🎯 Outcome", Style.BRIGHT)
        outcome_icon = self._get_outcome_icon(result.outcome)
        outcome_color = self._get_outcome_color(result.outcome)
        self._print_colored(f"{self._indent * 2}• Status: {outcome_icon} {result.outcome.value.upper()}", outcome_color)

        if result.outcome_reason:
            self._print_colored(f"{self._indent * 2}• Reason: {result.outcome_reason}", Fore.WHITE)

        if result.last_score:
            print()
            self._print_colored(f"{self._indent} Final Score", Style.BRIGHT)
            self._print_score(result.last_score, indent_level=2)

    def _print_header(self, result: AttackResult) -> None:
        """
        Print the header with outcome-based coloring.

        Args:
            result (AttackResult): The attack result containing the outcome.
        """
        color = self._get_outcome_color(result.outcome)
        icon = self._get_outcome_icon(result.outcome)

        print()
        self._print_colored("═" * self._width, color)
        header_text = f"{icon} ATTACK RESULT: {result.outcome.value.upper()} {icon}"
        self._print_colored(header_text.center(self._width), Style.BRIGHT, color)
        self._print_colored("═" * self._width, color)

    def _print_footer(self) -> None:
        """Print a footer with timestamp."""
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print()
        self._print_colored("─" * self._width, Style.DIM, Fore.WHITE)
        footer_text = f"Report generated at: {timestamp} UTC"
        self._print_colored(footer_text.center(self._width), Style.DIM, Fore.WHITE)

    def _print_section_header(self, title: str) -> None:
        """
        Print a section header with consistent styling.

        Args:
            title (str): The title text to display.
        """
        print()
        self._print_colored(f" {title} ", Style.BRIGHT, Back.BLUE, Fore.WHITE)
        self._print_colored("─" * self._width, Fore.BLUE)

    def _print_metadata(self, metadata: dict[str, Any]) -> None:
        """
        Print metadata in a formatted way.

        Args:
            metadata (dict[str, Any]): Dictionary containing metadata key-value pairs.
        """
        self._print_section_header("Additional Metadata")
        for key, value in metadata.items():
            self._print_colored(f"{self._indent}• {key}: {value}", Fore.CYAN)

    def _print_score(self, score: Score, indent_level: int = 3) -> None:
        """
        Print a score with proper formatting.

        Args:
            score (Score): Score object to be printed.
            indent_level (int): Number of indent units to apply. Defaults to 3.
        """
        indent = self._indent * indent_level
        scorer_name = score.scorer_class_identifier.class_name
        print(f"{indent}Scorer: {scorer_name}")
        self._print_colored(f"{indent}• Category: {score.score_category or 'N/A'}", Fore.LIGHTMAGENTA_EX)
        self._print_colored(f"{indent}• Type: {score.score_type}", Fore.CYAN)

        if score.score_type == "true_false":
            score_color = Fore.GREEN if score.get_value() else Fore.RED
        else:
            score_color = Fore.YELLOW

        self._print_colored(f"{indent}• Value: {score.score_value}", score_color)

        if score.score_rationale:
            print(f"{indent}• Rationale:")
            rationale_wrapper = textwrap.TextWrapper(
                width=self._width - len(indent) - 2,
                initial_indent=indent + "  ",
                subsequent_indent=indent + "  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
            lines = score.score_rationale.split("\n")
            for line in lines:
                if line.strip():
                    wrapped_lines = rationale_wrapper.wrap(line)
                    for wrapped_line in wrapped_lines:
                        self._print_colored(wrapped_line, Fore.WHITE)
                else:
                    self._print_colored(f"{indent}  ")

    def _print_wrapped_text(self, text: str, color: str) -> None:
        """
        Print text with proper wrapping and indentation, preserving newlines.

        Args:
            text (str): The text to print.
            color (str): Colorama color constant to apply.
        """
        text_wrapper = textwrap.TextWrapper(
            width=self._width - len(self._indent),
            initial_indent="",
            subsequent_indent=self._indent,
            break_long_words=True,
            break_on_hyphens=True,
            expand_tabs=False,
            replace_whitespace=False,
        )

        lines = text.split("\n")
        for line_num, line in enumerate(lines):
            if line.strip():
                wrapped_lines = text_wrapper.wrap(line)
                for i, wrapped_line in enumerate(wrapped_lines):
                    if line_num == 0 and i == 0:
                        self._print_colored(f"{self._indent}{wrapped_line}", color)
                    else:
                        self._print_colored(f"{self._indent * 2}{wrapped_line}", color)
            else:
                self._print_colored(f"{self._indent}", color)

    async def _print_pruned_conversations_async(self, result: AttackResult) -> None:
        """
        Print pruned conversations showing only the last message and score for each.

        Args:
            result (AttackResult): The attack result containing related conversations.
        """
        pruned_refs = result.get_conversations_by_type(ConversationType.PRUNED)

        if not pruned_refs:
            return

        self._print_section_header(f"Pruned Conversations ({len(pruned_refs)} total)")

        for idx, ref in enumerate(pruned_refs, 1):
            print()
            self._print_colored("─" * self._width, Fore.RED)
            label = f"🗑️ PRUNED #{idx}"
            if ref.description:
                label += f" - {ref.description}"
            self._print_colored(label, Style.BRIGHT, Fore.RED)
            self._print_colored("─" * self._width, Fore.RED)

            messages = await self.get_conversation_async(ref.conversation_id)

            if not messages:
                self._print_colored(
                    f"{self._indent}No messages found for conversation: {ref.conversation_id}", Fore.YELLOW
                )
                continue

            last_message = messages[-1]
            role_label = last_message.api_role.upper()
            self._print_colored(f"{self._indent}Last Message ({role_label}):", Style.BRIGHT, Fore.WHITE)

            for piece in last_message.message_pieces:
                self._print_wrapped_text(piece.converted_value, Fore.WHITE)

                scores = await self.get_scores_async(prompt_ids=[str(piece.id)])
                if scores:
                    print()
                    self._print_colored(f"{self._indent}📊 Score:", Style.DIM, Fore.MAGENTA)
                    for score in scores:
                        self._print_score(score)

        print()
        self._print_colored("─" * self._width, Fore.RED)

    async def _print_adversarial_conversation_async(self, result: AttackResult) -> None:
        """
        Print the adversarial conversation for the best-scoring attack branch.

        Args:
            result (AttackResult): The attack result containing related conversations.
        """
        adversarial_refs = result.get_conversations_by_type(ConversationType.ADVERSARIAL)

        if not adversarial_refs:
            return

        self._print_section_header("Adversarial Conversation (Red Team LLM)")

        best_adversarial_id = result.metadata.get("best_adversarial_conversation_id")
        if best_adversarial_id:
            adversarial_refs = [ref for ref in adversarial_refs if ref.conversation_id == best_adversarial_id]
            if adversarial_refs:
                self._print_colored(
                    f"{self._indent}📌 Showing best-scoring branch's adversarial conversation",
                    Style.DIM,
                    Fore.CYAN,
                )

        for ref in adversarial_refs:
            if ref.description:
                self._print_colored(f"{self._indent}📝 {ref.description}", Style.DIM, Fore.CYAN)

            messages = await self.get_conversation_async(ref.conversation_id)

            if not messages:
                self._print_colored(
                    f"{self._indent}No messages found for conversation: {ref.conversation_id}", Fore.YELLOW
                )
                continue

            await self.print_messages_async(messages=messages, include_scores=False)

    def _get_outcome_color(self, outcome: AttackOutcome) -> str:
        """
        Get the color for an outcome.

        Args:
            outcome (AttackOutcome): The attack outcome enum value.

        Returns:
            str: Colorama color constant.
        """
        return str(
            {
                AttackOutcome.SUCCESS: Fore.GREEN,
                AttackOutcome.FAILURE: Fore.RED,
                AttackOutcome.UNDETERMINED: Fore.YELLOW,
            }.get(outcome, Fore.WHITE)
        )

    async def display_image_async(self, piece: MessagePiece) -> None:
        """Display images using PIL/IPython in notebook environments."""
        from pyrit.common.display_response import display_image_response

        await display_image_response(piece)


class ConsoleAttackMemoryPrinter(ConsoleAttackPrinterBase):
    """
    Framework console printer for attack results.

    Implements data-fetching via CentralMemory (deferred import).
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
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def get_conversation_async(self, conversation_id: str) -> list[Message]:
        """
        Fetch conversation messages from CentralMemory.

        Returns:
            list[Message]: The conversation messages.
        """
        return list(self._memory.get_conversation(conversation_id=conversation_id))

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores from CentralMemory.

        Returns:
            list[Score]: The scores.
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))
