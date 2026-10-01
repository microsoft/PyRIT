# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Distinguish retained intermediate scores from public scorer results."""

import sqlalchemy as sa
from alembic import op

revision: str = "6d8f0a2c4e61"
down_revision: str = "aca1eba410d9"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    """Mark existing scores as public results and allow storing intermediate results."""
    op.add_column("ScoreEntries", sa.Column("is_intermediate", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """
    Remove the schema only after callers explicitly clean up retained child judgments.

    Raises:
        ValueError: If nested scores would become independent roots in the old schema.
    """
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM ScoreEntries WHERE is_intermediate = 1")).scalar_one()
    if count:
        raise ValueError("Remove retained intermediate scores explicitly before downgrading score storage.")
    op.drop_column("ScoreEntries", "is_intermediate", mssql_drop_default=True)
