# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ConversationStats:
    """
    Lightweight aggregate statistics for a conversation.

    Used to build attack summaries without loading full message pieces.
    """

    PREVIEW_MAX_LEN: ClassVar[int] = 100

    message_count: int = 0
    last_message_preview: Optional[str] = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: Optional[datetime] = None
