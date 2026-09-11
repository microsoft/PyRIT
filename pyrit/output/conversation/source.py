# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import Message, Score


@runtime_checkable
class ConversationSource(Protocol):
    """
    The data a conversation printer needs, decoupled from where it comes from.

    ``MemoryConversationSource`` (CentralMemory) backs the framework/notebook path;
    a REST-backed source backs the thin CLI client. Printers depend only on this
    Protocol, so ``pyrit.output`` needs no knowledge of either backend and never
    imports ``pyrit.cli``.
    """

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """Return the ordered messages for a conversation."""
        ...

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """Return the scores attached to the given message-piece ids (empty if none)."""
        ...


class MemoryConversationSource:
    """``ConversationSource`` backed by ``CentralMemory`` (framework / notebook path)."""

    def __init__(self) -> None:
        """Resolve the process-wide memory instance (deferred import)."""
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """
        Return the ordered messages for a conversation from memory.

        Args:
            conversation_id (str): The conversation to read.

        Returns:
            list[Message]: The conversation's messages in order.
        """
        return list(self._memory.get_conversation_messages(conversation_id=conversation_id))

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Return the scores attached to the given message-piece ids from memory.

        Args:
            prompt_ids (list[str]): The message-piece ids to fetch scores for.

        Returns:
            list[Score]: The scores for those pieces (empty if none).
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))
