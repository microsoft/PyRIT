# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime
from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.literals import PromptDataType


class ConversationStats(BaseModel):
    """
    Lightweight aggregate statistics for a conversation.

    Used to build attack summaries without loading full message pieces.
    """

    model_config = ConfigDict(frozen=True)

    PREVIEW_MAX_LEN: ClassVar[int] = 100
    PREVIEW_FETCH_MAX_LEN: ClassVar[int] = 1024
    """
    Upper bound (in characters) for the raw ``last_message_preview`` value
    fetched from storage. Larger than ``PREVIEW_MAX_LEN`` so that downstream
    presentation code (see ``pyrit.backend.mappers._preview``) has enough
    characters to extract a basename from a long media path or signed blob
    URL before applying display-level truncation.
    """

    message_count: int = 0
    last_message_preview: Optional[str] = None
    last_message_data_type: Optional[PromptDataType] = None
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
