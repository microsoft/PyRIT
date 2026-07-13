# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scenario identifiers as content-addressed rows.

Revision ID: b7d9f1a3c5e7
Revises: a6c8e0f2b4d6
Create Date: 2026-07-13 13:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "b7d9f1a3c5e7"
down_revision: str | None = "a6c8e0f2b4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ScenarioIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("class_module", sa.String(), nullable=False),
        sa.Column("identifier_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("techniques", sa.JSON(), nullable=True),
        sa.Column("datasets", sa.JSON(), nullable=True),
        sa.Column("objective_target_hash", sa.String(64), nullable=True),
        sa.Column("objective_scorer_hash", sa.String(64), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["objective_target_hash"],
            ["TargetIdentifiers.hash"],
            name="fk_scenario_identifiers_objective_target_hash",
        ),
        sa.ForeignKeyConstraint(
            ["objective_scorer_hash"],
            ["ScorerIdentifiers.hash"],
            name="fk_scenario_identifiers_objective_scorer_hash",
        ),
    )
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.add_column(sa.Column("scenario_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenario_result_entries_scenario_identifier_hash",
            "ScenarioIdentifiers",
            ["scenario_identifier_hash"],
            ["hash"],
        )

    _backfill_scenario_identifiers()


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_column("scenario_identifier_hash")
    op.drop_table("ScenarioIdentifiers")


def _backfill_scenario_identifiers() -> None:
    """Backfill scenario rows and result foreign keys from the retained JSON column."""
    from pyrit.models import ScenarioIdentifier, ScorerIdentifier, TargetIdentifier

    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text('SELECT id, scenario_identifier FROM "ScenarioResultEntries" WHERE scenario_identifier IS NOT NULL')
    ).fetchall()
    existing_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "ScenarioIdentifiers"')).fetchall()}
    target_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "TargetIdentifiers"')).fetchall()}
    scorer_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "ScorerIdentifiers"')).fetchall()}
    target_insert = sa.text(
        'INSERT INTO "TargetIdentifiers" '
        "(hash, class_name, class_module, identifier_json, endpoint, model_name, underlying_model_name, "
        "temperature, top_p, max_requests_per_minute, supported_auth_modes, pyrit_version) "
        "VALUES (:hash, :class_name, :class_module, :identifier_json, :endpoint, :model_name, "
        ":underlying_model_name, :temperature, :top_p, :max_requests_per_minute, "
        ":supported_auth_modes, :pyrit_version)"
    )
    target_edge_insert = sa.text(
        'INSERT INTO "TargetIdentifierChildren" (parent_hash, position, child_hash) '
        "VALUES (:parent_hash, :position, :child_hash)"
    )
    scorer_insert = sa.text(
        'INSERT INTO "ScorerIdentifiers" '
        "(hash, class_name, class_module, identifier_json, scorer_type, score_aggregator, "
        "prompt_target_hash, pyrit_version) "
        "VALUES (:hash, :class_name, :class_module, :identifier_json, :scorer_type, "
        ":score_aggregator, :prompt_target_hash, :pyrit_version)"
    )
    scorer_edge_insert = sa.text(
        'INSERT INTO "ScorerIdentifierChildren" (parent_hash, position, child_hash) '
        "VALUES (:parent_hash, :position, :child_hash)"
    )
    insert_stmt = sa.text(
        'INSERT INTO "ScenarioIdentifiers" '
        "(hash, class_name, class_module, identifier_json, version, techniques, datasets, "
        "objective_target_hash, objective_scorer_hash, pyrit_version) "
        "VALUES (:hash, :class_name, :class_module, :identifier_json, :version, :techniques, :datasets, "
        ":objective_target_hash, :objective_scorer_hash, :pyrit_version)"
    )
    update_stmt = sa.text('UPDATE "ScenarioResultEntries" SET scenario_identifier_hash = :hash WHERE id = :id')

    def _insert_target(identifier: TargetIdentifier) -> None:
        if identifier.hash in target_hashes:
            return
        for child in identifier.targets:
            _insert_target(child)
        bind.execute(
            target_insert,
            {
                "hash": identifier.hash,
                "class_name": identifier.class_name,
                "class_module": identifier.class_module,
                "identifier_json": json.dumps(identifier.model_dump(), sort_keys=True),
                "endpoint": identifier.endpoint,
                "model_name": identifier.model_name,
                "underlying_model_name": identifier.underlying_model_name,
                "temperature": identifier.temperature,
                "top_p": identifier.top_p,
                "max_requests_per_minute": identifier.max_requests_per_minute,
                "supported_auth_modes": (
                    json.dumps(identifier.supported_auth_modes) if identifier.supported_auth_modes is not None else None
                ),
                "pyrit_version": identifier.pyrit_version,
            },
        )
        target_hashes.add(identifier.hash)
        for position, child in enumerate(identifier.targets):
            bind.execute(
                target_edge_insert,
                {"parent_hash": identifier.hash, "position": position, "child_hash": child.hash},
            )

    def _insert_scorer(identifier: ScorerIdentifier) -> None:
        if identifier.hash in scorer_hashes:
            return
        if identifier.prompt_target is not None:
            _insert_target(identifier.prompt_target)
        for child in identifier.sub_scorers:
            _insert_scorer(child)
        bind.execute(
            scorer_insert,
            {
                "hash": identifier.hash,
                "class_name": identifier.class_name,
                "class_module": identifier.class_module,
                "identifier_json": json.dumps(identifier.model_dump(), sort_keys=True),
                "scorer_type": identifier.scorer_type,
                "score_aggregator": identifier.score_aggregator,
                "prompt_target_hash": identifier.prompt_target.hash if identifier.prompt_target is not None else None,
                "pyrit_version": identifier.pyrit_version,
            },
        )
        scorer_hashes.add(identifier.hash)
        for position, child in enumerate(identifier.sub_scorers):
            bind.execute(
                scorer_edge_insert,
                {"parent_hash": identifier.hash, "position": position, "child_hash": child.hash},
            )

    skipped = 0
    for result_id, raw_scenario in result_rows:
        stored = json.loads(raw_scenario) if isinstance(raw_scenario, str) else raw_scenario
        if not stored:
            continue
        try:
            identifier = ScenarioIdentifier.model_validate(stored)
            if identifier.objective_target is not None:
                _insert_target(identifier.objective_target)
            if identifier.objective_scorer is not None:
                _insert_scorer(identifier.objective_scorer)
            if identifier.hash not in existing_hashes:
                bind.execute(
                    insert_stmt,
                    {
                        "hash": identifier.hash,
                        "class_name": identifier.class_name,
                        "class_module": identifier.class_module,
                        "identifier_json": json.dumps(identifier.model_dump(), sort_keys=True),
                        "version": identifier.version,
                        "techniques": json.dumps(identifier.techniques) if identifier.techniques is not None else None,
                        "datasets": json.dumps(identifier.datasets) if identifier.datasets is not None else None,
                        "objective_target_hash": (
                            identifier.objective_target.hash if identifier.objective_target is not None else None
                        ),
                        "objective_scorer_hash": (
                            identifier.objective_scorer.hash if identifier.objective_scorer is not None else None
                        ),
                        "pyrit_version": identifier.pyrit_version,
                    },
                )
                existing_hashes.add(identifier.hash)
            bind.execute(update_stmt, {"hash": identifier.hash, "id": result_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScenarioIdentifiers backfill: could not reconstruct scenario for result {result_id}",
                exc_info=True,
            )

    if skipped:
        logger.warning(f"ScenarioIdentifiers backfill skipped {skipped} scenario result row(s)")
