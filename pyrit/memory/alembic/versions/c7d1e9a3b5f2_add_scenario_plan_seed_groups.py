# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
add scenario plan seed groups.

Revision ID: c7d1e9a3b5f2
Revises: a3f7c9e2b641
Create Date: 2026-11-16 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import pyrit.memory.memory_models

# revision identifiers, used by Alembic.
revision: str = "c7d1e9a3b5f2"
down_revision: str | None = "a3f7c9e2b641"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the seed groups each new scenario run planned, so resume doesn't read datasets."""
    op.create_table(
        "ScenarioPlanSeedGroupEntries",
        sa.Column("id", pyrit.memory.memory_models.CustomUUID(), nullable=False),
        sa.Column("scenario_result_id", pyrit.memory.memory_models.CustomUUID(), nullable=False),
        sa.Column("dataset_name", sa.Unicode(), nullable=False),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.Column("seed_group", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["scenario_result_id"], ["ScenarioResultEntries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ScenarioPlanSeedGroupEntries_scenario_result_id_position",
        "ScenarioPlanSeedGroupEntries",
        ["scenario_result_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    """Drop stored plan seed groups; runs planned after the upgrade can no longer resume."""
    op.drop_index(
        "ix_ScenarioPlanSeedGroupEntries_scenario_result_id_position", table_name="ScenarioPlanSeedGroupEntries"
    )
    op.drop_table("ScenarioPlanSeedGroupEntries")
