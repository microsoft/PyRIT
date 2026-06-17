# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pyrit.models.score import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ComponentIdentifierField,
)


class Conversation(BaseModel):
    """
    Conversation-scoped metadata shared by every piece in a conversation.

    A ``Conversation`` records identifiers that belong to the conversation as a
    whole rather than to any individual ``MessagePiece`` -- most importantly the
    target the conversation is held with. Persisting these once per conversation
    (instead of stamping them onto every piece/row) is what keeps ``MessagePiece``
    small.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    conversation_id: str
    target_identifier: ComponentIdentifierField | None = None
