# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Compatibility no-op migration for previously issued revision id.

The ``b2f4c6a8d1e3`` migration now creates ``Conversations.conversation_id`` as a
bounded string (length 36), so this follow-up fix no longer needs to alter
schema state. This revision is intentionally retained to preserve Alembic
history compatibility for databases that are already stamped/applied at
``d4e5f6a7b8c9``.

Revision ID: d4e5f6a7b8c9
Revises: c3d5e7f9a1b2
Create Date: 2026-06-16 12:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema upgrade."""


def downgrade() -> None:
    """Revert this schema upgrade."""
