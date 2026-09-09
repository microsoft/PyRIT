# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, TypeVar

from pydantic import AwareDatetime, Field, field_serializer, model_validator

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.messages.conversation_reference import ConversationReference, ConversationType
from pyrit.models.messages.message_piece import MessagePiece
from pyrit.models.results.strategy_result import StrategyResult
from pyrit.models.retry_event import RetryEvent
from pyrit.models.score import Score

AttackResultT = TypeVar("AttackResultT", bound="AttackResult")

ATTRIBUTION_FIELDS: tuple[str, str] = ("operator", "operation")
ATTRIBUTION_VALUE_MAX_LENGTH: int = 128


def normalize_attribution_values(*, field: str, raw: Any, allow_multiple: bool) -> tuple[str, ...]:
    """
    Validate one attribution value, or a sequence of them, as bounded strings.

    Args:
        field (str): Name used in error messages.
        raw (Any): A single value or a sequence of values.
        allow_multiple (bool): Whether a sequence of values is valid.

    Returns:
        tuple[str, ...]: The validated values.

    Raises:
        ValueError: If a value is not a string or exceeds the maximum length.
    """
    if isinstance(raw, str):
        values: tuple[Any, ...] = (raw,)
    elif allow_multiple and isinstance(raw, Sequence):
        values = tuple(raw)
    else:
        expected = "a string or a sequence of strings" if allow_multiple else "a string"
        raise ValueError(f"{field} must be {expected}")
    if any(not isinstance(value, str) for value in values):
        expected = "strings" if allow_multiple else "a string"
        raise ValueError(f"{field} must contain {expected}")
    if any(len(value) > ATTRIBUTION_VALUE_MAX_LENGTH for value in values):
        raise ValueError(f"{field} must be at most {ATTRIBUTION_VALUE_MAX_LENGTH} characters")
    return values


def pop_legacy_attribution_labels(
    *,
    labels: Mapping[str, Any],
    dedicated: Mapping[str, Any],
    allow_multiple: bool,
    old_item: str,
    new_item: str,
) -> tuple[dict[str, Any], dict[str, tuple[str, ...] | None]]:
    """
    Move legacy ``operator``/``operation`` label aliases onto their dedicated values.

    ``operator`` and ``operation`` used to live in the free-form ``labels`` mapping. They are
    now indexed columns, so every entry point accepts the old spelling for one more release
    and funnels it here. Both ``old_item`` and ``new_item`` are format strings taking a
    ``field`` placeholder, so each caller reports the deprecation in its own vocabulary.

    Args:
        labels (Mapping[str, Any]): Labels that may still carry the legacy aliases.
        dedicated (Mapping[str, Any]): Current dedicated values, keyed by field name.
        allow_multiple (bool): Whether each attribution field can contain multiple values.
        old_item (str): Deprecation message template for the old spelling.
        new_item (str): Deprecation message template for the replacement.

    Returns:
        tuple[dict[str, Any], dict[str, tuple[str, ...] | None]]: The labels with the aliases
        removed, and the resolved values per attribution field.

    Raises:
        ValueError: If an alias is invalid or disagrees with its dedicated value.
    """
    remaining = dict(labels)
    resolved: dict[str, tuple[str, ...] | None] = {}
    for field in ATTRIBUTION_FIELDS:
        current = dedicated.get(field)
        values = (
            None
            if current is None
            else normalize_attribution_values(field=field, raw=current, allow_multiple=allow_multiple)
        )
        if field in remaining:
            legacy_values = normalize_attribution_values(
                field=f"labels.{field}",
                raw=remaining.pop(field),
                allow_multiple=allow_multiple,
            )
            if values is not None and set(values) != set(legacy_values):
                raise ValueError(f"{field} conflicts with legacy labels.{field}: {values!r} != {legacy_values!r}")
            print_deprecation_message(
                old_item=old_item.format(field=field),
                new_item=new_item.format(field=field),
                removed_in="1.4.0",
            )
            values = legacy_values
        resolved[field] = values
    return remaining, resolved


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


class AttackResult(StrategyResult):
    """Base class for all attack results."""

    ATTRIBUTION_VALUE_MAX_LENGTH: ClassVar[int] = ATTRIBUTION_VALUE_MAX_LENGTH

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
    last_response: MessagePiece | None = None

    # Score assigned to the final response by a scorer component
    last_score: Score | None = None

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
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Flexible conversation refs (nothing unused)
    related_conversations: set[ConversationReference] = Field(default_factory=set)

    # Arbitrary metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    # First-class attribution fields. These are deliberately separate from
    # arbitrary labels so they can be indexed and queried efficiently.
    operator: str | None = Field(default=None, max_length=ATTRIBUTION_VALUE_MAX_LENGTH)
    operation: str | None = Field(default=None, max_length=ATTRIBUTION_VALUE_MAX_LENGTH)

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

    # Attribution / parent linkage (infrastructure-managed). Set by the attack
    # persistence path when an AttackResultAttribution is present on the
    # AttackContext. User code should not set these directly; ad-hoc
    # AttackResults created outside an orchestrator leave both fields as None
    # and the corresponding DB columns remain NULL.
    attribution_parent_id: str | None = None
    attribution_data: dict[str, Any] | None = None

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
        remaining, resolved = pop_legacy_attribution_labels(
            labels=normalized["labels"],
            dedicated={field: normalized.get(field) for field in ATTRIBUTION_FIELDS},
            allow_multiple=False,
            old_item="AttackResult.labels['{field}']",
            new_item="AttackResult.{field}",
        )
        for field, values in resolved.items():
            normalized[field] = values[0] if values else None
        normalized["labels"] = remaining
        return normalized

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
