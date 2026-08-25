# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add persisted custom initializer source table.

Revision ID: 6b7c8d9e0f1a
Revises: 4c9a6e1f2b7d
Create Date: 2026-08-24 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b7c8d9e0f1a"
down_revision: str | None = "4c9a6e1f2b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "CustomInitializers",
        sa.Column("initializer_name", sa.String(length=64), nullable=False),
        sa.Column(
            "script_content",
            sa.UnicodeText().with_variant(sa.Text(), "sqlite"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("initializer_name"),
    )


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_table("CustomInitializers")