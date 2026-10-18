# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index scenario-linked attack results for ascending progress deltas.

Revision ID: 6b8d0f2a4c1e
Revises: 4c9a6e1f2b7d
Create Date: 2026-08-06 19:41:22.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6b8d0f2a4c1e"
down_revision: str | None = "4c9a6e1f2b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_AttackResultEntries_attribution_parent_timestamp_id"


def upgrade() -> None:
    """Create the scenario progress keyset index."""
    op.create_index(
        _INDEX_NAME,
        "AttackResultEntries",
        ["attribution_parent_id", "timestamp", "id"],
    )


def downgrade() -> None:
    """Drop the scenario progress keyset index."""
    op.drop_index(_INDEX_NAME, table_name="AttackResultEntries")
