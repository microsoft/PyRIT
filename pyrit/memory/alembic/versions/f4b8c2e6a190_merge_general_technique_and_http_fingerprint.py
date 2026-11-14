# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge general technique storage and HTTP request fingerprints.

Revision ID: f4b8c2e6a190
Revises: d6de1045cca4, e7a1b3c5d9f0
Create Date: 2026-11-10 05:45:00.000000
"""

from collections.abc import Sequence

revision: str = "f4b8c2e6a190"
down_revision: str | Sequence[str] | None = ("d6de1045cca4", "e7a1b3c5d9f0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
