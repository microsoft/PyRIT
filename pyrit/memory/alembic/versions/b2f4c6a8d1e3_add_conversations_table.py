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
Revises: f1a2b3c4d5e6
Create Date: 2026-05-20 12:00:00.000000
"""

from __future__ import annotations

import logging
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "b2f4c6a8d1e3"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)

_CONVERSATION_INSERT_BATCH_SIZE = 400
_CONVERSATION_INSERT_PROGRESS_INTERVAL = 25
_CONVERSATION_INSERT_PREFIX = 'INSERT INTO "Conversations" (conversation_id, target_identifier, pyrit_version) VALUES '


def _report_progress(message: str) -> None:
    """Write migration progress to Alembic stdout, or the logger outside a migration context."""
    try:
        context = op.get_context()
    except (AttributeError, NameError):
        logger.info(message)
        return
    config = context.config
    if config is not None:
        config.print_stdout(message)
    else:
        logger.info(message)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "Conversations",
        sa.Column("conversation_id", sa.String(length=36), primary_key=True, nullable=False),
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

    rows_to_insert = [
        (conversation_id, target_identifier)
        for conversation_id, target_identifier in targets_by_conversation.items()
        if conversation_id not in existing_ids
    ]
    _insert_conversation_rows(bind=bind, rows=rows_to_insert)

    inserted = len(rows_to_insert)
    if inserted or conflict_warnings:
        logger.info(
            f"Conversations backfill: inserted {inserted} row(s); {conflict_warnings} target-conflict warning(s)."
        )


def _insert_conversation_rows(*, bind: Connection, rows: Sequence[tuple[str, str | None]]) -> None:
    """Insert conversation rows in bounded multi-value statements."""
    batch_count = (len(rows) + _CONVERSATION_INSERT_BATCH_SIZE - 1) // _CONVERSATION_INSERT_BATCH_SIZE
    if not batch_count:
        return

    _report_progress(f"Conversations backfill: inserting {len(rows)} row(s) in {batch_count} batch(es).")
    for batch_number, start in enumerate(range(0, len(rows), _CONVERSATION_INSERT_BATCH_SIZE), start=1):
        _insert_conversation_batch(bind=bind, rows=rows[start : start + _CONVERSATION_INSERT_BATCH_SIZE])
        if batch_number % _CONVERSATION_INSERT_PROGRESS_INTERVAL == 0 or batch_number == batch_count:
            _report_progress(f"Conversations backfill: completed insert batch {batch_number}/{batch_count}.")


def _insert_conversation_batch(*, bind: Connection, rows: Sequence[tuple[str, str | None]]) -> None:
    """Insert one non-empty batch of conversation rows."""
    value_clauses: list[str] = []
    parameters: dict[str, str | None] = {}
    for index, (conversation_id, target_identifier) in enumerate(rows):
        value_clauses.append(f"(:cid_{index}, :target_{index}, NULL)")
        parameters[f"cid_{index}"] = conversation_id
        parameters[f"target_{index}"] = target_identifier
    bind.execute(sa.text(_CONVERSATION_INSERT_PREFIX + ", ".join(value_clauses)), parameters)
