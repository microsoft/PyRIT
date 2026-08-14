# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from pyrit.models.literals import ChatMessageRole, PromptDataType
    from pyrit.models.messages import Message


@dataclass(frozen=True)
class MessageScorable:
    """
    A message the caller already holds.

    Use this when the message is in hand — an attack scoring the response it just
    received, or a caller scoring a message whose pieces were never persisted. Use
    ``MessageReferenceScorable`` when only the piece ids are known.
    """

    message: Message
    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


@dataclass(frozen=True)
class MessageReferenceScorable:
    """
    Specific message pieces, resolved from memory by id.

    Use this when the pieces are persisted and only their ids are known. Use
    ``MessageScorable`` when the caller already holds the message.
    """

    message_piece_ids: tuple[uuid.UUID | str, ...]
    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


@dataclass(frozen=True)
class ContentScorable:
    """Loose content with no conversation behind it."""

    value: str
    data_type: PromptDataType = "text"


Scorable = MessageScorable | MessageReferenceScorable | ContentScorable
