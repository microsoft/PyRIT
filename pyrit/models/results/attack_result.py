# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from typing_extensions import TypeVar

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.component_spec import RegisteredComponentReference  # noqa: TC001 (runtime-required by Pydantic)
from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.messages.conversation_reference import ConversationReference, ConversationType
from pyrit.models.messages.message_piece import MessagePiece
from pyrit.models.results.strategy_result import StrategyResult
from pyrit.models.retry_event import RetryEvent
from pyrit.models.score import Score
from pyrit.models.score.expectation import ScoringExpectation

if TYPE_CHECKING:
    from collections.abc import Mapping

AttackResultT = TypeVar("AttackResultT", bound="AttackResult")
_ResponsePieceT = TypeVar("_ResponsePieceT", bound=MessagePiece, default=MessagePiece)
_ScoreT = TypeVar("_ScoreT", bound=Score, default=Score)

ATTRIBUTION_FIELDS: tuple[str, str] = ("operator", "operation")
ATTRIBUTION_VALUE_MAX_LENGTH: int = 128


def normalize_legacy_attack_attribution(
    *,
    labels: Mapping[str, Any],
    operator: str | None,
    operation: str | None,
) -> tuple[dict[str, Any], str | None, str | None]:
    """
    Move scalar legacy label aliases to dedicated attribution fields.

    TODO(PyRIT 1.4): Remove this helper with legacy attribution label aliases.

    Args:
        labels (Mapping[str, Any]): Labels that may still carry the legacy aliases.
        operator (str | None): Dedicated operator value.
        operation (str | None): Dedicated operation value.

    Returns:
        tuple[dict[str, Any], str | None, str | None]: Arbitrary labels and resolved attribution.

    Raises:
        ValueError: If an alias is invalid or disagrees with its dedicated value.
    """
    remaining = dict(labels)
    resolved: dict[str, Any] = {"operator": operator, "operation": operation}
    for field in ATTRIBUTION_FIELDS:
        current = resolved[field]
        if current is not None and not isinstance(current, str):
            raise ValueError(f"{field} must be a string")
        if current is not None and len(current) > ATTRIBUTION_VALUE_MAX_LENGTH:
            raise ValueError(f"{field} must be at most {ATTRIBUTION_VALUE_MAX_LENGTH} characters")
        if field in remaining:
            legacy_value = remaining.pop(field)
            if not isinstance(legacy_value, str):
                raise ValueError(f"labels.{field} must be a string")
            if len(legacy_value) > ATTRIBUTION_VALUE_MAX_LENGTH:
                raise ValueError(f"labels.{field} must be at most {ATTRIBUTION_VALUE_MAX_LENGTH} characters")
            if current is not None and current != legacy_value:
                raise ValueError(f"{field} conflicts with legacy labels.{field}: {current!r} != {legacy_value!r}")
            print_deprecation_message(
                old_item=f"labels.{field}",
                # The dedicated operation name is itself deprecated in favor of a saved Operation's ID.
                new_item="operation_id" if field == "operation" else field,
                removed_in="1.4.0",
            )
            resolved[field] = legacy_value
    return remaining, resolved["operator"], resolved["operation"]


class AttackOutcome(str, Enum):
    """
    Enum representing the possible outcomes of an attack.

    Inherits from ``str`` so that values serialize naturally in Pydantic
    models and REST responses without a dedicated mapping function.
    """

    # The attack was successful in achieving its objective
    SUCCESS = "success"

    # The attack failed to achieve its objective
    FAILURE = "failure"

    # The attack failed due to an infrastructure error (exception), not a defensive refusal
    ERROR = "error"

    # The outcome of the attack is unknown or could not be determined
    UNDETERMINED = "undetermined"


class AttackResultRole(str, Enum):
    """
    What a persisted attack result represents, recorded by the strategy that produced it.

    The role says which kind of record this is, not what happened. A target-facing result
    can still end before any request reaches the objective target (for example, a preparation
    failure), so the role is not proof that the target was called.
    """

    #: Produced by a strategy that sends its own requests to the objective target.
    TARGET_FACING = "target_facing"

    #: Produced by a strategy that only coordinates other attacks and has no target
    #: conversation of its own, such as ``SequentialAttack``.
    ORCHESTRATION = "orchestration"

    #: Read-side value for records that carry no recognized role, such as rows persisted
    #: before roles were recorded. Producers never write it.
    UNKNOWN = "unknown"


class ManualContinuation(BaseModel):
    """The source and handoff point of a copied automated conversation."""

    METADATA_KEY: ClassVar[str] = "manual_continuation"
    source_attack_result_id: str
    source_conversation_id: str
    source_response_id: str | None = None


