# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from pyrit.models import Message


@dataclass(slots=True)
class PrependedHistorySendContext:
    """
    Per-execution state for delivering persisted bootstrap history.

    An explicit seed is identified by persisted message-piece IDs instead of
    inferred from response roles or error codes. A stateful cloned conversation
    may instead have an empty seed plus copied branch messages to bootstrap its
    new provider session. A context may be used by only one send at a time.
    Stateful targets consume the bootstrap after target-specific execution succeeds;
    stateless targets replay their explicit seed for every send.
    """

    conversation_id: str
    seed_message_ids: tuple[uuid.UUID, ...]
    replay_seed_each_send: bool
    bootstrap_message_ids: tuple[uuid.UUID, ...] | None = None
    _seed_consumed: bool = field(default=False, init=False, repr=False)
    _send_in_progress: bool = field(default=False, init=False, repr=False)
    _target_invocation_marked: bool = field(default=False, init=False, repr=False)
    _target_invocation_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        """
        Validate the persisted seed identity.

        Raises:
            ValueError: If the conversation ID or seed IDs are invalid.
        """
        if not self.conversation_id:
            raise ValueError("conversation_id must not be empty")
        if not self.seed_message_ids and not self.bootstrap_message_ids:
            raise ValueError("seed_message_ids must not be empty unless bootstrap_message_ids are provided")
        if len(set(self.seed_message_ids)) != len(self.seed_message_ids):
            raise ValueError("seed_message_ids must be unique")
        if self.bootstrap_message_ids is not None:
            if not self.bootstrap_message_ids:
                raise ValueError("bootstrap_message_ids must not be empty")
            if len(set(self.bootstrap_message_ids)) != len(self.bootstrap_message_ids):
                raise ValueError("bootstrap_message_ids must be unique")
            if not set(self.seed_message_ids).issubset(self.bootstrap_message_ids):
                raise ValueError("bootstrap_message_ids must include every seed message")

    @property
    def seed_message_count(self) -> int:
        """Number of messages in the explicit seed prefix."""
        return len(self.seed_message_ids)

    @property
    def bootstrap_message_count(self) -> int:
        """Number of historical messages needed to bootstrap the next provider session."""
        return len(self.bootstrap_message_ids or self.seed_message_ids)

    @property
    def should_include_seed(self) -> bool:
        """Whether the seed prefix should be included in the next send."""
        return self.replay_seed_each_send or not self._seed_consumed

    @property
    def is_seed_consumed(self) -> bool:
        """Whether a stateful target has consumed the seed prefix."""
        return self._seed_consumed

    @property
    def target_invocation_count(self) -> int:
        """Number of sends that reached target-specific execution."""
        return self._target_invocation_count

    def begin_send(self) -> None:
        """
        Acquire this context for one complete send.

        Raises:
            RuntimeError: If another send using this context is still active.
        """
        if self._send_in_progress:
            raise RuntimeError(
                "Concurrent sends for the same prepended history send context are not supported. "
                "Wait for the active send to finish before sending another request."
            )
        self._send_in_progress = True
        self._target_invocation_marked = False

    def mark_target_invoked(self) -> None:
        """
        Record that target-specific execution has begun for the active send.

        Raises:
            RuntimeError: If no send currently owns the context.
        """
        if not self._send_in_progress:
            raise RuntimeError("Cannot mark a target invocation without an active send.")
        if self._target_invocation_marked:
            return
        self._target_invocation_marked = True
        self._target_invocation_count += 1

    def finish_send(self, *, succeeded: bool) -> None:
        """
        Release this context and consume stateful bootstrap after successful target execution.

        Args:
            succeeded: Whether target-specific execution returned successfully.
        """
        if not self._send_in_progress:
            return
        if succeeded and self._target_invocation_marked and not self.replay_seed_each_send:
            self._seed_consumed = True
        self._send_in_progress = False

    def select_history(self, *, messages: list[Message]) -> list[Message]:
        """
        Select history needed for delivery or provider-session restoration.

        Args:
            messages: Persisted conversation messages after failed exchanges
                have been removed.

        Returns:
            The pending bootstrap messages before a stateful target consumes
            them, the explicit seed for a stateless target, or all replayable
            persisted history after a stateful target has been bootstrapped.

        Raises:
            ValueError: If an expected persisted seed message is missing.
        """
        if not self.should_include_seed:
            return list(messages)

        messages_by_id = {message.get_piece().id: message for message in messages}
        selected_message_ids = self.bootstrap_message_ids or self.seed_message_ids
        missing_ids = [message_id for message_id in selected_message_ids if message_id not in messages_by_id]
        if missing_ids:
            raise ValueError(
                "The persisted prepended history no longer matches the prepended history send context. "
                f"Missing {len(missing_ids)} message(s)."
            )
        return [messages_by_id[message_id] for message_id in selected_message_ids]

    def remap_for_duplicate_conversation(
        self,
        *,
        conversation_id: str,
        source_messages: list[Message],
        duplicated_messages: list[Message],
    ) -> PrependedHistorySendContext:
        """
        Remap this explicit seed boundary to a duplicated conversation.

        The explicit prepended seed identity is always remapped. A stateless clone
        continues to replay only that seed. A stateful clone opens a new provider
        session, so every replayable duplicated branch message becomes its one-time
        bootstrap boundary.

        Args:
            conversation_id: Conversation ID assigned to the duplicated messages.
            source_messages: Messages from the source conversation in persisted order.
            duplicated_messages: Their duplicates in the same persisted order.

        Returns:
            A new context with seed IDs from the duplicated conversation.
            A new logical conversation starts with an unconsumed seed boundary.

        Raises:
            ValueError: If the duplicated messages do not match the source structure
                or an explicit seed piece cannot be remapped.
        """
        if len(source_messages) != len(duplicated_messages):
            raise ValueError("Duplicated conversation does not match the source message count.")

        duplicated_ids_by_source_id: dict[uuid.UUID, uuid.UUID] = {}
        for source_message, duplicated_message in zip(source_messages, duplicated_messages, strict=True):
            if (
                source_message.api_role != duplicated_message.api_role
                or source_message.sequence != duplicated_message.sequence
                or len(source_message.message_pieces) != len(duplicated_message.message_pieces)
            ):
                raise ValueError("Duplicated conversation does not preserve the source message structure.")

            for source_piece, duplicated_piece in zip(
                source_message.message_pieces,
                duplicated_message.message_pieces,
                strict=True,
            ):
                if (
                    source_piece.api_role != duplicated_piece.api_role
                    or source_piece.sequence != duplicated_piece.sequence
                    or duplicated_piece.conversation_id != conversation_id
                ):
                    raise ValueError("Duplicated conversation does not preserve the source piece structure.")
                duplicated_ids_by_source_id[source_piece.id] = duplicated_piece.id

        current_bootstrap_ids = self.bootstrap_message_ids or self.seed_message_ids
        ids_to_remap = set(self.seed_message_ids) | set(current_bootstrap_ids)
        missing_ids = [message_id for message_id in ids_to_remap if message_id not in duplicated_ids_by_source_id]
        if missing_ids:
            raise ValueError(f"Could not remap {len(missing_ids)} bootstrap message(s).")

        remapped_seed_ids = tuple(duplicated_ids_by_source_id[message_id] for message_id in self.seed_message_ids)
        if conversation_id != self.conversation_id and not self.replay_seed_each_send:
            remapped_bootstrap_ids = tuple(message.get_piece().id for message in duplicated_messages)
        else:
            remapped_bootstrap_ids = tuple(
                duplicated_ids_by_source_id[message_id] for message_id in current_bootstrap_ids
            )

        duplicated_context = PrependedHistorySendContext(
            conversation_id=conversation_id,
            seed_message_ids=remapped_seed_ids,
            replay_seed_each_send=self.replay_seed_each_send,
            bootstrap_message_ids=remapped_bootstrap_ids,
        )
        # Provider bootstrap consumption belongs to the logical conversation, not copied memory.
        if conversation_id == self.conversation_id:
            duplicated_context._seed_consumed = self._seed_consumed
        return duplicated_context
