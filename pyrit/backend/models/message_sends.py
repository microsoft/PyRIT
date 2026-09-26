# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Transient progress for one manual message sent to one conversation."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.backend.models.attacks import AddMessageRequest


class MessageSendState(StrEnum):
    """Execution state, independent of whether message evidence has been saved."""

    QUEUED = "queued"
    PREPARING = "preparing"
    SENDING = "sending"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MessageSendFailureStage(StrEnum):
    """Failure location, not permission to retry provider delivery."""

    PREPARATION = "preparation"
    SENDING = "sending"
    FINALIZATION = "finalization"
    INTERRUPTED = "interrupted"


class MessageSendRequest(AddMessageRequest):
    """Submit one send; the submission identity is only a worker-local deduplication hint."""

    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(..., min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_send(self) -> Self:
        if not self.send:
            raise ValueError("Message sends require send=true; use the messages endpoint to store context")
        if not self.pieces:
            raise ValueError("Message sends require at least one message piece")
        if not self.submission_id.strip():
            raise ValueError("submission_id must not be blank")
        if not self.target_registry_name or not self.target_registry_name.strip():
            raise ValueError("target_registry_name is required when send=True")
        if not self.target_conversation_id.strip():
            raise ValueError("target_conversation_id must not be blank")
        return self


class MessageSendStatus(BaseModel):
    """Worker-local progress, without a transcript or durable delivery guarantee."""

    send_id: str
    attack_result_id: str
    conversation_id: str
    request_turn_number: int | None = Field(None, description="This send's request turn, assigned during preparation")
    state: MessageSendState = MessageSendState.QUEUED
    error: str | None = None
    failure_stage: MessageSendFailureStage | None = None
