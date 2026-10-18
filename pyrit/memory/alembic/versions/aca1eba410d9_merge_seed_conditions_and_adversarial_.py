# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge seed conditions and adversarial prompt template.

Revision ID: aca1eba410d9
Revises: 9b2d4f6a8c0e, fcecd0617e61
Create Date: 2026-09-24 16:40:59.892388
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "aca1eba410d9"
down_revision: str | Sequence[str] | None = ("9b2d4f6a8c0e", "fcecd0617e61")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
