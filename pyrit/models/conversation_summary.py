# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    """Read-only conversation metadata shared by attack and hierarchy views."""

    conversation_id: str = Field(..., description="Unique conversation identifier")
    message_count: int = Field(0, description="Number of messages in this conversation")
    last_message_preview: str | None = Field(None, description="Preview of the last message")
    created_at: datetime | None = Field(None, description="Timestamp of the first message")
    conversation_type: str | None = None
