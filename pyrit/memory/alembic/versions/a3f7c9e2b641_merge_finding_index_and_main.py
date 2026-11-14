# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge the finding-evidence index and current main schema heads.

Revision ID: a3f7c9e2b641
Revises: f8a6c2d4e901, d9e4f6a8b201
Create Date: 2026-11-11 23:00:00.000000
"""

from collections.abc import Sequence

revision: str = "a3f7c9e2b641"
down_revision: str | Sequence[str] | None = ("f8a6c2d4e901", "d9e4f6a8b201")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
