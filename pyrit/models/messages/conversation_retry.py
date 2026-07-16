# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ConversationRetryReason(str, Enum):
    """Why a turn in a conversation had to be retried."""

    JSON_PARSING = "json_parsing"


class ConversationRetry(BaseModel):
    """
    Record of a single retried turn within a conversation.

    A retry happens when a turn's response failed validation (e.g. malformed JSON)
    and the failed turn was rolled back out of memory so the turn could be resent on
    a clean history. The record is conversation-scoped metadata: it captures which
    turn was retried and why, without keeping the discarded turn's pieces around.
    """

    model_config = ConfigDict(frozen=True)

    # The sequence the retried turn's request occupies. Stable across attempts
    # because the rollback resets the sequence so the eventual successful request
    # lands at the same index.
    sequence: int

    reason: ConversationRetryReason
