# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scorer identifiers as content-addressed rows.

Revision ID: a6c8e0f2b4d6
Revises: e5f7a9c1b3d2
Create Date: 2026-07-13 12:00:00.000000
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

revision: str = "a6c8e0f2b4d6"
down_revision: str | None = "e5f7a9c1b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ScorerIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("scorer_type", sa.String(), nullable=True),
        sa.Column("score_aggregator", sa.String(), nullable=True),
        sa.Column("prompt_target_hash", sa.String(64), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["prompt_target_hash"], ["TargetIdentifiers.hash"], name="fk_scorer_identifiers_prompt_target_hash"
        ),
    )
    op.create_table(
        "ScorerIdentifierChildren",
        sa.Column("parent_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("child_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("parent_hash", "position"),
        sa.ForeignKeyConstraint(
            ["parent_hash"], ["ScorerIdentifiers.hash"], name="fk_scorer_identifier_children_parent_hash"
        ),
        sa.ForeignKeyConstraint(
            ["child_hash"], ["ScorerIdentifiers.hash"], name="fk_scorer_identifier_children_child_hash"
        ),
    )
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("scorer_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_score_entries_scorer_identifier_hash",
            "ScorerIdentifiers",
            ["scorer_identifier_hash"],
            ["hash"],
        )

    bind = op.get_bind()
    run_best_effort_backfill(bind=bind, name="ScorerIdentifiers", backfill=_backfill_scorer_identifiers)


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("scorer_identifier_hash")
    op.drop_table("ScorerIdentifierChildren")
    op.drop_table("ScorerIdentifiers")


def _backfill_scorer_identifiers() -> None:
    """Backfill scorer rows and score foreign keys from the retained JSON column."""
    bind = op.get_bind()
    score_rows = bind.execute(
        sa.text('SELECT id, scorer_class_identifier FROM "ScoreEntries" WHERE scorer_class_identifier IS NOT NULL')
    ).fetchall()
    score_update = sa.text('UPDATE "ScoreEntries" SET scorer_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for score_id, raw_scorer in score_rows:
        identifier = load_identifier(raw_scorer)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_scorer(identifier)
            if identifier_hash:
                bind.execute(score_update, {"hash": identifier_hash, "id": score_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScorerIdentifiers backfill: could not reconstruct scorer for score {score_id}",
                exc_info=True,
            )

    if skipped:
        logger.warning(f"ScorerIdentifiers backfill skipped {skipped} score row(s)")
