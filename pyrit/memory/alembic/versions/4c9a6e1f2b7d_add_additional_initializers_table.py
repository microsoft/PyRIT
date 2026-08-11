# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add persisted additional initializers table.

Revision ID: 4c9a6e1f2b7d
Revises: d7e9f1a3b5c6
Create Date: 2026-07-24 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c9a6e1f2b7d"
down_revision: str | None = "d7e9f1a3b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "AdditionalInitializers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("initializer_name", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.INTEGER(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_table("AdditionalInitializers")
