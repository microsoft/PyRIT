# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist attack identifier graphs as content-addressed rows.

Revision ID: d9f2a4b6c8e0
Revises: c8e1f3a5b7d9
Create Date: 2026-07-13 17:00:00.000000
"""

from __future__ import annotations

import logging
from collections.abc import Sequence  # noqa: TC003
from typing import Any

import sqlalchemy as sa
from alembic import op

from pyrit.memory.alembic.identifier_backfill import (
    IdentifierGraphInserter,
    load_identifier,
    run_best_effort_backfill,
)

revision: str = "d9f2a4b6c8e0"
down_revision: str | None = "c8e1f3a5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    _create_identifier_tables()
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.add_column(sa.Column("atomic_attack_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_attack_result_entries_atomic_attack_identifier_hash",
            "AtomicAttackIdentifiers",
            ["atomic_attack_identifier_hash"],
            ["hash"],
        )
    bind = op.get_bind()
    run_best_effort_backfill(bind=bind, name="AttackIdentifiers", backfill=_backfill_attack_identifiers)


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_column("atomic_attack_identifier_hash")
    op.drop_table("AtomicAttackSeedIdentifiers")
    op.drop_table("AtomicAttackIdentifiers")
    op.drop_table("AttackTechniqueSeedIdentifiers")
    op.drop_table("AttackTechniqueIdentifiers")
    op.drop_table("AttackResponseConverterIdentifiers")
    op.drop_table("AttackRequestConverterIdentifiers")
    op.drop_table("AttackIdentifiers")
    op.drop_table("SeedIdentifiers")


def _create_identifier_tables() -> None:
    op.create_table(
        "SeedIdentifiers",
        *_common_columns(),
        sa.Column("value", sa.Unicode(), nullable=True),
        sa.Column("value_sha256", sa.String(), nullable=True),
        sa.Column("data_type", sa.String(), nullable=True),
        sa.Column("dataset_name", sa.String(), nullable=True),
        sa.Column("is_general_technique", sa.Boolean(), nullable=True),
    )
    op.create_table(
        "AttackIdentifiers",
        *_common_columns(),
        sa.Column("adversarial_system_prompt", sa.Unicode(), nullable=True),
        sa.Column("adversarial_seed_prompt", sa.Unicode(), nullable=True),
        sa.Column("objective_target_hash", sa.String(64), nullable=True),
        sa.Column("adversarial_chat_hash", sa.String(64), nullable=True),
        sa.Column("objective_scorer_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["objective_target_hash"], ["TargetIdentifiers.hash"]),
        sa.ForeignKeyConstraint(["adversarial_chat_hash"], ["TargetIdentifiers.hash"]),
        sa.ForeignKeyConstraint(["objective_scorer_hash"], ["ScorerIdentifiers.hash"]),
    )
    _create_ordered_edge_table(
        table_name="AttackRequestConverterIdentifiers",
        parent_column="attack_identifier_hash",
        parent_table="AttackIdentifiers",
        child_column="converter_identifier_hash",
        child_table="ConverterIdentifiers",
    )
    _create_ordered_edge_table(
        table_name="AttackResponseConverterIdentifiers",
        parent_column="attack_identifier_hash",
        parent_table="AttackIdentifiers",
        child_column="converter_identifier_hash",
        child_table="ConverterIdentifiers",
    )
    op.create_table(
        "AttackTechniqueIdentifiers",
        *_common_columns(),
        sa.Column("attack_identifier_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["attack_identifier_hash"], ["AttackIdentifiers.hash"]),
    )
    _create_ordered_edge_table(
        table_name="AttackTechniqueSeedIdentifiers",
        parent_column="attack_technique_identifier_hash",
        parent_table="AttackTechniqueIdentifiers",
        child_column="seed_identifier_hash",
        child_table="SeedIdentifiers",
    )
    op.create_table(
        "AtomicAttackIdentifiers",
        *_common_columns(),
        sa.Column("attack_technique_identifier_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["attack_technique_identifier_hash"],
            ["AttackTechniqueIdentifiers.hash"],
        ),
    )
    _create_ordered_edge_table(
        table_name="AtomicAttackSeedIdentifiers",
        parent_column="atomic_attack_identifier_hash",
        parent_table="AtomicAttackIdentifiers",
        child_column="seed_identifier_hash",
        child_table="SeedIdentifiers",
    )


def _common_columns() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
    )


def _create_ordered_edge_table(
    *,
    table_name: str,
    parent_column: str,
    parent_table: str,
    child_column: str,
    child_table: str,
) -> None:
    op.create_table(
        table_name,
        sa.Column(parent_column, sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(child_column, sa.String(64), nullable=False),
        sa.ForeignKeyConstraint([parent_column], [f"{parent_table}.hash"]),
        sa.ForeignKeyConstraint([child_column], [f"{child_table}.hash"]),
        sa.PrimaryKeyConstraint(parent_column, "position"),
    )


def _backfill_attack_identifiers() -> None:
    """Backfill attack identifier graphs and result links from retained JSON."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text(
            'SELECT id, atomic_attack_identifier FROM "AttackResultEntries" '
            "WHERE atomic_attack_identifier IS NOT NULL"
        )
    ).fetchall()
    inserter = IdentifierGraphInserter(bind=bind)
    update_stmt = sa.text(
        'UPDATE "AttackResultEntries" SET atomic_attack_identifier_hash = :hash WHERE id = :id'
    )

    skipped = 0
    for result_id, raw_identifier in result_rows:
        identifier = load_identifier(raw_identifier)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_atomic_attack(identifier)
            if identifier_hash:
                bind.execute(update_stmt, {"hash": identifier_hash, "id": result_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"Attack identifier backfill could not reconstruct result {result_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"Attack identifier backfill skipped {skipped} attack result row(s)")
