# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add an optimistic concurrency version to finding edits.

Revision ID: 8c2d4e6f0a13
Revises: d43b1bcf9c56
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c2d4e6f0a13"
down_revision: str | None = "d43b1bcf9c56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Initialize existing and new findings at version one."""
    op.add_column("FindingEntries", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    """Remove finding edit versions."""
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.drop_column("version")