class ScoringFailure(BaseModel):
    """Diagnostics for delivered evidence whose scoring failed without a score."""

    scorer: RegisteredComponentReference
    objective: str
    response_id: str
    conversation_id: str
    error: str


class AttackScoringInputs(BaseModel):
    """Attack-owned inputs for future evaluations, separate from completed scores."""

    model_config = ConfigDict(strict=True)
    METADATA_KEY: ClassVar[str] = "chat_scoring"
    scorer: RegisteredComponentReference | None = None
    revision: str = ""
    inherited: bool = False
    expectation: ScoringExpectation | None = None

    @field_validator("expectation", mode="before")
    @classmethod
    def _load_expectation(cls, value: Any) -> Any:
        """
        Restore versioned criteria without losing condition types.

        Returns:
            The reconstructed expectation or unchanged runtime input.
        """
        return ScoringExpectation.model_validate_persisted(value) if isinstance(value, dict) else value

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize the inputs into their attack metadata namespace.

        Returns:
            The metadata update, including an explicit Off when scorer is None.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}


class AttackResult(StrategyResult, Generic[_ResponsePieceT, _ScoreT]):
    """Base class for all attack results."""

    ATTRIBUTION_VALUE_MAX_LENGTH: ClassVar[int] = ATTRIBUTION_VALUE_MAX_LENGTH
    SCORING_ERRORS_KEY: ClassVar[str] = "chat_scoring_errors"

    #: ``metadata`` key for the registered name of the technique that produced this result, recorded
    #: when the atomic attack name alone does not identify it (for example, adaptive scenarios).
    TECHNIQUE_NAME_METADATA_KEY: ClassVar[str] = "technique_name"

    # Identity
    # Unique identifier of the conversation that produced this result
    conversation_id: str

    # Natural-language description of the attacker's objective
    objective: str

    # Database-assigned unique ID for this AttackResult row.
    # Auto-generated if not provided (e.g. when loading from DB, the persisted ID is passed in).
    attack_result_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Composite identifier combining the attack strategy identity with
    # seed identifiers from the dataset.
    # Contains the attack strategy as children["attack"] plus optional seeds.
    atomic_attack_identifier: ComponentIdentifier | None = None

    # Evidence
    # Model response generated in the final turn of the attack
    last_response: _ResponsePieceT | None = None

    # Score assigned to the final response by an automated scorer component
    automated_score: _ScoreT | None = None

    # Score assigned to the final response by a human
    human_score: _ScoreT | None = None

    # Metrics
    # Total number of turns that were executed
    executed_turns: int = 0

    # Total execution time of the attack in milliseconds
    execution_time_ms: int = 0

    # Outcome
    # The outcome of the attack, indicating success, failure, or undetermined
    outcome: AttackOutcome = AttackOutcome.UNDETERMINED

    # Optional reason for the outcome, providing additional context
    outcome_reason: str | None = None

    # Wall-clock time the result was created or persisted.
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    # Flexible conversation refs (nothing unused)
    related_conversations: set[ConversationReference] = Field(default_factory=set)

    # Arbitrary metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    # First-class attribution fields. These are deliberately separate from
    # arbitrary labels so they can be indexed and queried efficiently.
    operator: str | None = Field(default=None, max_length=ATTRIBUTION_VALUE_MAX_LENGTH)
    # TODO(PyRIT 1.4): Remove this name with Operation name attribution; use operation_id.
    operation: str | None = Field(default=None, max_length=ATTRIBUTION_VALUE_MAX_LENGTH)
    operation_id: uuid.UUID | None = None

    # labels associated with this attack result
    labels: dict[str, str] = Field(default_factory=dict)

    # Harm categories this attack targeted. Auto-populated from the attack's
    # SeedGroup (the deduplicated union of its seeds' harm_categories) when the
    # result is produced by an attack strategy.
    targeted_harm_categories: list[str] = Field(default_factory=list)

    # Error information (populated when attack fails with exception)
    error_message: str | None = None
    error_type: str | None = None
    error_traceback: str | None = None

    # Retry tracking
    retry_events: list[RetryEvent] = Field(default_factory=list)
    total_retries: int = 0

    # Infrastructure-managed parent linkage and producer metadata. Strategies
    # record their role even without an orchestrator; the parent ID stays None.
    attribution_parent_id: str | None = None
    attribution_data: dict[str, Any] | None = None

    @property
    def scoring_inputs(self) -> AttackScoringInputs | None:
        """The future evaluation inputs; None is unset, while a scorer of None is Off."""
        if AttackScoringInputs.METADATA_KEY not in self.metadata:
            return None
        return AttackScoringInputs.model_validate(self.metadata[AttackScoringInputs.METADATA_KEY])

    def get_scoring_failure(self, conversation_id: str) -> ScoringFailure | None:
        """
        Read a conversation's scoring error for the current objective.

        Returns:
            The retained diagnostics, or None when the conversation has no current failure.
        """
        value = self.metadata.get(self.SCORING_ERRORS_KEY, {}).get(conversation_id)
        if value is None:
            return None
        failure = ScoringFailure.model_validate(value)
        return failure if failure.objective == self.objective else None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_attribution_labels(cls, data: Any) -> Any:
        """
        Move legacy attribution label aliases to their dedicated fields.

        Returns:
            The normalized model input.

        Raises:
            ValueError: If an alias is not a string or conflicts with a dedicated field.
        """
        if not isinstance(data, dict) or not isinstance(data.get("labels"), dict):
            return data

        normalized = dict(data)
        remaining, operator, operation = normalize_legacy_attack_attribution(
            labels=normalized["labels"],
            operator=normalized.get("operator"),
            operation=normalized.get("operation"),
        )
        normalized["labels"] = remaining
        normalized["operator"] = operator
        normalized["operation"] = operation
        return normalized

    @property
    def last_score(self) -> Score | None:
        """The human score when present, otherwise the automated score."""
        return self.human_score or self.automated_score

    @property
    def technique_name(self) -> str | None:
        """The registered technique name recorded in ``metadata``, or None when absent."""
        name = self.metadata.get(self.TECHNIQUE_NAME_METADATA_KEY)
        return name if isinstance(name, str) and name else None

    def get_attack_strategy_identifier(self) -> ComponentIdentifier | None:
        """
        Return the attack strategy identifier from the composite atomic identifier.

        This replaces the removed ``attack_identifier`` property.
        Extracts the ``"attack"`` child from the nested ``"attack_technique"`` child
        of ``atomic_attack_identifier``.

        Falls back to ``children["attack"]`` for rows created before the nested
        structure was introduced.

        Returns:
            ComponentIdentifier | None: The attack strategy identifier, or ``None`` if
                ``atomic_attack_identifier`` is not set or the expected children are missing.

        """
        if self.atomic_attack_identifier is None:
            return None
        technique = self.atomic_attack_identifier.get_child("attack_technique")
        if technique is not None:
            return technique.get_child("attack")
        # Fallback for pre-nesting rows that had children["attack"] directly.
        return self.atomic_attack_identifier.get_child("attack")

    def get_conversations_by_type(self, conversation_type: ConversationType) -> list[ConversationReference]:
        """
        Return all related conversations of the requested type.

        Args:
            conversation_type (ConversationType): The type of conversation to filter by.

        Returns:
            list: A list of related conversations matching the specified type.

        """
        return [ref for ref in self.related_conversations if ref.conversation_type == conversation_type]

    def get_all_conversation_ids(self) -> set[str]:
        """
        Return the main conversation ID plus all related conversation IDs.

        Returns:
            set[str]: All conversation IDs associated with this attack.
        """
        return {self.conversation_id} | {ref.conversation_id for ref in self.related_conversations}

    def get_active_conversation_ids(self) -> set[str]:
        """
        Return the main conversation ID plus pruned (user-visible) related conversation IDs.

        Excludes adversarial chat conversations which are internal implementation details.

        Returns:
            set[str]: Main + pruned conversation IDs.
        """
        return {self.conversation_id} | {
            ref.conversation_id
            for ref in self.related_conversations
            if ref.conversation_type == ConversationType.PRUNED
        }

    def get_pruned_conversation_ids(self) -> list[str]:
        """
        Return IDs of pruned (branched) conversations only.

        Returns:
            list[str]: Pruned conversation IDs.
        """
        return [
            ref.conversation_id
            for ref in self.related_conversations
            if ref.conversation_type == ConversationType.PRUNED
        ]

    def includes_conversation(self, conversation_id: str) -> bool:
        """
        Check whether a conversation belongs to this attack (main or any related).

        Args:
            conversation_id (str): The conversation ID to check.

        Returns:
            bool: True if the conversation is part of this attack.
        """
        return conversation_id in self.get_all_conversation_ids()

    @field_serializer("related_conversations", when_used="json")
    def _serialize_related_conversations(
        self,
        related_conversations: set[ConversationReference],
    ) -> list[dict[str, Any]]:
        return [
            ref.model_dump(mode="json")
            for ref in sorted(
                related_conversations,
                key=lambda ref: (
                    ref.conversation_id,
                    ref.conversation_type.value,
                    ref.description or "",
                ),
            )
        ]

    def __str__(self) -> str:
        """
        Return a concise string representation of this attack result.

        Returns:
            str: Summary containing conversation ID, outcome, and objective preview.

        """
        return f"AttackResult: {self.conversation_id}: {self.outcome.value}: {self.objective[:50]}..."
