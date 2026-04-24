# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from pyrit.models.strategy_result import StrategyResult

if TYPE_CHECKING:
    from pyrit.identifiers.component_identifier import ComponentIdentifier
    from pyrit.models.conversation_reference import ConversationReference
    from pyrit.models.message_piece import MessagePiece
    from pyrit.models.score import Score

from pyrit.models.conversation_reference import ConversationType

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

    # Flexible conversation refs (nothing unused)
    related_conversations: set[ConversationReference] = field(default_factory=set)

    # Arbitrary metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def attack_identifier(self) -> Optional[ComponentIdentifier]:
        """
        Deprecated: use ``get_attack_strategy_identifier()`` or ``atomic_attack_identifier`` instead.

        Returns the attack strategy ``ComponentIdentifier`` extracted from
        ``atomic_attack_identifier``, emitting a deprecation warning.

        Returns:
            Optional[ComponentIdentifier]: The attack strategy identifier, or ``None``.

        """
        from pyrit.common.deprecation import print_deprecation_message

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
    original_init = cls.__init__  # type: ignore[misc]

    @functools.wraps(original_init)
    def wrapped_init(self: Any, *args: Any, **kwargs: Any) -> None:
        attack_identifier = kwargs.pop("attack_identifier", None)
        if attack_identifier is not None:
            from pyrit.common.deprecation import print_deprecation_message

            print_deprecation_message(
                old_item="AttackResult(attack_identifier=...)",
                new_item="AttackResult(atomic_attack_identifier=...)",
                removed_in="0.15.0",
            )
            if kwargs.get("atomic_attack_identifier") is None:
                from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier

                kwargs["atomic_attack_identifier"] = build_atomic_attack_identifier(
                    attack_identifier=attack_identifier,
                )
        original_init(self, *args, **kwargs)

    cls.__init__ = wrapped_init  # type: ignore[misc]
    return cls


_add_attack_identifier_compat(AttackResult)
