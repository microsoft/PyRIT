# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add is_general_technique flag to SeedPromptEntries.

Existing rows default to False. Downgrading removes the column.

Revision ID: e7a1b3c5d9f0
Revises: c8d3e5f7a901
Create Date: 2026-10-28 00:00:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1b3c5d9f0"
down_revision: str | None = "c8d3e5f7a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add is_general_technique storage and default existing rows to False."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("is_general_technique", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Remove is_general_technique column."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_column("is_general_technique", mssql_drop_default=True)
