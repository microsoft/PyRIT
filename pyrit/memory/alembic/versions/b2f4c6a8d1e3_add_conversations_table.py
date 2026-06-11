# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Introduce the Conversations table for conversation-scoped metadata and stop
stamping that metadata onto every PromptMemoryEntry row.

Creates ``Conversations`` (one row per ``conversation_id``) holding the target
identifier, backfills it from the existing
``PromptMemoryEntries.prompt_target_identifier`` column (plus placeholder rows for
conversation_ids referenced only by ``AttackResultEntries``), and drops the now
per-row ``prompt_target_identifier`` and ``attack_identifier`` columns from
``PromptMemoryEntries``.

Revision ID: b2f4c6a8d1e3
Revises: 9c8b7a6d5e4f
Create Date: 2026-05-20 12:00:00.000000
"""

from __future__ import annotations

import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2f4c6a8d1e3"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "Conversations",
        sa.Column("conversation_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("target_identifier", sa.JSON(), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
    )

    _backfill_conversations()

    # Stop persisting conversation-scoped metadata per row: the target identifier now
    # lives in Conversations, and the attack identifier is no longer stamped on pieces
    # (resolved via AttackResult). Batch op for SQLite portability.
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.drop_column("prompt_target_identifier")
        batch_op.drop_column("attack_identifier")


def downgrade() -> None:
    """Revert this schema upgrade."""
    # Re-add the dropped columns (data is not restored) then drop Conversations.
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.add_column(sa.Column("prompt_target_identifier", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("attack_identifier", sa.JSON(), nullable=True))
    op.drop_table("Conversations")


def _backfill_conversations() -> None:
    """
    Populate ``Conversations`` with one row per distinct ``conversation_id``.

    The target identifier is taken from the existing
    ``PromptMemoryEntries.prompt_target_identifier`` column, preferring a non-null
    value when a conversation has rows with differing targets (a non-null target
    always wins over null; a WARNING is logged if two distinct non-null targets are
    seen for the same conversation). Conversation ids that are referenced only by
    ``AttackResultEntries`` (no prompt rows) get a placeholder row with a null
    target so reads/joins stay consistent.

    Idempotent: only conversation_ids not already present in ``Conversations`` are
    inserted.
    """
    bind = op.get_bind()

    existing_ids = {row[0] for row in bind.execute(sa.text('SELECT conversation_id FROM "Conversations"')).fetchall()}

    targets_by_conversation: dict[str, str | None] = {}
    conflict_warnings = 0

    prompt_rows = bind.execute(
        sa.text(
            "SELECT conversation_id, prompt_target_identifier "
            'FROM "PromptMemoryEntries" '
            "WHERE conversation_id IS NOT NULL "
            "ORDER BY sequence"
        )
    ).fetchall()

    for conversation_id, target_identifier in prompt_rows:
        if conversation_id is None:
            continue
        current = targets_by_conversation.get(conversation_id, "__unset__")
        if current == "__unset__":
            targets_by_conversation[conversation_id] = target_identifier
        elif target_identifier is not None:
            if current is None:
                targets_by_conversation[conversation_id] = target_identifier
            elif current != target_identifier:
                conflict_warnings += 1
                logger.warning(
                    f"Backfill: conversation_id {conversation_id!r} has multiple distinct "
                    f"target identifiers; keeping the first non-null value."
                )

    # Conversation ids referenced only by AttackResultEntries (no prompt rows).
    attack_rows = bind.execute(
        sa.text('SELECT DISTINCT conversation_id FROM "AttackResultEntries" WHERE conversation_id IS NOT NULL')
    ).fetchall()
    for (conversation_id,) in attack_rows:
        if conversation_id is not None and conversation_id not in targets_by_conversation:
            targets_by_conversation[conversation_id] = None

    insert_stmt = sa.text(
        'INSERT INTO "Conversations" (conversation_id, target_identifier, pyrit_version) '
        "VALUES (:cid, :target, :version)"
    )

    inserted = 0
    for conversation_id, target_identifier in targets_by_conversation.items():
        if conversation_id in existing_ids:
            continue
        bind.execute(
            insert_stmt,
            {"cid": conversation_id, "target": target_identifier, "version": None},
        )
        inserted += 1

    if inserted or conflict_warnings:
        logger.info(
            f"Conversations backfill: inserted {inserted} row(s); {conflict_warnings} target-conflict warning(s)."
        )
