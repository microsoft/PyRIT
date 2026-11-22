# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store each prompt generation's position in its batch request.

Revision ID: ccf97e771f04
Revises: c4a401cd8201
Create Date: 2026-11-19 10:33:01.915509
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ccf97e771f04"
down_revision: str | None = "c4a401cd8201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the batch position. Existing generations get position 0, the position of a single generation."""
    with op.batch_alter_table("PromptGenerationEntries") as batch_op:
        batch_op.add_column(sa.Column("group_index", sa.INTEGER(), nullable=False, server_default=sa.text("0")))
    # The service always writes the position, so the default only fills existing rows.
    with op.batch_alter_table("PromptGenerationEntries") as batch_op:
        batch_op.alter_column("group_index", existing_type=sa.INTEGER(), existing_nullable=False, server_default=None)


def downgrade() -> None:
    """Remove the batch position."""
    with op.batch_alter_table("PromptGenerationEntries") as batch_op:
        batch_op.drop_column("group_index")
