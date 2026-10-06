# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Record seed ingestion origin without inferring historical provenance."""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "a6c8e0f2b4d7"
down_revision: str | None = "9b2d4f6a8c0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add bounded origin storage and default existing rows to unknown."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(16), nullable=False, server_default="unknown"))
        batch_op.create_index("ix_SeedPromptEntries_origin", ["origin"])


def downgrade() -> None:
    """Remove origin storage while preserving the seeds."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_index("ix_SeedPromptEntries_origin")
        batch_op.drop_column("origin", mssql_drop_default=True)
