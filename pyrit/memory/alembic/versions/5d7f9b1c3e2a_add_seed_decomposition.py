# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist decomposed objectives: each objective's parent and its ordered child paths.

Existing rows have no parent or paths and load as standalone objectives. Downgrading drops the
decomposition, leaving every objective standalone.

Revision ID: 5d7f9b1c3e2a
Revises: d6de1045cca4
Create Date: 2026-11-06 18:00:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

revision: str = "5d7f9b1c3e2a"
down_revision: str | None = "d6de1045cca4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable parent link, indexed for subtree reads, and nullable JSON paths."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.add_column(sa.Column("parent_objective_id", CustomUUID(), nullable=True))
        batch_op.add_column(sa.Column("paths", sa.JSON(), nullable=True))
        batch_op.create_index("ix_SeedPromptEntries_parent_objective_id", ["parent_objective_id"])


def downgrade() -> None:
    """Drop the decomposition; downgrading irreversibly loses parent links and paths."""
    with op.batch_alter_table("SeedPromptEntries") as batch_op:
        batch_op.drop_index("ix_SeedPromptEntries_parent_objective_id")
        batch_op.drop_column("paths")
        batch_op.drop_column("parent_objective_id")
