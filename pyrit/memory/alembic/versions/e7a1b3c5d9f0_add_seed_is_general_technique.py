# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add is_general_technique flag to SeedPromptEntries.

Existing simulated conversations retain True; other rows default to False.
Downgrading removes the column.

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
    """Persist the flag while preserving legacy simulated-conversation defaults."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("is_general_technique", sa.Boolean(), nullable=False, server_default=sa.false()))
    seeds = sa.table(
        "SeedPromptEntries",
        sa.column("seed_type", sa.String()),
        sa.column("is_general_technique", sa.Boolean()),
    )
    op.execute(
        seeds.update()
        .where(seeds.c.seed_type == op.inline_literal("simulated_conversation", type_=sa.String()))
        .values(is_general_technique=op.inline_literal(True, type_=sa.Boolean()))
    )


def downgrade() -> None:
    """Remove is_general_technique column."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_column("is_general_technique", mssql_drop_default=True)
