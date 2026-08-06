# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Allow ScenarioResultEntries.objective_target_identifier to be null.

A scenario only resolves an objective target when it declares the
``objective_target`` parameter; a scenario that supplies its own targets leaves
``ScenarioIdentifier.objective_target`` unset. ``ScenarioResult`` models that as
``TargetIdentifier | None``, so the persisted column must permit null too. This
mirrors the sibling ``objective_scorer_identifier`` column.

Revision ID: b6d2f4a8c0e1
Revises: 4c9a6e1f2b7d
Create Date: 2026-08-06 05:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d2f4a8c0e1"
down_revision: str | None = "4c9a6e1f2b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    # SQLite cannot ALTER COLUMN in place; batch_alter_table recreates the table so
    # the NOT NULL relaxation is portable across SQLite and Azure SQL.
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "objective_target_identifier",
            existing_type=sa.JSON(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    """Revert this schema upgrade."""
    # Rows written while the column was nullable may hold SQL NULL, which the
    # restored NOT NULL constraint would reject. Normalize them to the JSON literal
    # ``null`` first, which is what the ORM already persists for an absent target.
    op.execute(
        sa.text(
            "UPDATE \"ScenarioResultEntries\" SET objective_target_identifier = 'null' "
            "WHERE objective_target_identifier IS NULL"
        )
    )
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "objective_target_identifier",
            existing_type=sa.JSON(),
            existing_nullable=True,
            nullable=False,
        )
