# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TypeVar

from pyrit.common.deprecation import print_deprecation_message
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.conversation_reference import ConversationReference, ConversationType
from pyrit.models.message_piece import MessagePiece
from pyrit.models.retry_event import RetryEvent
from pyrit.models.score import Score
from pyrit.models.strategy_result import StrategyResult

AttackResultT = TypeVar("AttackResultT", bound="AttackResult")


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


@dataclass
class AttackResult(StrategyResult):
    """Base class for all attack results."""

    # Identity
    # Unique identifier of the conversation that produced this result
    conversation_id: str

    # Natural-language description of the attacker's objective
    objective: str

    # Database-assigned unique ID for this AttackResult row.
    # Auto-generated if not provided (e.g. when loading from DB, the persisted ID is passed in).
    attack_result_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Composite identifier combining the attack strategy identity with
    # seed identifiers from the dataset.
    # Contains the attack strategy as children["attack"] plus optional seeds.
    atomic_attack_identifier: Optional[ComponentIdentifier] = None

    # Evidence
    # Model response generated in the final turn of the attack
    last_response: Optional[MessagePiece] = None

    # Score assigned to the final response by a scorer component
    last_score: Optional[Score] = None

    # Metrics
    # Total number of turns that were executed
    executed_turns: int = 0

    # Total execution time of the attack in milliseconds
    execution_time_ms: int = 0

    # Outcome
    # The outcome of the attack, indicating success, failure, or undetermined
    outcome: AttackOutcome = AttackOutcome.UNDETERMINED

    # Optional reason for the outcome, providing additional context
    outcome_reason: Optional[str] = None

    # Wall-clock time the result was created or persisted.
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Flexible conversation refs (nothing unused)
    related_conversations: set[ConversationReference] = field(default_factory=set)

    # Arbitrary metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # labels associated with this attack result
    labels: dict[str, str] = field(default_factory=dict)

    # Error information (populated when attack fails with exception)
    error_message: str | None = None
    error_type: str | None = None
    error_traceback: str | None = None

    # Retry tracking
    retry_events: list[RetryEvent] = field(default_factory=list)
    total_retries: int = 0

    # Attribution / parent linkage (infrastructure-managed). Set by the attack
    # persistence path when an AttackResultAttribution is present on the
    # AttackContext. User code should not set these directly; ad-hoc
    # AttackResults created outside an orchestrator leave both fields as None
    # and the corresponding DB columns remain NULL.
    attribution_parent_id: str | None = None
    attribution_data: dict[str, Any] | None = None

    @property
    def attack_identifier(self) -> Optional[ComponentIdentifier]:
        """
        Deprecated: use ``get_attack_strategy_identifier()`` or ``atomic_attack_identifier`` instead.

        Returns the attack strategy ``ComponentIdentifier`` extracted from
        ``atomic_attack_identifier``, emitting a deprecation warning.

        Returns:
            Optional[ComponentIdentifier]: The attack strategy identifier, or ``None``.

        """
        print_deprecation_message(
            old_item="AttackResult.attack_identifier",
            new_item="AttackResult.atomic_attack_identifier or get_attack_strategy_identifier()",
            removed_in="0.15.0",
        )
        return self.get_attack_strategy_identifier()

    def get_attack_strategy_identifier(self) -> Optional[ComponentIdentifier]:
        """
        Return the attack strategy identifier from the composite atomic identifier.

        This is the non-deprecated replacement for the ``attack_identifier`` property.
        Extracts the ``"attack"`` child from the nested ``"attack_technique"`` child
        of ``atomic_attack_identifier``.

        Falls back to ``children["attack"]`` for rows created before the nested
        structure was introduced.

        Returns:
            Optional[ComponentIdentifier]: The attack strategy identifier, or ``None`` if
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

    def __str__(self) -> str:
        """
        Return a concise string representation of this attack result.

        Returns:
            str: Summary containing conversation ID, outcome, and objective preview.

        """
        return f"AttackResult: {self.conversation_id}: {self.outcome.value}: {self.objective[:50]}..."

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this attack result to a JSON-compatible dictionary.

        Returns:
            dict[str, Any]: Serialized payload suitable for REST APIs or persistence.
        """
        return {
            "conversation_id": self.conversation_id,
            "objective": self.objective,
            "attack_result_id": self.attack_result_id,
            "atomic_attack_identifier": (
                self.atomic_attack_identifier.to_dict() if self.atomic_attack_identifier else None
            ),
            "last_response": self.last_response.to_dict() if self.last_response else None,
            "last_score": self.last_score.to_dict() if self.last_score else None,
            "executed_turns": self.executed_turns,
            "execution_time_ms": self.execution_time_ms,
            "outcome": self.outcome.value,
            "outcome_reason": self.outcome_reason,
            "timestamp": self.timestamp.isoformat(),
            "related_conversations": sorted(
                [ref.model_dump(mode="json") for ref in self.related_conversations],
                key=lambda r: r["conversation_id"],
            ),
            "metadata": self.metadata,
            "labels": self.labels,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "error_traceback": self.error_traceback,
            "retry_events": [e.model_dump(mode="json") for e in self.retry_events],
            "total_retries": self.total_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttackResult:
        """
        Reconstruct an AttackResult from a dictionary.

        Args:
            data (dict[str, Any]): Dictionary as produced by to_dict().

        Returns:
            AttackResult: Reconstructed instance.
        """
        return cls(
            conversation_id=data["conversation_id"],
            objective=data["objective"],
            attack_result_id=data.get("attack_result_id", str(uuid.uuid4())),
            atomic_attack_identifier=(
                ComponentIdentifier.from_dict(data["atomic_attack_identifier"])
                if data.get("atomic_attack_identifier")
                else None
            ),
            last_response=(MessagePiece.from_dict(data["last_response"]) if data.get("last_response") else None),
            last_score=Score.from_dict(data["last_score"]) if data.get("last_score") else None,
            executed_turns=data.get("executed_turns", 0),
            execution_time_ms=data.get("execution_time_ms", 0),
            outcome=AttackOutcome(data.get("outcome", "undetermined")),
            outcome_reason=data.get("outcome_reason"),
            timestamp=(
                datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(timezone.utc)
            ),
            related_conversations={
                ConversationReference.model_validate(r) for r in data.get("related_conversations", [])
            },
            metadata=data.get("metadata", {}),
            labels=data.get("labels", {}),
            error_message=data.get("error_message"),
            error_type=data.get("error_type"),
            error_traceback=data.get("error_traceback"),
            retry_events=[RetryEvent.model_validate(e) for e in data.get("retry_events", [])],
            total_retries=data.get("total_retries", 0),
        )


def _add_attack_identifier_compat(cls: type) -> type:
    """
    Wrap a dataclass ``__init__`` to accept the deprecated ``attack_identifier`` kwarg.

    When ``attack_identifier`` is passed, it is automatically promoted to
    ``atomic_attack_identifier`` via ``build_atomic_attack_identifier`` and a
    deprecation warning is emitted.

    Args:
        cls: The dataclass to wrap.

    Returns:
        The same class with a wrapped ``__init__``.

    """
    original_init = cls.__init__

    @functools.wraps(original_init)
    def wrapped_init(self: Any, *args: Any, **kwargs: Any) -> None:
        attack_identifier = kwargs.pop("attack_identifier", None)
        if attack_identifier is not None:
            print_deprecation_message(
                old_item="AttackResult(attack_identifier=...)",
                new_item="AttackResult(atomic_attack_identifier=...)",
                removed_in="0.15.0",
            )
            if kwargs.get("atomic_attack_identifier") is None:
                kwargs["atomic_attack_identifier"] = build_atomic_attack_identifier(
                    attack_identifier=attack_identifier,
                )
        original_init(self, *args, **kwargs)

    cls.__init__ = wrapped_init  # type: ignore[ty:invalid-assignment]
    return cls


_add_attack_identifier_compat(AttackResult)
