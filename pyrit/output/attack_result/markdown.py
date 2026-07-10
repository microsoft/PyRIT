# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from datetime import datetime, timezone

from pyrit.models import AttackResult, ConversationType, Message, Score
from pyrit.output.attack_result.base import AttackResultPrinterBase
from pyrit.output.conversation.markdown import MarkdownConversationPrinter
from pyrit.output.score.markdown import MarkdownScorePrinter
from pyrit.output.sink import Sink


class MarkdownAttackResultPrinter(AttackResultPrinterBase):
    """
    Markdown printer for attack results optimized for Jupyter notebooks.

    Composes a conversation printer for message rendering and a score printer
    for inline score display. Subclasses implement data-fetching methods.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        display_inline: bool = True,
        conversation_printer: MarkdownConversationPrinter | None = None,
        score_printer: MarkdownScorePrinter | None = None,
        blur_images: bool = False,
        blur_radius: int = 20,
        blurred_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """
        Initialize the markdown printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            display_inline (bool): Kept for backward compatibility but unused.
                All output is routed through the sink. Defaults to True.
            conversation_printer (MarkdownConversationPrinter | None): Conversation printer.
                Defaults to a new MarkdownConversationPrinter with matching sink.
            score_printer (MarkdownScorePrinter | None): Score printer.
                Defaults to a new MarkdownScorePrinter with matching sink.
            blur_images (bool): If True, write a blurred copy of each referenced
                image and link to it instead of the original. Forwarded to the default
                conversation printer when one is not supplied. Defaults to False.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
            blurred_dir (str | PathLike | None): Directory to write blurred copies into
                when ``blur_images`` is True. Defaults to None (sibling of the original).
        """
        super().__init__(sink=sink)
        self._display_inline = display_inline
        self._score_printer = score_printer or MarkdownScorePrinter(sink=sink)
        self._conversation_printer = conversation_printer or MarkdownConversationPrinter(
            sink=sink,
            score_printer=self._score_printer,
            blur_images=blur_images,
            blur_radius=blur_radius,
            blurred_dir=blurred_dir,
        )

    async def render_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> str:
        """
        Render the complete attack result as markdown and return it as a string.

        Args:
            result (AttackResult): The attack result to render.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.

        Returns:
            str: The rendered markdown text.
        """
        markdown_lines: list[str] = []

        outcome_emoji = self._get_outcome_icon(result.outcome)
        markdown_lines.append(f"# {outcome_emoji} Attack Result: {result.outcome.value.upper()}\n")
        markdown_lines.append("---\n")

        summary_lines = await self._get_summary_markdown_async(result)
        markdown_lines.extend(summary_lines)
        markdown_lines.append("---\n")

        markdown_lines.append("\n## Conversation History\n")
        conversation_lines = await self._get_conversation_markdown_async(
            result=result, include_scores=include_auxiliary_scores
        )
        markdown_lines.extend(conversation_lines)

        if include_pruned_conversations:
            pruned_lines = await self._get_pruned_conversations_markdown_async(result)
            if pruned_lines:
                markdown_lines.extend(pruned_lines)

        if include_adversarial_conversation:
            adversarial_lines = await self._get_adversarial_conversation_markdown_async(result)
            if adversarial_lines:
                markdown_lines.extend(adversarial_lines)

        if result.metadata:
            markdown_lines.append("\n## Additional Metadata\n")
            for key, value in result.metadata.items():
                try:
                    str_value = str(value)
                    markdown_lines.append(f"- **{key}:** {str_value}")
                except Exception:
                    pass

        markdown_lines.append("\n---")
        timestamp_utc = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
        markdown_lines.append(f"*Report generated at {timestamp_utc}*")

        return "\n".join(markdown_lines)

    async def _get_conversation_markdown_async(
        self, *, result: AttackResult, include_scores: bool = False
    ) -> list[str]:
        """
        Generate markdown lines for the conversation history.

        Args:
            result (AttackResult): The attack result containing the conversation ID.
            include_scores (bool): Whether to include scores. Defaults to False.

        Returns:
            list[str]: Markdown strings for the conversation.
        """
        if not result.conversation_id:
            return ["*No conversation ID available*\n"]

        messages = await self._get_conversation_async(result.conversation_id)

        if not messages:
            return [f"*No conversation found for ID: {result.conversation_id}*\n"]

        rendered = await self._conversation_printer.render_async(messages, include_scores=include_scores)
        return [rendered]

    async def _get_summary_markdown_async(self, result: AttackResult) -> list[str]:
        """
        Generate markdown lines for the attack summary.

        Args:
            result (AttackResult): The attack result to summarize.

        Returns:
            list[str]: Markdown strings for the summary.
        """
        markdown_lines: list[str] = []
        markdown_lines.append("## Attack Summary\n")

        markdown_lines.append("### Basic Information\n")
        markdown_lines.append("| Field | Value |")
        markdown_lines.append("|-------|-------|")
        markdown_lines.append(f"| **Objective** | {result.objective} |")

        _strategy_id = result.get_attack_strategy_identifier()
        attack_type = _strategy_id.class_name if _strategy_id is not None else "Unknown"

        markdown_lines.append(f"| **Attack Type** | `{attack_type}` |")
        markdown_lines.append(f"| **Conversation ID** | `{result.conversation_id}` |")

        markdown_lines.append("\n### Execution Metrics\n")
        markdown_lines.append("| Metric | Value |")
        markdown_lines.append("|--------|-------|")
        markdown_lines.append(f"| **Turns Executed** | {result.executed_turns} |")
        markdown_lines.append(f"| **Execution Time** | {self._format_time(result.execution_time_ms)} |")

        outcome_emoji = self._get_outcome_icon(result.outcome)
        markdown_lines.append("\n### Outcome\n")
        markdown_lines.append(f"**Status:** {outcome_emoji} **{result.outcome.value.upper()}**\n")

        if result.outcome_reason:
            markdown_lines.append(f"**Reason:** {result.outcome_reason}\n")

        if result.last_score:
            markdown_lines.append("\n### Final Score\n")
            markdown_lines.append(self._score_printer._format_score(result.last_score))

        return markdown_lines

    async def _get_pruned_conversations_markdown_async(self, result: AttackResult) -> list[str]:
        """
        Generate markdown lines for pruned conversations.

        Args:
            result (AttackResult): The attack result containing related conversations.

        Returns:
            list[str]: Markdown strings for pruned conversations.
        """
        pruned_refs = result.get_conversations_by_type(ConversationType.PRUNED)

        if not pruned_refs:
            return []

        markdown_lines: list[str] = []
        markdown_lines.append(f"\n## Pruned Conversations ({len(pruned_refs)} total)\n")
        markdown_lines.append("*Showing only the last message and score for each pruned branch.*\n")

        for idx, ref in enumerate(pruned_refs, 1):
            label = f"### 🗑️ Pruned #{idx}"
            if ref.description:
                label += f" - {ref.description}"
            markdown_lines.append(f"\n{label}\n")

            messages = await self._get_conversation_async(ref.conversation_id)

            if not messages:
                markdown_lines.append(f"*No messages found for conversation: `{ref.conversation_id}`*\n")
                continue

            last_message = messages[-1]
            role_label = last_message.api_role.upper()

            markdown_lines.append(f"**Last Message ({role_label}):**\n")

            for piece in last_message.message_pieces:
                content = piece.converted_value or ""
                if "\n" in content:
                    markdown_lines.append("```")
                    markdown_lines.append(content)
                    markdown_lines.append("```")
                else:
                    markdown_lines.append(f"> {content}\n")

                scores = await self._get_scores_async(prompt_ids=[str(piece.id)])
                if scores:
                    markdown_lines.append("\n**Score:**\n")
                    markdown_lines.extend(self._score_printer._format_score(score, indent="") for score in scores)

        return markdown_lines

    async def _get_adversarial_conversation_markdown_async(self, result: AttackResult) -> list[str]:
        """
        Generate markdown lines for the adversarial conversation.

        Args:
            result (AttackResult): The attack result containing related conversations.

        Returns:
            list[str]: Markdown strings for the adversarial conversation.
        """
        adversarial_refs = result.get_conversations_by_type(ConversationType.ADVERSARIAL)

        if not adversarial_refs:
            return []

        markdown_lines: list[str] = []
        markdown_lines.append("\n## Adversarial Conversation (Red Team LLM)\n")
        markdown_lines.append("*This shows the reasoning and strategy of the red teaming LLM.*\n")

        best_adversarial_id = result.metadata.get("best_adversarial_conversation_id")
        if best_adversarial_id:
            adversarial_refs = [ref for ref in adversarial_refs if ref.conversation_id == best_adversarial_id]
            if adversarial_refs:
                markdown_lines.append("*📌 Showing best-scoring branch's adversarial conversation*\n")

        for ref in adversarial_refs:
            if ref.description:
                markdown_lines.append(f"*📝 {ref.description}*\n")

            messages = await self._get_conversation_async(ref.conversation_id)

            if not messages:
                markdown_lines.append(f"*No messages found for conversation: `{ref.conversation_id}`*\n")
                continue

            turn_number = 0
            for message in messages:
                if message.api_role == "user":
                    turn_number += 1
                    markdown_lines.append(f"\n#### Turn {turn_number} - USER\n")
                elif message.api_role == "system":
                    markdown_lines.append("\n#### SYSTEM\n")
                else:
                    markdown_lines.append(f"\n#### {message.api_role.upper()}\n")

                for piece in message.message_pieces:
                    content = piece.converted_value or ""
                    if len(content) > 200 or "\n" in content:
                        markdown_lines.append("```")
                        markdown_lines.append(content)
                        markdown_lines.append("```")
                    else:
                        markdown_lines.append(f"> {content}\n")

        return markdown_lines


class MarkdownAttackResultMemoryPrinter(MarkdownAttackResultPrinter):
    """
    Framework markdown printer for attack results.

    Implements data-fetching via CentralMemory (deferred import).
    All formatting logic lives in MarkdownAttackResultPrinter.
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        display_inline: bool = True,
        blur_images: bool = False,
        blur_radius: int = 20,
        blurred_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """
        Initialize the markdown printer with CentralMemory data source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            display_inline (bool): Kept for backward compatibility but unused.
                All output is routed through the sink. Defaults to True.
            blur_images (bool): If True, write a blurred copy of each referenced
                image and link to it instead of the original. Defaults to False.
            blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
                Defaults to 20.
            blurred_dir (str | PathLike | None): Directory to write blurred copies into.
                Defaults to None (sibling of the original).
        """
        from pyrit.memory import CentralMemory
        from pyrit.output.conversation.markdown import MarkdownConversationMemoryPrinter

        score_printer = MarkdownScorePrinter(sink=sink)
        conversation_printer = MarkdownConversationMemoryPrinter(
            sink=sink,
            score_printer=score_printer,
            blur_images=blur_images,
            blur_radius=blur_radius,
            blurred_dir=blurred_dir,
        )
        super().__init__(
            sink=sink,
            display_inline=display_inline,
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
        Render the complete attack result as markdown and return it as a string.

        Args:
            result (AttackResult): The attack result to render.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.

        Returns:
            str: The rendered markdown text.
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
        return list(self._memory.get_conversation_messages(conversation_id=conversation_id))

    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores from CentralMemory.

        Returns:
            list[Score]: The scores.
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))
