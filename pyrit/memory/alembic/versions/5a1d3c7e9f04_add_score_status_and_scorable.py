# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Give a score a completeness axis and something honest to point at.

``ScoreEntries.status`` separates "no verdict was reachable" from a false / 0.0
verdict, so ``score_value`` becomes nullable. ``ScoreEntries.scorable`` records what
the score was about, which a score over a trace, a file, or loose content could not
express through ``prompt_request_response_id``. ``ScorableContentEntries`` stores
loose content that previously had nowhere to live.

Revision ID: 5a1d3c7e9f04
Revises: 4c9a6e1f2b7d
Create Date: 2026-08-20 10:00:00.000000
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a1d3c7e9f04"
down_revision: str | None = "4c9a6e1f2b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

#: Rows per UPDATE so a large score table does not become one unbounded statement.
_BACKFILL_BATCH_SIZE = 500


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ScorableContentEntries",
        sa.Column("id", sa.Uuid().with_variant(sa.CHAR(36), "sqlite"), nullable=False),
        sa.Column("value", sa.Unicode(), nullable=False),
        sa.Column("value_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_type", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("scorable", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("scorable_content_id", sa.Uuid().with_variant(sa.CHAR(36), "sqlite"), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_score_entries_scorable_content_id",
            "ScorableContentEntries",
            ["scorable_content_id"],
            ["id"],
        )

    # Every stored score has a value, so every stored score is complete.
    op.execute(sa.text("UPDATE \"ScoreEntries\" SET status = 'complete' WHERE status IS NULL"))

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=16),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.alter_column(
            "score_value",
            existing_type=sa.String(),
            existing_nullable=False,
            nullable=True,
        )

    _backfill_message_scorables()


def downgrade() -> None:
    """
    Revert this schema upgrade.

    This is lossy by design. Undetermined scores have no value the old schema can hold,
    so they are deleted, and persisted loose content is dropped with its table.
    """
    # Attack results point at their last score, so release those references before the rows go.
    op.execute(
        sa.text(
            'UPDATE "AttackResultEntries" SET last_score_id = NULL WHERE last_score_id IN '
            '(SELECT id FROM "ScoreEntries" WHERE score_value IS NULL)'
        )
    )
    op.execute(sa.text('DELETE FROM "ScoreEntries" WHERE score_value IS NULL'))

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.alter_column(
            "score_value",
            existing_type=sa.String(),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.drop_constraint("fk_score_entries_scorable_content_id", type_="foreignkey")
        batch_op.drop_column("scorable_content_id")
        batch_op.drop_column("scorable")
        batch_op.drop_column("status")

    op.drop_table("ScorableContentEntries")


def _backfill_message_scorables() -> None:
    """
    Anchor existing scores on the message piece they already reference.

    Scores whose piece link was dropped (loose content scored before it was persisted)
    have no recoverable anchor and are left NULL.

    Rows are read a page at a time, keyed on ``id``, so a large score table is never
    pulled into memory at once.
    """
    connection = op.get_bind()
    score_entries = sa.table(
        "ScoreEntries",
        sa.column("id"),
        sa.column("prompt_request_response_id"),
        sa.column("scorable"),
    )
    statement = sa.text('UPDATE "ScoreEntries" SET scorable = :scorable WHERE id = :score_id')

    last_id = None
    while True:
        conditions = [
            score_entries.c.prompt_request_response_id.isnot(None),
            score_entries.c.scorable.is_(None),
        ]
        if last_id is not None:
            conditions.append(score_entries.c.id > last_id)
        rows = connection.execute(
            sa.select(score_entries.c.id, score_entries.c.prompt_request_response_id)
            .where(*conditions)
            .order_by(score_entries.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        last_id = rows[-1][0]

        updates = []
        for score_id, piece_id in rows:
            try:
                normalized = str(uuid.UUID(str(piece_id)))
            except (ValueError, AttributeError, TypeError):
                logger.warning("Skipping scorable backfill for score %s: unparsable piece id %r.", score_id, piece_id)
                continue
            updates.append(
                {
                    "score_id": score_id,
                    "scorable": json.dumps({"scorable_type": "message", "message_piece_ids": [normalized]}),
                }
            )

        if updates:
            connection.execute(statement, updates)
