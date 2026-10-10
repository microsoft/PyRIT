# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge seed origin and intermediate scores.

Revision ID: 6767741d8c6f
Revises: a6c8e0f2b4d7, 6d8f0a2c4e61
Create Date: 2026-10-01 10:19:57.966665
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6767741d8c6f"
down_revision: str | Sequence[str] | None = ("a6c8e0f2b4d7", "6d8f0a2c4e61")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
