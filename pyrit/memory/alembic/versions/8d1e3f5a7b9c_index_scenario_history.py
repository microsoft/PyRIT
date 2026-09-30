# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index scenario results for descending history keyset pagination.

Revision ID: 8d1e3f5a7b9c
Revises: 0f2e4d6c8b1a
Create Date: 2026-08-06 22:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8d1e3f5a7b9c"
down_revision: str | None = "0f2e4d6c8b1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_ScenarioResultEntries_timestamp_id"


def upgrade() -> None:
    """Create the scenario history keyset index."""
    op.create_index(
        _INDEX_NAME,
        "ScenarioResultEntries",
        ["timestamp", "id"],
    )


def downgrade() -> None:
    """Drop the scenario history keyset index."""
    op.drop_index(_INDEX_NAME, table_name="ScenarioResultEntries")
