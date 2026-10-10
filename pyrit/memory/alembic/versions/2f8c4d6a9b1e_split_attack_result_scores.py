# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Split the attack result score into automated and human score references.

Revision ID: 2f8c4d6a9b1e
Revises: a4c6e8f0b2d1
Create Date: 2026-09-10 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "2f8c4d6a9b1e"
down_revision: str | None = "a4c6e8f0b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename legacy scores to automated scores and add human scores."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.alter_column("last_score_id", new_column_name="automated_score_id")
        batch_op.add_column(sa.Column("human_score_id", CustomUUID(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attack_results_human_score",
            "ScoreEntries",
            ["human_score_id"],
            ["id"],
        )


def downgrade() -> None:
    """Remove human scores and restore the legacy score column name."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_constraint("fk_attack_results_human_score", type_="foreignkey")
        batch_op.drop_column("human_score_id")
        batch_op.alter_column("automated_score_id", new_column_name="last_score_id")
