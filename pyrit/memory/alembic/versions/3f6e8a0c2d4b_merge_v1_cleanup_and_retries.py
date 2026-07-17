# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the v1 cleanup and conversation retry migration heads.

Revision ID: 3f6e8a0c2d4b
Revises: 24b44ef076b6, a1c3e5d7f9b0
Create Date: 2026-07-15 20:42:09.727000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3f6e8a0c2d4b"
down_revision: str | Sequence[str] | None = ("24b44ef076b6", "a1c3e5d7f9b0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
