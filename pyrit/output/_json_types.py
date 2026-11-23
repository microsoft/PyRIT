# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal, NotRequired, TypedDict

from pyrit.models import ChatMessageRole, PromptDataType, PromptResponseError, ScoreType


class ScorerNode(TypedDict):
    """Recursive behavioral identity; empty params and children are omitted."""

    class_name: str
    params: NotRequired[dict[str, object]]
    children: NotRequired[dict[str, "ScorerNode | list[ScorerNode]"]]


class ScorerInfo(ScorerNode):
    """Top-level scorer report; metrics is always present, including when unavailable."""

    metrics: dict[str, object] | None


class ScoreInfo(TypedDict):
    """Curated score fields; nullable values remain present rather than omitted."""

    scorer: str | None
    score_type: ScoreType
    score_value: str | None
    score_category: list[str] | None
    score_rationale: str | None
    objective: str | None


class ReasoningPieceInfo(TypedDict):
    """Reasoning-only piece; an unavailable summary is an explicit null."""

    data_type: Literal["reasoning"]
    reasoning_summary: str | None


class RegularPieceInfo(TypedDict):
    """Original/converted values; partial content and nonempty scores are optional."""

    data_type: PromptDataType
    original_value_data_type: PromptDataType
    converted_value_data_type: PromptDataType
    original_value: str
    converted_value: str
    response_error: PromptResponseError
    partial_content: NotRequired[str]
    scores: NotRequired[list[ScoreInfo]]


class MessageInfo(TypedDict):
    """A renderable conversation message, with reasoning or regular pieces."""

    role: ChatMessageRole
    is_simulated: bool
    pieces: list[ReasoningPieceInfo | RegularPieceInfo]


class ScenarioInfo(TypedDict):
    """Scenario identity and display metadata."""

    name: str
    id: str
    version: int
    pyrit_version: str
    description: str


class TargetInfo(TypedDict):
    """Target display fields; absent identifiers produce null values."""

    type: str | None
    model: str | None
    endpoint: str | None


class GroupInfo(TypedDict):
    """Aggregate execution statistics for one display group."""

    name: str
    num_objective_executions: int
    num_attempts: int
    success_rate: int
    outcomes: dict[str, object] | None
    producer_counts: dict[str, object] | None


class ScenarioStats(TypedDict):
    """Aggregate statistics for the complete scenario."""

    total_techniques: int
    total_objective_executions: int
    total_attempts: int
    overall_success_rate: int
    outcomes: dict[str, object] | None
    producer_counts: dict[str, object] | None
    unique_objectives: int


class ScenarioOverview(TypedDict):
    """Overview view; scorer is required but may be null."""

    view: Literal["overview"]
    scenario: ScenarioInfo
    target: TargetInfo
    scorer: ScorerInfo | None
    stats: ScenarioStats
    groups: list[GroupInfo]


class AttackInfo(TypedDict):
    """Per-attack summary shared by attacks and conversation views."""

    id: str
    technique: str
    objective: str
    outcome: str
    executed_turns: int
    score: str | None


class ScenarioAttacks(TypedDict):
    """Filtered attacks view, including the shown and total counts."""

    view: Literal["attacks"]
    attacks: list[AttackInfo]
    shown: int
    total: int


class ConversationInfo(AttackInfo):
    """An attack summary with its conversation identifier and structured messages."""

    conversation_id: str | None
    messages: list[MessageInfo]


class ScenarioConversations(TypedDict):
    """Conversation-only document envelope."""

    view: Literal["conversations"]
    scenario_result_id: str
    conversations: list[ConversationInfo]


class ScenarioFull(TypedDict):
    """Full JSON/HTML report envelope, including its overview and conversations."""

    view: Literal["full"]
    scenario_result_id: str
    overview: ScenarioOverview
    conversations: list[ConversationInfo]
