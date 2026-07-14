# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Introduce the TargetIdentifiers table and reference it from Conversations.

Phase 1 (dual-write) of storing component identifiers as first-class,
content-addressed rows. Creates ``TargetIdentifiers`` (one row per distinct
target identifier, keyed by its content ``hash``, with promoted scalar query
columns) and ``TargetIdentifierChildren`` (a self-referential pivot mapping a
multi-target to its inner target identifiers), adds a nullable
``target_identifier_hash`` foreign key to ``Conversations``, and backfills all
three from the existing ``Conversations.target_identifier`` JSON column. The JSON
column is retained (reads still come from it), so this migration is purely
additive.

Revision ID: e5f7a9c1b3d2
Revises: d4e6f8a0b2c4
Create Date: 2026-07-10 12:00:00.000000
"""

from __future__ import annotations

import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.alembic.identifier_backfill import (
    IdentifierGraphInserter,
    load_identifier,
    run_best_effort_backfill,
)

# revision identifiers, used by Alembic.
revision: str = "e5f7a9c1b3d2"
down_revision: str | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "TargetIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("underlying_model_name", sa.String(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("top_p", sa.Float(), nullable=True),
        sa.Column("max_requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("supported_auth_modes", sa.JSON(), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
    )

    # Self-referential pivot mapping a multi-target to its inner target identifiers.
    # Both endpoints are content hashes into TargetIdentifiers; ``position`` preserves
    # the parent's ``targets`` list order. Named FK constraints for SQL Server / batch
    # portability.
    op.create_table(
        "TargetIdentifierChildren",
        sa.Column("parent_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("child_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("parent_hash", "position"),
        sa.ForeignKeyConstraint(
            ["parent_hash"], ["TargetIdentifiers.hash"], name="fk_target_identifier_children_parent_hash"
        ),
        sa.ForeignKeyConstraint(
            ["child_hash"], ["TargetIdentifiers.hash"], name="fk_target_identifier_children_child_hash"
        ),
    )

    # Batch op for SQLite portability (no ALTER TABLE ADD FOREIGN KEY on SQLite).
    # The FK constraint must be named explicitly: Alembic batch mode rejects an
    # unnamed constraint.
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.add_column(sa.Column("target_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_conversations_target_identifier_hash",
            "TargetIdentifiers",
            ["target_identifier_hash"],
            ["hash"],
        )

    bind = op.get_bind()
    run_best_effort_backfill(
        bind=bind,
        name="TargetIdentifiers",
        backfill=_backfill_target_identifiers,
    )


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.drop_column("target_identifier_hash")
    # Drop the child edge table before its referenced parent table.
    op.drop_table("TargetIdentifierChildren")
    op.drop_table("TargetIdentifiers")


def _backfill_target_identifiers() -> None:
    """
    Populate ``TargetIdentifiers`` / ``TargetIdentifierChildren`` and set
    ``Conversations.target_identifier_hash``.

    For every ``Conversations`` row with a non-null ``target_identifier`` JSON,
    reconstruct the ``TargetIdentifier`` (recomputing its content hash), insert the
    deduped ``TargetIdentifiers`` row if absent -- recursing into any inner
    ``targets`` first so the child edge foreign keys resolve -- record the
    ``parent_hash -> child_hash`` edges, and point the conversation's
    ``target_identifier_hash`` at the top-level row. Idempotent: hashes already present
    are not re-inserted. Rows whose stored target cannot be reconstructed are logged and
    skipped rather than aborting the upgrade.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text('SELECT conversation_id, target_identifier FROM "Conversations" WHERE target_identifier IS NOT NULL')
    ).fetchall()

    update_stmt = sa.text('UPDATE "Conversations" SET target_identifier_hash = :hash WHERE conversation_id = :cid')
    inserter = IdentifierGraphInserter(bind=bind)
    linked = 0
    skipped = 0
    for conversation_id, raw_target in rows:
        identifier = load_identifier(raw_target)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_target(identifier)
            if identifier_hash:
                bind.execute(update_stmt, {"hash": identifier_hash, "cid": conversation_id})
                linked += 1
        except Exception:
            skipped += 1
            logger.warning(f"TargetIdentifiers backfill skipped conversation {conversation_id!r}", exc_info=True)

    if linked or skipped:
        logger.info(f"TargetIdentifiers backfill linked {linked} conversation(s); skipped {skipped}.")
