# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score-related API request models."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.common import TextStr
from pyrit.models.component_spec import RegisteredComponentReference


class ApplyChatScorerRequest(BaseModel):
    """Apply the selected scorer to the latest saved response in one conversation."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID
    response_id: uuid.UUID
    scorer: RegisteredComponentReference
    objective: TextStr


class ApplyChatScorerResponse(BaseModel):
    """The persisted result, including whether an existing complete score was reused."""

    conversation_id: str
    score: ScoreView
    reused: bool


class ManualScoreRequest(BaseModel):
    """Request to attach a user-supplied score to a message piece."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: uuid.UUID = Field(..., description="ID of the attack containing the message")
    message_id: uuid.UUID = Field(..., description="ID of the message piece to score")
    value: StrictBool = Field(..., description="Whether the attack objective was achieved")
    rationale: TextStr = Field(default="", description="Optional explanation for the score")
    update_attack: bool = Field(
        default=False,
        description="Whether to make this the attack's human score and update its outcome",
    )
