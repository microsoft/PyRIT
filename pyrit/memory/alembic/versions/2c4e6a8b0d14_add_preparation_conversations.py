# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add preparation conversation references to attack results.

Revision ID: 2c4e6a8b0d14
Revises: 1b3d5f7a9c2e
Create Date: 2026-09-10 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c4e6a8b0d14"
down_revision: str | None = "1b3d5f7a9c2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add storage for preparation conversation IDs."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.add_column(sa.Column("preparation_conversation_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove storage for preparation conversation IDs."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_column("preparation_conversation_ids")
