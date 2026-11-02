# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persist general technique flag on seed entries."""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "064a1eff19fc"
down_revision: str | None = "c8d3e5f7a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add is_general_technique column and default existing rows."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("is_general_technique", sa.Boolean(), nullable=False, server_default=sa.false()))
    seed_prompt_entries = sa.table(
        "SeedPromptEntries",
        sa.column("is_general_technique", sa.Boolean()),
        sa.column("seed_type", sa.String()),
    )
    op.execute(
        seed_prompt_entries.update()
        .where(seed_prompt_entries.c.seed_type == "simulated_conversation")
        .values(is_general_technique=True)
    )


def downgrade() -> None:
    """Drop is_general_technique column from seed entries."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_column("is_general_technique", mssql_drop_default=True)
