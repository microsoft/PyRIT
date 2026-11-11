# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the score message-piece index and seed decomposition heads.

Revision ID: 7f2c8a4d6e91
Revises: c4d1e8b72a90, 5d7f9b1c3e2a
Create Date: 2026-11-10 23:40:00.000000
"""

from collections.abc import Sequence

revision: str = "7f2c8a4d6e91"
down_revision: str | Sequence[str] | None = ("c4d1e8b72a90", "5d7f9b1c3e2a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
