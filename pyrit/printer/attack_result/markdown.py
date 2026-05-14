# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from datetime import datetime, timezone

from pyrit.models import AttackResult, ConversationType, Message, MessagePiece, Score
from pyrit.printer.attack_result.base import AttackResultPrinterBase


class MarkdownAttackPrinterBase(AttackResultPrinterBase):
    """
    Markdown printer base for attack results optimized for Jupyter notebooks.

    Contains all formatting logic. Subclasses implement get_conversation_async
    and get_scores_async for data fetching.
    """

    def __init__(self, *, display_inline: bool = True) -> None:
        """
        Initialize the markdown printer.

        Args:
            display_inline (bool): If True, uses IPython.display to render markdown
                inline in Jupyter notebooks. If False, prints markdown strings.
                Defaults to True.
        """
        self._display_inline = display_inline

    def _render_markdown(self, markdown_lines: list[str]) -> None:
        """
        Render the markdown content using appropriate display method.

        Attempts to use IPython.display.Markdown for Jupyter notebook rendering
        when display_inline is True, falling back to print() if not available.

        Args:
            markdown_lines (List[str]): List of markdown strings to render.
        """
        full_markdown = "\n".join(markdown_lines)

        if self._display_inline:
            try:
                from IPython.display import Markdown, display

                display(Markdown(full_markdown))
            except (ImportError, NameError):
                print(full_markdown)
        else:
            print(full_markdown)

    def _format_score(self, score: Score, indent: str = "") -> str:
        """
        Format a score object as markdown with proper styling.

        Args:
            score (Score): The score object to format.
            indent (str): String prefix for indentation. Defaults to "".

        Returns:
            str: Formatted markdown representation of the score.
        """
        lines = []

        score_value = score.get_value()
        if isinstance(score_value, bool):
            value_str = str(score_value)
        elif isinstance(score_value, (int, float)):
            value_str = f"**{score_value:.2f}**" if isinstance(score_value, float) else f"**{score_value}**"
        else:
            value_str = f"**{score_value}**"

        lines.append(f"{indent}- **Score Type:** {score.score_type}")
        lines.append(f"{indent}- **Value:** {value_str}")
        category_str = ", ".join(score.score_category) if score.score_category else "N/A"
        lines.append(f"{indent}- **Category:** {category_str}")

        if score.score_rationale:
            rationale_lines = score.score_rationale.split("\n")
            if len(rationale_lines) > 1:
                lines.append(f"{indent}- **Rationale:**")
                lines.extend(f"{indent}  {line}" for line in rationale_lines)
            else:
                lines.append(f"{indent}- **Rationale:** {score.score_rationale}")

        if score.score_metadata:
            lines.append(f"{indent}- **Metadata:** `{score.score_metadata}`")

        return "\n".join(lines)

    async def print_result_async(
        self,
        result: AttackResult,
        *,
        include_auxiliary_scores: bool = False,
        include_pruned_conversations: bool = False,
        include_adversarial_conversation: bool = False,
    ) -> None:
        """
        Print the complete attack result as formatted markdown.

        Args:
            result (AttackResult): The attack result to print.
            include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
            include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
            include_adversarial_conversation (bool): Whether to include the adversarial conversation.
                Defaults to False.
        """
        markdown_lines = []

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

        self._render_markdown(markdown_lines)

    async def print_conversation_async(self, result: AttackResult, *, include_scores: bool = False) -> None:
        """
        Print only the conversation history as formatted markdown.

        Args:
            result (AttackResult): The attack result containing the conversation to display.
            include_scores (bool): Whether to include scores. Defaults to False.
        """
        markdown_lines = await self._get_conversation_markdown_async(result=result, include_scores=include_scores)
        self._render_markdown(markdown_lines)

    async def print_summary_async(self, result: AttackResult) -> None:
        """
        Print a summary of the attack result as formatted markdown.

        Args:
            result (AttackResult): The attack result to summarize.
        """
        markdown_lines = await self._get_summary_markdown_async(result)
        self._render_markdown(markdown_lines)

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
        markdown_lines: list[str] = []

        if not result.conversation_id:
            markdown_lines.append("*No conversation ID available*\n")
            return markdown_lines

        messages = await self.get_conversation_async(result.conversation_id)

        if not messages:
            markdown_lines.append(f"*No conversation found for ID: {result.conversation_id}*\n")
            return markdown_lines

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

        return markdown_lines

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

    def _get_audio_mime_type(self, *, audio_path: str) -> str:
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

    def _format_image_content(self, *, image_path: str) -> list[str]:
        """
        Format image content as markdown.

        Args:
            image_path (str): The path to the image file.

        Returns:
            list[str]: Markdown lines for the image.
        """
        relative_path = os.path.relpath(image_path)
        posix_path = relative_path.replace("\\", "/")
        return [f"![Image]({posix_path})\n"]

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
            scores = await self.get_scores_async(prompt_ids=[str(piece.id)])
            if scores:
                lines.append("\n##### Scores\n")
                lines.extend(self._format_score(score, indent="") for score in scores)
                lines.append("")
        return lines

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
            markdown_lines.append(self._format_score(result.last_score))

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

            messages = await self.get_conversation_async(ref.conversation_id)

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

                scores = await self.get_scores_async(prompt_ids=[str(piece.id)])
                if scores:
                    markdown_lines.append("\n**Score:**\n")
                    markdown_lines.extend(self._format_score(score, indent="") for score in scores)

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

            messages = await self.get_conversation_async(ref.conversation_id)

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


class MarkdownAttackMemoryPrinter(MarkdownAttackPrinterBase):
    """
    Framework markdown printer for attack results.

    Implements data-fetching via CentralMemory (deferred import).
    All formatting logic lives in MarkdownAttackPrinterBase.
    """

    def __init__(self, *, display_inline: bool = True) -> None:
        """
        Initialize the markdown printer.

        Args:
            display_inline (bool): If True, uses IPython.display to render markdown
                inline in Jupyter notebooks. If False, prints markdown strings.
                Defaults to True.
        """
        super().__init__(display_inline=display_inline)
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def get_conversation_async(self, conversation_id: str) -> list[Message]:
        """Fetch conversation messages from CentralMemory."""
        return list(self._memory.get_conversation(conversation_id=conversation_id))

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """Fetch scores from CentralMemory."""
        return self._memory.get_prompt_scores(prompt_ids=prompt_ids)
