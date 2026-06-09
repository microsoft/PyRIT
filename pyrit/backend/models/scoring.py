# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scoring request/response models.

DTOs for the on-demand scoring surface exposed under ``/api/scorers`` and
``/api/attacks/{id}/conversations/{cid}/scores``. Distinct from the planned
read-only scorer-introspection surface (eval metrics, etc.) — this file only
covers the inputs and outputs needed to *invoke* a registered scorer.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.attacks import Score

__all__ = [
    "ScorerSummary",
    "ScorerListResponse",
    "ScoreConversationMode",
    "ScoreConversationRequest",
    "ScoreMessageRequest",
    "ScoreResponse",
]


ScoreConversationMode = Literal["last_message", "whole_conversation"]


class ScorerSummary(BaseModel):
    """Minimal scorer entry used to populate the scoring dialog."""

    scorer_registry_name: str = Field(..., description="Registry name of the scorer instance")
    scorer_type: str = Field(..., description="Scorer class name (e.g., 'SelfAskRefusalScorer')")
    score_type: str = Field(..., description="Score shape: 'true_false', 'float_scale', or 'unknown'")
    description: str | None = Field(
        None,
        description=(
            "First paragraph of the scorer class docstring. Surfaces in the GUI as an info pane so users "
            "can see what each scorer does without leaving the dialog."
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Registry tags (e.g. 'refusal', 'best_refusal'). Used in the GUI for grouping/badges.",
    )


class ScorerListResponse(BaseModel):
    """Response listing every registered scorer."""

    items: list[ScorerSummary] = Field(..., description="Registered scorers in registry-name order")


class ScoreConversationRequest(BaseModel):
    """Request to score a conversation with a registered scorer."""

    scorer_registry_name: str = Field(..., description="Registry name of the scorer to invoke")
    mode: ScoreConversationMode = Field(
        "last_message",
        description=(
            "'last_message' scores only the most recent assistant message; "
            "'whole_conversation' wraps the scorer in a ConversationScorer and scores the full transcript."
        ),
    )
    objective: str | None = Field(
        None, description="Optional objective to pass to the scorer (only used by objective scorers)"
    )


class ScoreMessageRequest(BaseModel):
    """Request to score a single message piece with a registered scorer."""

    scorer_registry_name: str = Field(..., description="Registry name of the scorer to invoke")
    objective: str | None = Field(
        None, description="Optional objective to pass to the scorer (only used by objective scorers)"
    )


class ScoreResponse(BaseModel):
    """Response containing the scores produced by an on-demand scoring call."""

    scores: list[Score] = Field(default_factory=list, description="Scores produced by the scorer")
