# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist the full scoring expectation instead of a bare objective.

``ScoreEntries.objective`` held only the objective string a score was judged against.
``ScoreEntries.scored_expectation`` records the complete versioned expectation (objective
plus any typed conditions), so a persisted score keeps the whole of what it was scored
for. On upgrade the legacy objective is folded into an objective-only expectation; on
downgrade only the objective survives and typed conditions are dropped.

Revision ID: 1b3d5f7a9c2e
Revises: 0f2e4d6c8b1a
Create Date: 2026-09-03 10:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1b3d5f7a9c2e"
down_revision: str | None = "0f2e4d6c8b1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

#: Version stamped onto every backfilled expectation; matches ``ScoringExpectation.SCHEMA_VERSION``.
_SCHEMA_VERSION = 1

#: Rows per page so a large score table migrates in bounded keyset batches, not one statement.
_BACKFILL_BATCH_SIZE = 500


def upgrade() -> None:
    """Add ``scored_expectation``, fold the legacy objective into it, then drop ``objective``."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("scored_expectation", sa.JSON(), nullable=True))

    _backfill_scored_expectation()

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("objective")


def downgrade() -> None:
    """
    Re-add ``objective``, recover it from ``scored_expectation``, then drop the expectation.

    This is lossy by design: only the expectation's objective survives. Typed conditions
    have no column in the old schema and are dropped.
    """
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("objective", sa.String(), nullable=True))

    _backfill_objective()

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("scored_expectation")


def _backfill_scored_expectation() -> None:
    """
    Fold every non-null legacy objective into an objective-only versioned expectation.

    Rows are read a page at a time, keyed on ``id``, so a large score table is never pulled
    into memory at once. Scores with no objective keep a NULL expectation.
    """
    connection = op.get_bind()
    score_entries = sa.table(
        "ScoreEntries",
        sa.column("id"),
        sa.column("objective"),
        sa.column("scored_expectation"),
    )
    statement = sa.text('UPDATE "ScoreEntries" SET scored_expectation = :scored_expectation WHERE id = :score_id')

    last_id = None
    while True:
        conditions = [
            score_entries.c.objective.isnot(None),
            score_entries.c.scored_expectation.is_(None),
        ]
        if last_id is not None:
            conditions.append(score_entries.c.id > last_id)
        rows = connection.execute(
            sa.select(score_entries.c.id, score_entries.c.objective)
            .where(*conditions)
            .order_by(score_entries.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        last_id = rows[-1][0]

        updates = [
            {
                "score_id": score_id,
                "scored_expectation": json.dumps(
                    {"schema_version": _SCHEMA_VERSION, "objective": objective, "conditions": []}
                ),
            }
            for score_id, objective in rows
        ]
        connection.execute(statement, updates)


def _backfill_objective() -> None:
    """
    Recover the objective string from every stored expectation.

    Typed conditions cannot be represented by the old ``objective`` column and are dropped.
    Rows are read a page at a time, keyed on ``id``.
    """
    connection = op.get_bind()
    score_entries = sa.table(
        "ScoreEntries",
        sa.column("id"),
        sa.column("objective"),
        sa.column("scored_expectation"),
    )
    statement = sa.text('UPDATE "ScoreEntries" SET objective = :objective WHERE id = :score_id')

    last_id = None
    while True:
        conditions = [
            score_entries.c.scored_expectation.isnot(None),
            score_entries.c.objective.is_(None),
        ]
        if last_id is not None:
            conditions.append(score_entries.c.id > last_id)
        rows = connection.execute(
            sa.select(score_entries.c.id, score_entries.c.scored_expectation)
            .where(*conditions)
            .order_by(score_entries.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        last_id = rows[-1][0]

        updates = []
        for score_id, scored_expectation in rows:
            objective = _extract_objective(scored_expectation)
            if objective is None:
                continue
            updates.append({"score_id": score_id, "objective": objective})
        if updates:
            connection.execute(statement, updates)


def _extract_objective(scored_expectation: object) -> str | None:
    """
    Read the objective out of a stored expectation, tolerating either a dict or JSON text.

    Args:
        scored_expectation (object): The stored ``scored_expectation`` value.

    Returns:
        str | None: The objective string, or ``None`` when absent or unparsable.
    """
    if isinstance(scored_expectation, str):
        try:
            scored_expectation = json.loads(scored_expectation)
        except (ValueError, TypeError):
            return None
    if isinstance(scored_expectation, dict):
        objective = scored_expectation.get("objective")
        return objective if isinstance(objective, str) else None
    return None
