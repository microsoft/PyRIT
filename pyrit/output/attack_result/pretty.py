# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timezone
from typing import Any

from colorama import Back, Fore, Style

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models import AttackOutcome, AttackResult, ConversationType, Message, Score
from pyrit.output.attack_result.base import AttackResultPrinterBase
from pyrit.output.conversation.pretty import PrettyConversationPrinter
from pyrit.output.score.pretty import PrettyScorePrinter
from pyrit.output.sink import Sink


class PrettyAttackResultPrinter(AttackResultPrinterBase):
    """
    Pretty printer for attack results with ANSI-colored formatting.

    Composes a conversation printer for message rendering and a score printer
    for inline score display. Subclasses implement data-fetching methods.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        width: int = 100,
        indent_size: int = 2,
        enable_colors: bool = True,
        conversation_printer: PrettyConversationPrinter | None = None,
        score_printer: PrettyScorePrinter | None = None,
    ) -> None:
        """
        Initialize the pretty printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            conversation_printer (PrettyConversationPrinter | None): Conversation printer.
                Defaults to a new PrettyConversationPrinter with matching settings.
            score_printer (PrettyScorePrinter | None): Score printer.
                Defaults to a new PrettyScorePrinter with matching settings.
        """
        super().__init__(sink=sink)
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors
        self._score_printer = score_printer or PrettyScorePrinter(
            sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors
        )
        self._conversation_printer = conversation_printer or PrettyConversationPrinter(
            sink=sink,
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            score_printer=self._score_printer,
        )

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

    async def render_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> str:
        """
        Render the complete attack result and return it as a string.

        Args:
            result (AttackResult): The attack result to render.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.

        Returns:
            str: The rendered attack result text.
        """
        lines: list[str] = []
        lines.append(self._render_header(result))
        lines.append(await self._render_summary_async(result))
        lines.append(self._render_section_header("Conversation History with Objective Target"))
        lines.append(await self._render_conversation_async(result, include_scores=include_auxiliary_scores))
        if include_pruned_conversations:
            lines.append(await self._render_pruned_conversations_async(result))
        if include_adversarial_conversation:
            lines.append(await self._render_adversarial_conversation_async(result))
        if result.metadata:
            lines.append(self._render_metadata(result.metadata))
        lines.append(self._render_footer())
        return "".join(lines)

    async def print_result_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> None:
        """Use ``write_async`` instead. This method is deprecated."""
        print_deprecation_message(old_item="print_result_async", new_item="write_async", removed_in="2.0")
        await self.write_async(
            result,
            include_auxiliary_scores=include_auxiliary_scores,
            include_pruned_conversations=include_pruned_conversations,
            include_adversarial_conversation=include_adversarial_conversation,
        )

    async def _render_conversation_async(
        self, result: AttackResult, *, include_scores: bool = False, include_reasoning_trace: bool = False
    ) -> str:
        """
        Render the conversation history as a formatted string.

        Args:
            result (AttackResult): The attack result containing the conversation_id.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include model reasoning trace. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
        if not result.conversation_id:
            return self._format_colored(f"{self._indent} No conversation ID available", Fore.YELLOW)

        messages = await self._get_conversation_async(result.conversation_id)

        if not messages:
            return self._format_colored(
                f"{self._indent} No conversation found for ID: {result.conversation_id}", Fore.YELLOW
            )

        return await self._conversation_printer.render_async(
            messages,
            include_scores=include_scores,
            include_reasoning_trace=include_reasoning_trace,
        )

    async def print_conversation_async(
        self, result: AttackResult, *, include_scores: bool = False, include_reasoning_trace: bool = False
    ) -> None:
        """Use ``write_async`` instead. This method is deprecated."""
        print_deprecation_message(old_item="print_conversation_async", new_item="write_async", removed_in="2.0")
        content = await self._render_conversation_async(
            result, include_scores=include_scores, include_reasoning_trace=include_reasoning_trace
        )
        await self._write_async(content)

    async def output_conversation_async(
        self, result: AttackResult, *, include_scores: bool = False, include_reasoning_trace: bool = False
    ) -> None:
        """Use ``write_async`` instead. This method is deprecated."""
        print_deprecation_message(old_item="output_conversation_async", new_item="write_async", removed_in="2.0")
        content = await self._render_conversation_async(
            result, include_scores=include_scores, include_reasoning_trace=include_reasoning_trace
        )
        await self._write_async(content)

    async def print_messages_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_trace: bool = False,
    ) -> None:
        """Use the conversation printer's ``write_async`` instead. This method is deprecated."""
        print_deprecation_message(old_item="print_messages_async", new_item="write_async", removed_in="2.0")
        content = await self._conversation_printer.render_async(
            messages, include_scores=include_scores, include_reasoning_trace=include_reasoning_trace
        )
        await self._write_async(content)

    async def _render_summary_async(self, result: AttackResult) -> str:
        """
        Render a summary of the attack result.

        Args:
            result (AttackResult): The attack result to summarize.

        Returns:
            str: The rendered summary text.
        """
        lines: list[str] = []
        lines.append(self._render_section_header("Attack Summary"))

        lines.append(self._format_colored(f"{self._indent}📋 Basic Information", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}• Objective: {result.objective}", Fore.CYAN))

        attack_type = "Unknown"
        attack_strategy_id = result.get_attack_strategy_identifier()
        if attack_strategy_id:
            attack_type = attack_strategy_id.class_name

        lines.append(self._format_colored(f"{self._indent * 2}• Attack Type: {attack_type}", Fore.CYAN))
        lines.append(self._format_colored(f"{self._indent * 2}• Conversation ID: {result.conversation_id}", Fore.CYAN))

        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}⚡ Execution Metrics", Style.BRIGHT))
        lines.append(self._format_colored(f"{self._indent * 2}• Turns Executed: {result.executed_turns}", Fore.GREEN))
        lines.append(
            self._format_colored(
                f"{self._indent * 2}• Execution Time: {self._format_time(result.execution_time_ms)}", Fore.GREEN
            )
        )

        lines.append("\n")
        lines.append(self._format_colored(f"{self._indent}🎯 Outcome", Style.BRIGHT))
        outcome_icon = self._get_outcome_icon(result.outcome)
        outcome_color = self._get_outcome_color(result.outcome)
        lines.append(
            self._format_colored(
                f"{self._indent * 2}• Status: {outcome_icon} {result.outcome.value.upper()}", outcome_color
            )
        )

        if result.outcome_reason:
            lines.append(self._format_colored(f"{self._indent * 2}• Reason: {result.outcome_reason}", Fore.WHITE))

        if result.last_score:
            lines.append("\n")
            lines.append(self._format_colored(f"{self._indent} Final Score", Style.BRIGHT))
            lines.append(self._score_printer._render_score(result.last_score, indent_level=2))

        return "".join(lines)

    async def print_summary_async(self, result: AttackResult) -> None:
        """Use ``write_async`` instead. This method is deprecated."""
        print_deprecation_message(old_item="print_summary_async", new_item="write_async", removed_in="2.0")
        content = await self._render_summary_async(result)
        await self._write_async(content)

    def _render_header(self, result: AttackResult) -> str:
        """
        Render the header with outcome-based coloring.

        Args:
            result (AttackResult): The attack result containing the outcome.

        Returns:
            str: The rendered header text.
        """
        color = self._get_outcome_color(result.outcome)
        icon = self._get_outcome_icon(result.outcome)

        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored("═" * self._width, color))
        header_text = f"{icon} ATTACK RESULT: {result.outcome.value.upper()} {icon}"
        lines.append(self._format_colored(header_text.center(self._width), Style.BRIGHT, color))
        lines.append(self._format_colored("═" * self._width, color))
        return "".join(lines)

    def _render_footer(self) -> str:
        """
        Render a footer with timestamp.

        Returns:
            str: The rendered footer text.
        """
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored("─" * self._width, Style.DIM, Fore.WHITE))
        footer_text = f"Report generated at: {timestamp} UTC"
        lines.append(self._format_colored(footer_text.center(self._width), Style.DIM, Fore.WHITE))
        return "".join(lines)

    def _render_section_header(self, title: str) -> str:
        """
        Render a section header with consistent styling.

        Args:
            title (str): The title text to display.

        Returns:
            str: The rendered section header text.
        """
        lines: list[str] = []
        lines.append("\n")
        lines.append(self._format_colored(f" {title} ", Style.BRIGHT, Back.BLUE, Fore.WHITE))
        lines.append(self._format_colored("─" * self._width, Fore.BLUE))
        return "".join(lines)

    def _render_metadata(self, metadata: dict[str, Any]) -> str:
        """
        Render metadata in a formatted way.

        Args:
            metadata (dict[str, Any]): Dictionary containing metadata key-value pairs.

        Returns:
            str: The rendered metadata text.
        """
        lines: list[str] = []
        lines.append(self._render_section_header("Additional Metadata"))
        for key, value in metadata.items():
            lines.append(self._format_colored(f"{self._indent}• {key}: {value}", Fore.CYAN))
        return "".join(lines)

    async def _render_pruned_conversations_async(self, result: AttackResult) -> str:
        """
        Render pruned conversations showing only the last message and score for each.

        Args:
            result (AttackResult): The attack result containing related conversations.

        Returns:
            str: The rendered pruned conversations text.
        """
        pruned_refs = result.get_conversations_by_type(ConversationType.PRUNED)

        if not pruned_refs:
            return ""

        lines: list[str] = []
        lines.append(self._render_section_header(f"Pruned Conversations ({len(pruned_refs)} total)"))

        for idx, ref in enumerate(pruned_refs, 1):
            lines.append("\n")
            lines.append(self._format_colored("─" * self._width, Fore.RED))
            label = f"🗑️ PRUNED #{idx}"
            if ref.description:
                label += f" - {ref.description}"
            lines.append(self._format_colored(label, Style.BRIGHT, Fore.RED))
            lines.append(self._format_colored("─" * self._width, Fore.RED))

            messages = await self._get_conversation_async(ref.conversation_id)

            if not messages:
                lines.append(
                    self._format_colored(
                        f"{self._indent}No messages found for conversation: {ref.conversation_id}", Fore.YELLOW
                    )
                )
                continue

            last_message = messages[-1]
            role_label = last_message.api_role.upper()
            lines.append(self._format_colored(f"{self._indent}Last Message ({role_label}):", Style.BRIGHT, Fore.WHITE))

            for piece in last_message.message_pieces:
                lines.append(self._conversation_printer._render_wrapped_text(piece.converted_value, Fore.WHITE))

                scores = await self._get_scores_async(prompt_ids=[str(piece.id)])
                if scores:
                    lines.append("\n")
                    lines.append(self._format_colored(f"{self._indent}📊 Score:", Style.DIM, Fore.MAGENTA))
                    lines.extend(self._score_printer._render_score(score) for score in scores)

        lines.append("\n")
        lines.append(self._format_colored("─" * self._width, Fore.RED))
        return "".join(lines)

    async def _render_adversarial_conversation_async(self, result: AttackResult) -> str:
        """
        Render the adversarial conversation for the best-scoring attack branch.

        Args:
            result (AttackResult): The attack result containing related conversations.

        Returns:
            str: The rendered adversarial conversation text.
        """
        adversarial_refs = result.get_conversations_by_type(ConversationType.ADVERSARIAL)

        if not adversarial_refs:
            return ""

        lines: list[str] = []
        lines.append(self._render_section_header("Adversarial Conversation (Red Team LLM)"))

        best_adversarial_id = result.metadata.get("best_adversarial_conversation_id")
        if best_adversarial_id:
            adversarial_refs = [ref for ref in adversarial_refs if ref.conversation_id == best_adversarial_id]
            if adversarial_refs:
                lines.append(
                    self._format_colored(
                        f"{self._indent}📌 Showing best-scoring branch's adversarial conversation",
                        Style.DIM,
                        Fore.CYAN,
                    )
                )

        for ref in adversarial_refs:
            if ref.description:
                lines.append(self._format_colored(f"{self._indent}📝 {ref.description}", Style.DIM, Fore.CYAN))

            messages = await self._get_conversation_async(ref.conversation_id)

            if not messages:
                lines.append(
                    self._format_colored(
                        f"{self._indent}No messages found for conversation: {ref.conversation_id}", Fore.YELLOW
                    )
                )
                continue

            lines.append(await self._conversation_printer.render_async(messages, include_scores=False))

        return "".join(lines)

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


