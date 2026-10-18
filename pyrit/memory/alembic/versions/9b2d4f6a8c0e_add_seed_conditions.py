# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist objective conditions independently of seed metadata.

Existing rows retain NULL and load without criteria. Downgrading drops all
seed-authored condition data.

Revision ID: 9b2d4f6a8c0e
Revises: 7a9c1e3f5b2d
Create Date: 2026-09-18 23:30:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "9b2d4f6a8c0e"
down_revision: str | None = "7a9c1e3f5b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable JSON objective criteria without rewriting existing rows."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("conditions", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop objective criteria; downgrading irreversibly loses condition data."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_column("conditions")
