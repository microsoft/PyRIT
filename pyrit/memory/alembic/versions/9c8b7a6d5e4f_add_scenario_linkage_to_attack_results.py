# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Add scenario_result_id FK + scenario_data JSON to AttackResultEntries; drop
ScenarioResultEntries.error_attack_result_ids_json; backfill linkage from the
existing attack_results_json manifest.

Revision ID: 9c8b7a6d5e4f
Revises: 7a1b2c3d4e5f
Create Date: 2026-05-18 15:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003
from typing import Any

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "9c8b7a6d5e4f"
down_revision: str | None = "7a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    # AttackResultEntries: scenario linkage columns.
    op.add_column(
        "AttackResultEntries",
        sa.Column("scenario_result_id", CustomUUID(), nullable=True),
    )
    op.add_column(
        "AttackResultEntries",
        sa.Column("scenario_data", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_AttackResultEntries_scenario_result_id",
        "AttackResultEntries",
        ["scenario_result_id"],
    )

    # FK with ON DELETE SET NULL: deleting a scenario nulls the FK on its
    # AttackResults; scenario_data is retained as historical provenance.
    # Use a batch operation for SQLite portability (no plain ALTER TABLE ADD FK).
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.create_foreign_key(
            "fk_attack_results_scenario",
            "ScenarioResultEntries",
            ["scenario_result_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ScenarioResultEntries: drop the not-yet-released error_attack_result_ids_json.
    # Error AttackResults are now linkable via the new FK; the per-scenario
    # manifest column is no longer used. Wrapped in a batch op for SQLite.
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_column("error_attack_result_ids_json")

    # Backfill scenario linkage from the existing attack_results_json manifest.
    _backfill_scenario_linkage()


def downgrade() -> None:
    """Revert this schema upgrade."""
    # Re-add error_attack_result_ids_json on ScenarioResultEntries.
    op.add_column(
        "ScenarioResultEntries",
        sa.Column("error_attack_result_ids_json", sa.Unicode(), nullable=True),
    )

    # Drop FK + columns from AttackResultEntries.
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_constraint("fk_attack_results_scenario", type_="foreignkey")

    op.drop_index("ix_AttackResultEntries_scenario_result_id", table_name="AttackResultEntries")
    op.drop_column("AttackResultEntries", "scenario_data")
    op.drop_column("AttackResultEntries", "scenario_result_id")


def _backfill_scenario_linkage() -> None:
    """
    Walk every ScenarioResultEntry and copy its attack_results_json manifest
    into the new FK + scenario_data columns on AttackResultEntries.

    Idempotent: the ``WHERE scenario_result_id IS NULL`` guard prevents
    clobbering rows that were already linked (e.g. by a re-run of the
    migration, or by code that ran after the schema change but before this
    backfill). ``conversation_id`` is logically unique per AttackResult but is
    not DB-enforced, so the guard is purely defensive and a WARNING is logged
    if any duplicate match is observed in the wild.
    """
    bind = op.get_bind()

    scenarios = bind.execute(sa.text('SELECT id, attack_results_json FROM "ScenarioResultEntries"')).fetchall()

    update_stmt = sa.text(
        'UPDATE "AttackResultEntries" '
        "SET scenario_result_id = :sid, scenario_data = :sdata "
        "WHERE conversation_id = :cid AND scenario_result_id IS NULL"
    )

    total_updates = 0
    duplicate_warnings = 0

    for row in scenarios:
        scenario_id = row[0]
        manifest_json = row[1]
        if not manifest_json:
            continue
        try:
            manifest: dict[str, Any] = json.loads(manifest_json)
        except (TypeError, ValueError):
            logger.warning(f"Skipping scenario {scenario_id}: attack_results_json is not valid JSON")
            continue

        for atomic_attack_name, conversation_ids in manifest.items():
            if not isinstance(conversation_ids, list):
                continue
            for objective_index, conversation_id in enumerate(conversation_ids):
                if not isinstance(conversation_id, str):
                    continue
                # Check for duplicate conversation_id matches (data anomaly).
                match_count = bind.execute(
                    sa.text(
                        'SELECT COUNT(*) FROM "AttackResultEntries" '
                        "WHERE conversation_id = :cid AND scenario_result_id IS NULL"
                    ),
                    {"cid": conversation_id},
                ).scalar()
                if isinstance(match_count, int) and match_count > 1:
                    duplicate_warnings += 1
                    logger.warning(
                        f"Backfill: conversation_id {conversation_id!r} matches {match_count} "
                        f"unlinked AttackResultEntries rows; conversation_id should be unique. "
                        f"All matching rows will be linked to scenario {scenario_id}."
                    )

                scenario_data = json.dumps(
                    {"atomic_attack_name": atomic_attack_name, "objective_index": objective_index}
                )
                result = bind.execute(
                    update_stmt,
                    {
                        "sid": str(scenario_id),
                        "sdata": scenario_data,
                        "cid": conversation_id,
                    },
                )
                total_updates += result.rowcount or 0

    if total_updates or duplicate_warnings:
        logger.info(
            f"Scenario linkage backfill: linked {total_updates} AttackResultEntries row(s); "
            f"{duplicate_warnings} duplicate-conversation_id warning(s)."
        )
