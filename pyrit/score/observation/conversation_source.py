# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire a whole-conversation snapshot without selecting scoring criteria."""

from pyrit.memory import CentralMemory
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ConversationObservationPayload,
    ConversationScorable,
    Observation,
)
from pyrit.models.score.observation import _conversation_piece_digest


class ConversationSource:
    """Capture exact references to the conversation available at acquisition time."""

    def get_identifier(self) -> ComponentIdentifier:
        """
        Identify the acquisition contract.

        Returns:
            ComponentIdentifier: The versioned source identity.
        """
        return ComponentIdentifier.of(self, params={"snapshot_version": 1})

    async def acquire_async(self, *, scorable: ConversationScorable) -> Observation:
        """
        Capture the full current history without filtering it.

        Returns:
            Observation: Ordered references to the acquired evidence.

        Raises:
            ValueError: If the conversation does not exist.
        """
        memory = CentralMemory.get_memory_instance()
        messages = await memory.get_conversation_messages_async(conversation_id=scorable.conversation_id)
        pieces = tuple(piece for message in messages for piece in message.message_pieces)
        if not pieces:
            raise ValueError(f"Conversation with ID {scorable.conversation_id} not found in memory.")
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=Acquisition.COMPLETE,
            scorable=scorable,
            payload=ConversationObservationPayload(
                message_piece_ids=tuple(piece.id for piece in pieces),
                message_piece_digests=tuple(_conversation_piece_digest(piece) for piece in pieces),
            ),
        )
