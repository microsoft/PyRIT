# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from pyrit.common.deprecation import print_deprecation_message


class ConversationType(Enum):
    """Types of conversations that can be associated with an attack."""

    ADVERSARIAL = "adversarial"
    PRUNED = "pruned"
    SCORE = "score"
    CONVERTER = "converter"


class ConversationReference(BaseModel):
    """Immutable reference to a conversation that played a role in the attack."""

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    conversation_type: ConversationType
    description: Optional[str] = None

    def __hash__(self) -> int:
        """
        Return a hash derived from conversation ID.

        Returns:
            int: Hash value for this reference.

        """
        return hash(self.conversation_id)

    def __eq__(self, other: object) -> bool:
        """
        Compare two references by conversation ID.

        Args:
            other (object): Other object to compare.

        Returns:
            bool: True when the other object is a matching ConversationReference.

        """
        return isinstance(other, ConversationReference) and self.conversation_id == other.conversation_id

    def to_dict(self) -> dict[str, str | None]:
        """
        Serialize to a JSON-compatible dictionary.

        .. deprecated::
            Use :meth:`model_dump` with ``mode="json"`` instead. This method
            will be removed in version 0.16.0.

        Returns:
            dict[str, str | None]: Dictionary with conversation_id, conversation_type, and description.
        """
        print_deprecation_message(
            old_item=ConversationReference.to_dict,
            new_item='ConversationReference.model_dump(mode="json")',
            removed_in="0.16.0",
        )
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, str | None]) -> ConversationReference:
        """
        Reconstruct a ConversationReference from a dictionary.

        .. deprecated::
            Use :meth:`model_validate` instead. This method will be removed
            in version 0.16.0.

        Args:
            data (dict[str, str | None]): Dictionary as produced by ``model_dump(mode="json")``.

        Returns:
            ConversationReference: Reconstructed instance.
        """
        print_deprecation_message(
            old_item=ConversationReference.from_dict,
            new_item="ConversationReference.model_validate",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)
