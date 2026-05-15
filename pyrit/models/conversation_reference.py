# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ConversationType(Enum):
    """Types of conversations that can be associated with an attack."""

    ADVERSARIAL = "adversarial"
    PRUNED = "pruned"
    SCORE = "score"
    CONVERTER = "converter"


@dataclass(frozen=True)
class ConversationReference:
    """Immutable reference to a conversation that played a role in the attack."""

    conversation_id: str
    conversation_type: ConversationType
    description: Optional[str] = None

    # Allow use in set / dict
    def __hash__(self) -> int:
        """
        Return a hash derived from conversation ID.

        Returns:
            int: Hash value for this reference.

        """
        return hash(self.conversation_id)

    def to_dict(self) -> dict[str, str | None]:
        """
        Serialize to a JSON-compatible dictionary.

        Returns:
            dict[str, str | None]: Dictionary with conversation_id, conversation_type, and description.
        """
        return {
            "conversation_id": self.conversation_id,
            "conversation_type": self.conversation_type.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str | None]) -> ConversationReference:
        """
        Reconstruct a ConversationReference from a dictionary.

        Args:
            data (dict[str, str | None]): Dictionary as produced by to_dict().

        Returns:
            ConversationReference: Reconstructed instance.
        """
        return cls(
            conversation_id=str(data["conversation_id"]),
            conversation_type=ConversationType(data["conversation_type"]),
            description=data.get("description"),
        )

    def __eq__(self, other: object) -> bool:
        """
        Compare two references by conversation ID.

        Args:
            other (object): Other object to compare.

        Returns:
            bool: True when the other object is a matching ConversationReference.

        """
        return isinstance(other, ConversationReference) and self.conversation_id == other.conversation_id
