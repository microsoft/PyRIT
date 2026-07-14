# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scenario identifiers as content-addressed rows.

Revision ID: b7d9f1a3c5e7
Revises: a6c8e0f2b4d6
Create Date: 2026-07-13 13:00:00.000000
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

revision: str = "b7d9f1a3c5e7"
down_revision: str | None = "a6c8e0f2b4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ScenarioIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("techniques", sa.JSON(), nullable=True),
        sa.Column("datasets", sa.JSON(), nullable=True),
        sa.Column("objective_target_hash", sa.String(64), nullable=True),
        sa.Column("objective_scorer_hash", sa.String(64), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["objective_target_hash"],
            ["TargetIdentifiers.hash"],
            name="fk_scenario_identifiers_objective_target_hash",
        ),
        sa.ForeignKeyConstraint(
            ["objective_scorer_hash"],
            ["ScorerIdentifiers.hash"],
            name="fk_scenario_identifiers_objective_scorer_hash",
        ),
    )
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.add_column(sa.Column("scenario_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenario_result_entries_scenario_identifier_hash",
            "ScenarioIdentifiers",
            ["scenario_identifier_hash"],
            ["hash"],
        )

    bind = op.get_bind()
    run_best_effort_backfill(bind=bind, name="ScenarioIdentifiers", backfill=_backfill_scenario_identifiers)


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_column("scenario_identifier_hash")
    op.drop_table("ScenarioIdentifiers")


def _backfill_scenario_identifiers() -> None:
    """Backfill scenario rows and result foreign keys from the retained JSON column."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text('SELECT id, scenario_identifier FROM "ScenarioResultEntries" WHERE scenario_identifier IS NOT NULL')
    ).fetchall()
    update_stmt = sa.text('UPDATE "ScenarioResultEntries" SET scenario_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for result_id, raw_scenario in result_rows:
        identifier = load_identifier(raw_scenario)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_scenario(identifier)
            if identifier_hash:
                bind.execute(update_stmt, {"hash": identifier_hash, "id": result_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScenarioIdentifiers backfill: could not reconstruct scenario for result {result_id}",
                exc_info=True,
            )

    if skipped:
        logger.warning(f"ScenarioIdentifiers backfill skipped {skipped} scenario result row(s)")
