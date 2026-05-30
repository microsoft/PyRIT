# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime
from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationStats(BaseModel):
    """
    Lightweight aggregate statistics for a conversation.

    Used to build attack summaries without loading full message pieces.
    """

    model_config = ConfigDict(frozen=True)

    PREVIEW_MAX_LEN: ClassVar[int] = 100

    message_count: int = 0
    last_message_preview: Optional[str] = None
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
