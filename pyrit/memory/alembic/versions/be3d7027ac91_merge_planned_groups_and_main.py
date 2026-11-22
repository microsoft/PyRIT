# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Merge planned seed groups and main, and enforce their ownership on SQLite.

Revision ID: be3d7027ac91
Revises: c7d1e9a3b5f2, 7aa74745cc9b
Create Date: 2026-11-17 23:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "be3d7027ac91"
down_revision: str | Sequence[str] | None = ("c7d1e9a3b5f2", "7aa74745cc9b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cascade planned-group deletion even with SQLite foreign keys disabled."""
    if op.get_context().dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER "trg_delete_scenario_plan_seed_groups"
            AFTER DELETE ON "ScenarioResultEntries"
            BEGIN
                DELETE FROM "ScenarioPlanSeedGroupEntries" WHERE scenario_result_id = OLD.id;
            END
            """
        )


def downgrade() -> None:
    """Drop the SQLite delete trigger."""
    if op.get_context().dialect.name == "sqlite":
        op.execute('DROP TRIGGER "trg_delete_scenario_plan_seed_groups"')
