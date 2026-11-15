# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Preserve legacy simulated-conversation defaults during pre-column upgrades.

Only backfill when this upgrade invocation added the flag column. Existing
stored flags are authoritative; already-migrated databases cannot distinguish
lost legacy defaults from intentional False values and are left unchanged.

Revision ID: a8f3c1d9e620
Revises: f4b8c2e6a190
Create Date: 2026-11-10 06:20:00.000000
"""

from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "a8f3c1d9e620"
down_revision: str | None = "f4b8c2e6a190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Restore defaults only for seeds known to predate the flag column.

    Raises:
        RuntimeError: When the migration environment supplies no revision provenance.
    """
    applied = op.get_context().opts.get("pyrit_applied_revisions")
    if not isinstance(applied, set):
        raise RuntimeError("Seed flag backfill requires revision provenance from the PyRIT Alembic environment")
    if "e7a1b3c5d9f0" not in applied:
        return
    seeds = sa.table(
        "SeedPromptEntries",
        sa.column("seed_type", sa.String()),
        sa.column("is_general_technique", sa.Boolean()),
    )
    op.execute(
        seeds.update()
        .where(seeds.c.seed_type == op.inline_literal("simulated_conversation", type_=sa.String()))
        .values(is_general_technique=op.inline_literal(True, type_=sa.Boolean()))
    )


def downgrade() -> None:
    """Keep retained seed values when removing this data-only revision."""
