# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Drop the obsolete additional initializers table.

Revision ID: 0f2e4d6c8b1a
Revises: 8e2c4a6b0d13
Create Date: 2026-09-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f2e4d6c8b1a"
down_revision: str | None = "8e2c4a6b0d13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.drop_table("AdditionalInitializers")


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.create_table(
        "AdditionalInitializers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("initializer_name", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.INTEGER(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
