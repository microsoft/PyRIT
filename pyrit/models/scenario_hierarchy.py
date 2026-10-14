# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pyrit.models.catalog.scenario import AttackErrorSummary, AttackRetrySummary
from pyrit.models.results.attack_result import AttackOutcome, AttackResultRole
from pyrit.models.results.scenario_result import ScenarioRunState
from pyrit.models.retry_event import RetryEvent
from pyrit.models.scenario_progress import (
    ScenarioAttackTechniqueDetails,
    ScenarioProgressCounts,
    ScenarioProgressScore,
)


class ScenarioOrchestrationParentSummary(BaseModel):
    """
    Typed summary of an orchestration parent (e.g. SequentialAttack, Adaptive).

    Surfaces execution metrics, completion policies, ordered child result IDs,
    and retry pressure without equating orchestration results with direct target calls.
    """

    model_config = ConfigDict(extra="ignore")

    attack_result_id: str
    result_role: AttackResultRole = AttackResultRole.UNKNOWN
    outcome: AttackOutcome
    outcome_reason: str | None = None
    attempt_index: int | None = Field(default=None, ge=1)
    execution_time_ms: int = 0
    executed_turns: int = 0
    completion_policy: str | None = None
    child_attack_result_ids: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    timestamp: AwareDatetime
    total_retries: int = 0
    retries: list[RetryEvent] = Field(default_factory=list)
    retries_truncated: bool = False


class ScenarioConversationMessagePieceProjection(BaseModel):
    """
    Projection of an individual message piece preserving data type and multimodal content.
    """

    model_config = ConfigDict(extra="ignore")

    data_type: str = "text"
    content: str = ""
    converted_value: str = ""


class ScenarioConversationMessageProjection(BaseModel):
    """
    Generic, read-only message projection for conversation views.

    Contains display text, conversation role, sequence order, multimodal pieces, and linked score indicators.
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    content: str
    sequence: int
    timestamp: AwareDatetime | None = None
    scores: list[ScenarioProgressScore] = Field(default_factory=list)
    pieces: list[ScenarioConversationMessagePieceProjection] = Field(default_factory=list)


class ScenarioConversationProjection(BaseModel):
    """
    Generic read-only conversation projection belonging to an attack attempt.

    Carries conversation metadata and optional message lists for detailed views.
    """

    model_config = ConfigDict(extra="ignore")

    conversation_id: str
    conversation_type: str = "main"
    description: str | None = None
    message_count: int = 0
    messages: list[ScenarioConversationMessageProjection] = Field(default_factory=list)
    messages_truncated: bool = False


class ScenarioHierarchyNode(BaseModel):
    """
    One node in the scenario result hierarchy.

    Can represent an orchestration parent (with children) or a leaf target-facing attack.
    Maintains ordered child links, parent-relative index, and unresolved child references.
    """

    model_config = ConfigDict(extra="ignore")

    attack_result_id: str
    conversation_id: str = ""
    parent_attack_result_id: str | None = None
    child_index: int | None = None
    result_role: AttackResultRole = AttackResultRole.UNKNOWN
    attempt_index: int | None = Field(default=None, ge=1)
    atomic_attack_name: str
    atomic_group_id: str
    seed_group_id: str
    objective: str
    outcome: AttackOutcome
    outcome_reason: str | None = None
    execution_time_ms: int = 0
    executed_turns: int = 0
    timestamp: AwareDatetime
    score: ScenarioProgressScore | None = None
    error_type: str | None = None
    error_message: str | None = None
    total_retries: int = 0
    retries: list[RetryEvent] = Field(default_factory=list)
    retries_truncated: bool = False

    # Stored references from producer
    child_attack_result_ids: list[str] = Field(default_factory=list)

    # Resolved children in stored order (bounded traversal)
    children: list["ScenarioHierarchyNode"] = Field(default_factory=list)

    # Missing, malformed, cyclic, or cross-run references that could not be safely resolved
    unresolved_child_attack_result_ids: list[str] = Field(default_factory=list)

    # Generic read-only conversations for this attack
    conversations: list[ScenarioConversationProjection] = Field(default_factory=list)

    # Orchestration details if role is ORCHESTRATION or has children
    orchestration: ScenarioOrchestrationParentSummary | None = None


class ScenarioHierarchyGroup(BaseModel):
    """One atomic attack group's hierarchy projection."""

    model_config = ConfigDict(extra="ignore")

    atomic_group_id: str
    atomic_attack_name: str
    display_group: str
    # Behavioral technique identity projected once per atomic group
    technique: ScenarioAttackTechniqueDetails | None = None
    roots: list[ScenarioHierarchyNode] = Field(default_factory=list)
    # Reuses existing #3062 / #3043 progress counters unchanged
    counts: ScenarioProgressCounts


class ScenarioHierarchyDiagnostics(BaseModel):
    """
    Bounded diagnostic detail with exact aggregate totals.

    Diagnostics lists are capped to bound payload sizes, but total counts
    remain exact and truncation is signaled explicitly.
    """

    model_config = ConfigDict(extra="ignore")

    failed_attacks: list[AttackErrorSummary] = Field(default_factory=list)
    failed_attacks_count: int = 0
    failed_attacks_truncated: bool = False

    attack_retries: list[AttackRetrySummary] = Field(default_factory=list)
    attack_retries_count: int = 0
    attack_retries_truncated: bool = False

    total_retries: int = 0


class ScenarioHierarchyProjection(BaseModel):
    """Complete consumed, typed, bounded Scenario result hierarchy projection."""

    model_config = ConfigDict(extra="ignore")

    scenario_result_id: str
    scenario_name: str
    scenario_version: int
    status: ScenarioRunState
    groups: list[ScenarioHierarchyGroup] = Field(default_factory=list)
    overall_counts: ScenarioProgressCounts
    diagnostics: ScenarioHierarchyDiagnostics = Field(default_factory=ScenarioHierarchyDiagnostics)
    truncated: bool = False


ScenarioHierarchyNode.model_rebuild()
ScenarioHierarchyGroup.model_rebuild()
ScenarioHierarchyProjection.model_rebuild()
