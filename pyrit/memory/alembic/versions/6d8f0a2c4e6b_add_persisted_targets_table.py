# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add persisted API-created targets table.

Revision ID: 6d8f0a2c4e6b
Revises: 8e2c4a6b0d13
Create Date: 2026-08-28 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d8f0a2c4e6b"
down_revision: str | None = "8e2c4a6b0d13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "PersistedTargets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_registry_name", sa.String(length=512), nullable=False),
        sa.Column("target_type", sa.String(length=128), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("auth_mode", sa.String(length=16), nullable=False),
        sa.Column("secret_uri", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_registry_name"),
    )


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_table("PersistedTargets")
