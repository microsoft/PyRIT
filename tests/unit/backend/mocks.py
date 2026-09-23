# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared attack fixtures for backend service tests."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from pyrit.models import AtomicAttackIdentifier, AttackOutcome, AttackResult, ComponentIdentifier


def make_attack_result(
    *,
    conversation_id: str = "attack-1",
    attack_result_id: str = "",
    objective: str = "Test objective",
    has_target: bool = True,
    name: str = "Test Attack",
    outcome: AttackOutcome = AttackOutcome.UNDETERMINED,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> AttackResult:
    """Create a mock AttackResult for testing."""
    now = datetime.now(UTC)
    created = created_at or now
    updated = updated_at or now

    # Default attack_result_id to "ar-<conversation_id>" when not explicit.
    effective_ar_id = attack_result_id or f"ar-{conversation_id}"

    target_identifier = (
        ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )
        if has_target
        else None
    )

    return AttackResult(
        conversation_id=conversation_id,
        objective=objective,
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=name,
                class_module="pyrit.backend",
                children={"objective_target": target_identifier} if target_identifier else {},
            ),
        ),
        outcome=outcome,
        attack_result_id=effective_ar_id,
        timestamp=updated,
        metadata={
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
        },
        labels={"test_ar_label": "test_ar_value"},
    )


def _make_matching_target_mock() -> MagicMock:
    """Create a mock target object whose get_identifier() matches make_attack_result's default target."""
    mock_target = MagicMock()
    mock_target.get_identifier.return_value = ComponentIdentifier(
        class_name="TextTarget",
        class_module="pyrit.prompt_target",
    )
    return mock_target


def make_mock_memory() -> MagicMock:
    """Create a mock memory instance."""
    memory = MagicMock()
    memory.get_attack_results.return_value = []
    memory.get_conversation_messages.return_value = []
    memory.get_message_pieces.return_value = []
    memory.get_conversation_stats.return_value = {}
    memory._get_conversation.return_value = None
    memory.get_prompt_scores.return_value = []

    return memory
