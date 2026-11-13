# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge finding decomposition and main.

Revision ID: 7aa74745cc9b
Revises: 7e1c3a5b9d20, a3f7c9e2b641
Create Date: 2026-11-12 18:22:28.188122
"""

from collections.abc import Sequence

revision: str = "7aa74745cc9b"
down_revision: str | Sequence[str] | None = ("7e1c3a5b9d20", "a3f7c9e2b641")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
