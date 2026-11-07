# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist decomposed findings: each finding's parent, whether it was demonstrated, and its ordered paths.

Existing rows have no parent or paths and load as demonstrated, top-level findings. Downgrading drops the
decomposition and the demonstrated flag, leaving every finding standalone.

Revision ID: 7e1c3a5b9d20
Revises: 5d7f9b1c3e2a
Create Date: 2026-11-06 19:00:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

revision: str = "7e1c3a5b9d20"
down_revision: str | None = "5d7f9b1c3e2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the indexed parent link to another finding, the demonstrated flag, and nullable JSON paths."""
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.add_column(sa.Column("parent_finding_id", CustomUUID(), nullable=True))
        batch_op.add_column(sa.Column("demonstrated", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("paths", sa.JSON(), nullable=True))
        batch_op.create_foreign_key("fk_findings_parent", "FindingEntries", ["parent_finding_id"], ["id"])
        batch_op.create_index("ix_FindingEntries_parent_finding_id", ["parent_finding_id"])


def downgrade() -> None:
    """Drop the decomposition; downgrading irreversibly loses parent links, paths, and demonstrated flags."""
    with op.batch_alter_table("FindingEntries") as batch_op:
        batch_op.drop_index("ix_FindingEntries_parent_finding_id")
        batch_op.drop_constraint("fk_findings_parent", type_="foreignkey")
        batch_op.drop_column("paths")
        batch_op.drop_column("demonstrated", mssql_drop_default=True)
        batch_op.drop_column("parent_finding_id")
