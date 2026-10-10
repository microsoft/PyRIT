# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Rename ScenarioResultEntries.scenario_init_data to scenario_identifier.

The scenario result now stores a single canonical ``ScenarioIdentifier`` in
place of the loose ``scenario_init_data`` blob. Rename the column and make it
non-nullable to match the model.

Revision ID: d4e6f8a0b2c4
Revises: c3d5e7f9a1b2
Create Date: 2026-07-02 10:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c4"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    # SQLite does not support ALTER COLUMN in place; batch_alter_table recreates
    # the table so the rename and NOT NULL change are portable across SQLite and
    # Azure SQL.
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "scenario_init_data",
            new_column_name="scenario_identifier",
            existing_type=sa.JSON(),
            existing_nullable=True,
            nullable=False,
        )


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "scenario_identifier",
            new_column_name="scenario_init_data",
            existing_type=sa.JSON(),
            existing_nullable=False,
            nullable=True,
        )
