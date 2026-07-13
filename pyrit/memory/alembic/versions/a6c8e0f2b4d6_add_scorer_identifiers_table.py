# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scorer identifiers as content-addressed rows.

Revision ID: a6c8e0f2b4d6
Revises: e5f7a9c1b3d2
Create Date: 2026-07-13 12:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003

import sqlalchemy as sa
from alembic import op

revision: str = "a6c8e0f2b4d6"
down_revision: str | None = "e5f7a9c1b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ScorerIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("class_module", sa.String(), nullable=False),
        sa.Column("identifier_json", sa.JSON(), nullable=False),
        sa.Column("scorer_type", sa.String(), nullable=True),
        sa.Column("score_aggregator", sa.String(), nullable=True),
        sa.Column("prompt_target_hash", sa.String(64), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["prompt_target_hash"], ["TargetIdentifiers.hash"], name="fk_scorer_identifiers_prompt_target_hash"
        ),
    )
    op.create_table(
        "ScorerIdentifierChildren",
        sa.Column("parent_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("child_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("parent_hash", "position"),
        sa.ForeignKeyConstraint(
            ["parent_hash"], ["ScorerIdentifiers.hash"], name="fk_scorer_identifier_children_parent_hash"
        ),
        sa.ForeignKeyConstraint(
            ["child_hash"], ["ScorerIdentifiers.hash"], name="fk_scorer_identifier_children_child_hash"
        ),
    )
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("scorer_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_score_entries_scorer_identifier_hash",
            "ScorerIdentifiers",
            ["scorer_identifier_hash"],
            ["hash"],
        )

    _backfill_scorer_identifiers()


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("scorer_identifier_hash")
    op.drop_table("ScorerIdentifierChildren")
    op.drop_table("ScorerIdentifiers")


def _backfill_scorer_identifiers() -> None:
    """Backfill scorer rows and score foreign keys from the retained JSON column."""
    from pyrit.models import ScorerIdentifier, TargetIdentifier

    bind = op.get_bind()
    score_rows = bind.execute(
        sa.text('SELECT id, scorer_class_identifier FROM "ScoreEntries" WHERE scorer_class_identifier IS NOT NULL')
    ).fetchall()
    scorer_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "ScorerIdentifiers"')).fetchall()}
    target_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "TargetIdentifiers"')).fetchall()}

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
    score_update = sa.text('UPDATE "ScoreEntries" SET scorer_identifier_hash = :hash WHERE id = :id')

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
    for score_id, raw_scorer in score_rows:
        stored = json.loads(raw_scorer) if isinstance(raw_scorer, str) else raw_scorer
        if not stored:
            continue
        try:
            identifier = ScorerIdentifier.model_validate(stored)
            _insert_scorer(identifier)
            bind.execute(score_update, {"hash": identifier.hash, "id": score_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScorerIdentifiers backfill: could not reconstruct scorer for score {score_id}",
                exc_info=True,
            )

    if skipped:
        logger.warning(f"ScorerIdentifiers backfill skipped {skipped} score row(s)")
