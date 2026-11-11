# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provider-call-free estimates of the work a prompt generator will do."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

_Count = Annotated[int, Field(strict=True, ge=0)]


class PromptGenerationStageEstimate(BaseModel):
    """Expected model calls and approximate total tokens for one generation stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str = Field(min_length=1)
    target_role: str = Field(min_length=1, description="The generator's name for the target that runs this stage")
    min_calls: _Count
    max_calls: _Count
    min_tokens: _Count = Field(description="Approximate input and output tokens with min_calls")
    max_tokens: _Count = Field(description="Approximate input and output tokens with max_calls")

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        if self.max_calls < self.min_calls or self.max_tokens < self.min_tokens:
            raise ValueError("Maximum calls and tokens must be at least the minimums.")
        return self


class PromptGenerationEstimate(BaseModel):
    """
    The remaining model calls and an approximate token range for a generation request.

    Token counts use a characters-per-token heuristic and typical output sizes, so they show
    the scale of a request rather than a bill. JSON validation retries and target-side
    tools such as web search can add more.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    LOW_TOKEN_FACTOR: ClassVar[float] = 0.7
    HIGH_TOKEN_FACTOR: ClassVar[float] = 1.5
    CHARACTERS_PER_TOKEN: ClassVar[int] = 4

    stages: tuple[PromptGenerationStageEstimate, ...] = ()
    exact_calls: bool = Field(description="Whether min_calls and max_calls are known exactly rather than bounded")
    notes: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_totals(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key not in cls.model_computed_fields}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def min_calls(self) -> int:
        """The fewest model calls the remaining stages need."""
        return sum(stage.min_calls for stage in self.stages)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_calls(self) -> int:
        """The most model calls the remaining stages need, without validation retries."""
        return sum(stage.max_calls for stage in self.stages)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def low_tokens(self) -> int:
        """A low estimate of input and output tokens."""
        return round(sum(stage.min_tokens for stage in self.stages) * self.LOW_TOKEN_FACTOR)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def high_tokens(self) -> int:
        """A high estimate of input and output tokens."""
        return round(sum(stage.max_tokens for stage in self.stages) * self.HIGH_TOKEN_FACTOR)

    @classmethod
    def tokens_from_characters(cls, characters: int) -> int:
        """
        Approximate a token count from a character count.

        Args:
            characters (int): Characters of text.

        Returns:
            int: The approximate number of tokens.
        """
        return -(-characters // cls.CHARACTERS_PER_TOKEN)
