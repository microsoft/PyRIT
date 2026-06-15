# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scoring request/response models.

DTOs for the on-demand scoring surface exposed under ``/api/scorers`` and
``/api/attacks/{id}/conversations/{cid}/scores``. Distinct from the planned
read-only scorer-introspection surface (eval metrics, etc.) — this file only
covers the inputs and outputs needed to *invoke* a registered scorer.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.attacks import Score

__all__ = [
    "ScorerSummary",
    "ScorerListResponse",
    "ScoreConversationMode",
    "ScoreConversationRequest",
    "ScoreMessageRequest",
    "ScoreResponse",
    "CustomScorerKind",
    "GeneralFloatScaleConfig",
    "GeneralTrueFalseConfig",
    "ThresholdWrapperConfig",
    "CustomScorerConfig",
    "CreateCustomScorerRequest",
    "UpdateCustomScorerRequest",
    "CustomScorerResponse",
]


ScoreConversationMode = Literal["last_message", "whole_conversation"]
CustomScorerKind = Literal["general_float_scale", "general_true_false", "threshold_wrapper"]
TrueFalseAggregator = Literal["OR", "AND", "MAJORITY"]


class GeneralFloatScaleConfig(BaseModel):
    """Form-driven config for a ``SelfAskGeneralFloatScaleScorer`` instance."""

    kind: Literal["general_float_scale"] = "general_float_scale"
    system_prompt_format_string: str = Field(
        ...,
        min_length=1,
        description=(
            "System prompt template. Placeholders: {objective}, {prompt}, {message_piece}, "
            "{min_value}, {max_value}. Must instruct the LLM to reply with JSON containing "
            "'score_value' (numeric in [min_value, max_value]) and 'rationale'."
        ),
    )
    prompt_format_string: str | None = Field(
        None,
        description="Optional user-prompt template with the same placeholders.",
    )
    category: str | None = Field(
        None, description="Category label applied to resulting Score rows when the LLM omits one."
    )
    min_value: int = Field(0, description="Minimum of the LLM's native scale.")
    max_value: int = Field(100, description="Maximum of the LLM's native scale; must be > min_value.")
    requires_objective: bool = Field(
        False,
        description=(
            "If True, the GUI requires the caller to supply an objective when invoking this scorer "
            "and the backend rejects scoring requests with no objective. Enable this only when your "
            "prompt template references {objective}; leaving it False makes the objective field hidden "
            "in the scoring dialog."
        ),
    )


class GeneralTrueFalseConfig(BaseModel):
    """Form-driven config for a ``SelfAskGeneralTrueFalseScorer`` instance."""

    kind: Literal["general_true_false"] = "general_true_false"
    system_prompt_format_string: str = Field(
        ...,
        min_length=1,
        description=(
            "System prompt template. Placeholders: {objective}, {task} (alias of {objective}), "
            "{prompt}, {message_piece}. Must instruct the LLM to reply with JSON containing "
            "'score_value' ('true'/'false') and 'rationale'."
        ),
    )
    prompt_format_string: str | None = Field(
        None, description="Optional user-prompt template with the same placeholders."
    )
    category: str | None = Field(
        None, description="Category label applied to resulting Score rows when the LLM omits one."
    )
    score_aggregator: TrueFalseAggregator = Field(
        "OR",
        description="How to combine multiple bool scores when the scorer runs more than one trial.",
    )
    requires_objective: bool = Field(
        False,
        description=(
            "If True, the GUI requires the caller to supply an objective when invoking this scorer "
            "and the backend rejects scoring requests with no objective. Enable this only when your "
            "prompt template references {objective} or {task}; leaving it False makes the objective "
            "field hidden in the scoring dialog."
        ),
    )


class ThresholdWrapperConfig(BaseModel):
    """Form-driven config for a ``FloatScaleThresholdScorer`` wrapping an existing float scorer."""

    kind: Literal["threshold_wrapper"] = "threshold_wrapper"
    wrapped_scorer_registry_name: str = Field(
        ...,
        min_length=1,
        description="Registry name of the float-scale scorer to wrap.",
    )
    threshold: float = Field(..., ge=0.0, le=1.0, description="Cut-off in [0, 1]. Scores >= threshold map to True.")


CustomScorerConfig = Annotated[
    GeneralFloatScaleConfig | GeneralTrueFalseConfig | ThresholdWrapperConfig,
    Field(discriminator="kind"),
]


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
    uses_objective: bool = Field(
        False,
        description=(
            "True if this scorer injects the caller-supplied objective into its scoring prompt so the "
            "judge LLM is conditioned on it. When False, the objective is only stored on the resulting "
            "Score row as metadata and has no effect on the scorer's verdict. Read off "
            "``Scorer.uses_objective``. The GUI hides the objective input for scorers where this is False."
        ),
    )
    editable: bool = Field(
        False,
        description=(
            "True for user-created scorers that can be edited or deleted via the custom-scorer API. "
            "Built-in (initializer-registered) scorers are always False."
        ),
    )
    custom_config: CustomScorerConfig | None = Field(
        None,
        description=(
            "When ``editable`` is True, the original form config used to build this scorer. Returned so "
            "the GUI can pre-fill the edit dialog. Null for built-in scorers."
        ),
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


class CreateCustomScorerRequest(BaseModel):
    """Request to instantiate and register a new user-defined scorer."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description=(
            "Registry name for the new scorer (alphanumeric, dash, underscore). Must not collide "
            "with an existing scorer."
        ),
    )
    config: CustomScorerConfig = Field(..., description="Type-discriminated scorer config.")


class UpdateCustomScorerRequest(BaseModel):
    """
    Request to replace the config of an existing user-defined scorer.

    The registry name does not change; only the underlying ``config`` is rebuilt.
    """

    config: CustomScorerConfig = Field(..., description="Replacement type-discriminated scorer config.")


class CustomScorerResponse(BaseModel):
    """Response returned after create/update of a user-defined scorer."""

    summary: ScorerSummary = Field(..., description="Fresh summary of the (re)registered scorer.")
