# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score-related API request models."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManualScoreRequest(BaseModel):
    """Request to attach a user-supplied score to a message piece."""

    model_config = ConfigDict(extra="forbid")

    attack_result_id: uuid.UUID = Field(..., description="ID of the attack whose objective is being scored")
    message_id: uuid.UUID = Field(..., description="ID of the message piece to score")
    score_type: Literal["true_false", "float_scale"] = Field(
        ...,
        description="How to interpret the supplied value",
    )
    value: bool | float = Field(..., description="True/false verdict or float value between 0 and 1")
    success_threshold: float | None = Field(
        default=None,
        description="Minimum float score considered a successful attack",
    )
    rationale: str = Field(default="", description="Optional explanation for the score")

    @model_validator(mode="after")
    def _validate_typed_value(self) -> "ManualScoreRequest":
        """
        Ensure the value matches the selected score family.

        Returns:
            ManualScoreRequest: The validated request.
        """
        if self.score_type == "true_false":
            if not isinstance(self.value, bool):
                raise ValueError("true_false manual scores require a boolean value")
            if self.success_threshold is not None:
                raise ValueError("true_false manual scores do not accept a success threshold")
            return self

        if isinstance(self.value, bool):
            raise ValueError("float_scale manual scores require a numeric value")
        if not 0 <= self.value <= 1:
            raise ValueError("float_scale manual score value must be between 0 and 1")
        if self.success_threshold is None:
            self.success_threshold = 0.5
        if not 0 <= self.success_threshold <= 1:
            raise ValueError("float_scale manual score success threshold must be between 0 and 1")
        return self
