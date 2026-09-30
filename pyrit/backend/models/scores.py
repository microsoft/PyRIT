# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score-related API request models."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ManualScoreRequest(BaseModel):
    """Request to attach a user-supplied score to a message piece."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: uuid.UUID = Field(..., description="ID of the attack containing the message")
    message_id: uuid.UUID = Field(..., description="ID of the message piece to score")
    value: StrictBool = Field(..., description="Whether the attack objective was achieved")
    rationale: str = Field(default="", description="Optional explanation for the score")
    update_attack: bool = Field(
        default=False,
        description="Whether to make this the attack's human score and update its outcome",
    )