class PrettyAttackResultMemoryPrinter(PrettyAttackResultPrinter):
    """
    Framework pretty printer for attack results.

    Implements data-fetching via CentralMemory (deferred import).
    All formatting logic lives in PrettyAttackResultPrinter.
    """

    def __init__(
        self, *, sink: Sink | None = None, width: int = 100, indent_size: int = 2, enable_colors: bool = True
    ) -> None:
        """
        Initialize the pretty printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            width (int): Maximum width for text wrapping. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        from pyrit.memory import CentralMemory
        from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter

        score_printer = PrettyScorePrinter(sink=sink, width=width, indent_size=indent_size, enable_colors=enable_colors)
        conversation_printer = PrettyConversationMemoryPrinter(
            sink=sink,
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            score_printer=score_printer,
        )
        super().__init__(
            sink=sink,
            width=width,
            indent_size=indent_size,
            enable_colors=enable_colors,
            conversation_printer=conversation_printer,
            score_printer=score_printer,
        )
        self._memory = CentralMemory.get_memory_instance()

    async def render_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> str:
        """
        Render the complete attack result and return it as a string.

        Args:
            result (AttackResult): The attack result to render.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.

        Returns:
            str: The rendered attack result text.
        """
        return await super().render_async(
            result,
            include_auxiliary_scores=include_auxiliary_scores,
            include_pruned_conversations=include_pruned_conversations,
            include_adversarial_conversation=include_adversarial_conversation,
        )

    async def _get_conversation_async(self, conversation_id: str) -> list[Message]:
        """
        Fetch conversation messages from CentralMemory.

        Returns:
            list[Message]: The conversation messages.
        """
        return list(self._memory.get_conversation(conversation_id=conversation_id))

    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores from CentralMemory.

        Returns:
            list[Score]: The scores.
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))
