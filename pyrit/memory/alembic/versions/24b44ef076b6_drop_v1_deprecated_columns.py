# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Drop columns whose compatibility windows end at the v1 boundary.

``ScoreEntries.objective`` replaces ``task``. Scenario attack results are
linked through ``AttackResultEntries.attribution_parent_id`` and grouped by
``attribution_data.parent_collection``, replacing the serialized manifest.
Message labels now live on ``AttackResultEntries`` rather than
``PromptMemoryEntries``.

Revision ID: 24b44ef076b6
Revises: e5f7a9c1b3d2
Create Date: 2026-07-14 14:47:47.419485
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = "24b44ef076b6"
down_revision: str | None = "e5f7a9c1b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("task")
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_column("attack_results_json")
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.drop_column("labels")


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.add_column(sa.Column("labels", sqlite.JSON(), nullable=False))

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("task", sa.String(), nullable=True))
    op.execute(sa.text('UPDATE "ScoreEntries" SET task = objective'))

    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.add_column(sa.Column("attack_results_json", sa.Unicode(), nullable=True))
    _backfill_attack_results_manifest()
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.alter_column(
            "attack_results_json",
            existing_type=sa.Unicode(),
            existing_nullable=True,
            nullable=False,
        )


def _backfill_attack_results_manifest() -> None:
    """Reconstruct legacy scenario manifests from AttackResult attribution."""
    bind = op.get_bind()
    scenario_ids = [str(row[0]) for row in bind.execute(sa.text('SELECT id FROM "ScenarioResultEntries"')).fetchall()]
    manifests: dict[str, dict[str, list[str]]] = {scenario_id: {} for scenario_id in scenario_ids}

    rows = bind.execute(
        sa.text(
            "SELECT attribution_parent_id, conversation_id, attribution_data "
            'FROM "AttackResultEntries" '
            "WHERE attribution_parent_id IS NOT NULL "
            "ORDER BY timestamp, id"
        )
    ).fetchall()

    for parent_id, conversation_id, raw_attribution_data in rows:
        scenario_id = str(parent_id)
        manifest = manifests.get(scenario_id)
        if manifest is None:
            logger.warning(f"Skipping AttackResult attribution for unknown scenario {scenario_id}")
            continue

        attribution_data = _parse_attribution_data(raw_attribution_data)
        parent_collection = attribution_data.get("parent_collection") if attribution_data else None
        if not isinstance(parent_collection, str) or not parent_collection:
            logger.warning(
                f"Skipping AttackResult {conversation_id!r} while restoring attack_results_json: "
                "attribution_data.parent_collection is missing or invalid"
            )
            continue

        manifest.setdefault(parent_collection, []).append(str(conversation_id))

    update_stmt = sa.text('UPDATE "ScenarioResultEntries" SET attack_results_json = :manifest WHERE id = :scenario_id')
    for scenario_id, manifest in manifests.items():
        bind.execute(
            update_stmt,
            {"scenario_id": scenario_id, "manifest": json.dumps(manifest)},
        )


def _parse_attribution_data(value: Any) -> dict[str, Any] | None:
    """
    Normalize JSON values returned by different database drivers.

    Returns:
        The parsed attribution dictionary, or None for an invalid value.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
