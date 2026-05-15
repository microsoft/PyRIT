# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
add error and retry fields to attack and scenario results.

Revision ID: 7a1b2c3d4e5f
Revises: 108a72344872
Create Date: 2026-05-13 11:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1b2c3d4e5f"
down_revision: str | None = "108a72344872"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    # AttackResultEntries: add error and retry tracking columns
    op.add_column("AttackResultEntries", sa.Column("error_message", sa.Unicode(), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("error_type", sa.String(), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("error_traceback", sa.Unicode(), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("retry_events_json", sa.Unicode(), nullable=True))
    op.add_column("AttackResultEntries", sa.Column("total_retries", sa.INTEGER(), nullable=True))

    # ScenarioResultEntries: add error tracking columns
    op.add_column("ScenarioResultEntries", sa.Column("error_attack_result_ids_json", sa.Unicode(), nullable=True))
    op.add_column("ScenarioResultEntries", sa.Column("error_message", sa.Unicode(), nullable=True))
    op.add_column("ScenarioResultEntries", sa.Column("error_type", sa.String(), nullable=True))


def downgrade() -> None:
    """Revert this schema upgrade."""
    # AttackResultEntries
    op.drop_column("AttackResultEntries", "error_message")
    op.drop_column("AttackResultEntries", "error_type")
    op.drop_column("AttackResultEntries", "error_traceback")
    op.drop_column("AttackResultEntries", "retry_events_json")
    op.drop_column("AttackResultEntries", "total_retries")

    # ScenarioResultEntries
    op.drop_column("ScenarioResultEntries", "error_attack_result_ids_json")
    op.drop_column("ScenarioResultEntries", "error_message")
    op.drop_column("ScenarioResultEntries", "error_type")
