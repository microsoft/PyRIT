# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scorer wrapper participation outside public score metadata.

Revision ID: e7c4a2d9f013
Revises: c8d3e5f7a901
Create Date: 2026-11-02 08:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c4a2d9f013"
down_revision: str | None = "c8d3e5f7a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable storage for private scorer participation trees."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("snapshot_participation", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    """Remove private scorer participation storage."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("snapshot_participation")
