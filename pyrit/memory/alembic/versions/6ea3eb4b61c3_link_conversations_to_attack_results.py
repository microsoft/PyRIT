# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Link each conversation to the attack execution that owns it.

Adds a nullable, indexed ``attack_result_id`` to ``Conversations``. Existing rows
retain NULL. Downgrading drops the link.

Revision ID: 6ea3eb4b61c3
Revises: 6767741d8c6f
Create Date: 2026-10-01 12:00:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "6ea3eb4b61c3"
down_revision: str | None = "6767741d8c6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_Conversations_attack_result_id"


def upgrade() -> None:
    """Add the nullable attack result link and its lookup index."""
    op.add_column("Conversations", sa.Column("attack_result_id", CustomUUID(), nullable=True))
    op.create_index(_INDEX_NAME, "Conversations", ["attack_result_id"])


def downgrade() -> None:
    """Drop the attack result link."""
    op.drop_index(_INDEX_NAME, table_name="Conversations")
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.drop_column("attack_result_id")
