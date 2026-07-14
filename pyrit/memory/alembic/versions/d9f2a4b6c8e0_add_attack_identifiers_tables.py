# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist attack identifier graphs as content-addressed rows.

Revision ID: d9f2a4b6c8e0
Revises: c8e1f3a5b7d9
Create Date: 2026-07-13 17:00:00.000000
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence  # noqa: TC003
from typing import Any

import sqlalchemy as sa
from alembic import op

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
    _backfill_attack_identifiers()


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
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("class_module", sa.String(), nullable=False),
        sa.Column("identifier_json", sa.JSON(), nullable=False),
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
    from pyrit.models import AtomicAttackIdentifier

    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text(
            'SELECT id, atomic_attack_identifier FROM "AttackResultEntries" '
            "WHERE atomic_attack_identifier IS NOT NULL"
        )
    ).fetchall()
    inserter = _AttackIdentifierGraphInserter(bind=bind)
    update_stmt = sa.text(
        'UPDATE "AttackResultEntries" SET atomic_attack_identifier_hash = :hash WHERE id = :id'
    )

    skipped = 0
    for result_id, raw_identifier in result_rows:
        try:
            stored = json.loads(raw_identifier) if isinstance(raw_identifier, str) else raw_identifier
            identifier = AtomicAttackIdentifier.model_validate(stored)
            inserter.insert_atomic_attack(identifier)
            bind.execute(update_stmt, {"hash": identifier.hash, "id": result_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"Attack identifier backfill could not reconstruct result {result_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"Attack identifier backfill skipped {skipped} attack result row(s)")


class _AttackIdentifierGraphInserter:
    """Insert a normalized attack identifier graph during migration backfill."""

    def __init__(self, *, bind: Any) -> None:
        self._bind = bind
        self._hashes = {
            table: set(bind.execute(sa.text(f'SELECT hash FROM "{table}"')).scalars())
            for table in (
                "TargetIdentifiers",
                "ScorerIdentifiers",
                "ConverterIdentifiers",
                "SeedIdentifiers",
                "AttackIdentifiers",
                "AttackTechniqueIdentifiers",
                "AtomicAttackIdentifiers",
            )
        }

    def insert_atomic_attack(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["AtomicAttackIdentifiers"]:
            return
        if identifier.attack_technique is not None:
            self._insert_attack_technique(identifier.attack_technique)
        for seed in identifier.seed_identifiers:
            self._insert_seed(seed)
        self._insert_identifier(
            table="AtomicAttackIdentifiers",
            identifier=identifier,
            extra={
                "attack_technique_identifier_hash": (
                    identifier.attack_technique.hash if identifier.attack_technique is not None else None
                )
            },
        )
        self._insert_edges(
            table="AtomicAttackSeedIdentifiers",
            parent_column="atomic_attack_identifier_hash",
            parent_hash=identifier.hash,
            child_column="seed_identifier_hash",
            children=identifier.seed_identifiers,
        )

    def _insert_attack_technique(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["AttackTechniqueIdentifiers"]:
            return
        if identifier.attack is not None:
            self._insert_attack(identifier.attack)
        for seed in identifier.technique_seeds:
            self._insert_seed(seed)
        self._insert_identifier(
            table="AttackTechniqueIdentifiers",
            identifier=identifier,
            extra={"attack_identifier_hash": identifier.attack.hash if identifier.attack is not None else None},
        )
        self._insert_edges(
            table="AttackTechniqueSeedIdentifiers",
            parent_column="attack_technique_identifier_hash",
            parent_hash=identifier.hash,
            child_column="seed_identifier_hash",
            children=identifier.technique_seeds,
        )

    def _insert_attack(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["AttackIdentifiers"]:
            return
        for target in (identifier.objective_target, identifier.adversarial_chat):
            if target is not None:
                self._insert_target(target)
        if identifier.objective_scorer is not None:
            self._insert_scorer(identifier.objective_scorer)
        for converter in [*identifier.request_converters, *identifier.response_converters]:
            self._insert_converter(converter)
        self._insert_identifier(
            table="AttackIdentifiers",
            identifier=identifier,
            extra={
                "adversarial_system_prompt": identifier.adversarial_system_prompt,
                "adversarial_seed_prompt": identifier.adversarial_seed_prompt,
                "objective_target_hash": (
                    identifier.objective_target.hash if identifier.objective_target is not None else None
                ),
                "adversarial_chat_hash": (
                    identifier.adversarial_chat.hash if identifier.adversarial_chat is not None else None
                ),
                "objective_scorer_hash": (
                    identifier.objective_scorer.hash if identifier.objective_scorer is not None else None
                ),
            },
        )
        self._insert_edges(
            table="AttackRequestConverterIdentifiers",
            parent_column="attack_identifier_hash",
            parent_hash=identifier.hash,
            child_column="converter_identifier_hash",
            children=identifier.request_converters,
        )
        self._insert_edges(
            table="AttackResponseConverterIdentifiers",
            parent_column="attack_identifier_hash",
            parent_hash=identifier.hash,
            child_column="converter_identifier_hash",
            children=identifier.response_converters,
        )

    def _insert_seed(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["SeedIdentifiers"]:
            return
        self._insert_identifier(
            table="SeedIdentifiers",
            identifier=identifier,
            extra=identifier.promoted_scalar_values(),
        )

    def _insert_target(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["TargetIdentifiers"]:
            return
        for child in identifier.targets:
            self._insert_target(child)
        self._insert_identifier(
            table="TargetIdentifiers",
            identifier=identifier,
            extra=identifier.promoted_scalar_values(),
        )
        self._insert_edges(
            table="TargetIdentifierChildren",
            parent_column="parent_hash",
            parent_hash=identifier.hash,
            child_column="child_hash",
            children=identifier.targets,
        )

    def _insert_scorer(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["ScorerIdentifiers"]:
            return
        if identifier.prompt_target is not None:
            self._insert_target(identifier.prompt_target)
        for child in identifier.sub_scorers:
            self._insert_scorer(child)
        self._insert_identifier(
            table="ScorerIdentifiers",
            identifier=identifier,
            extra={
                **identifier.promoted_scalar_values(),
                "prompt_target_hash": (
                    identifier.prompt_target.hash if identifier.prompt_target is not None else None
                ),
            },
        )
        self._insert_edges(
            table="ScorerIdentifierChildren",
            parent_column="parent_hash",
            parent_hash=identifier.hash,
            child_column="child_hash",
            children=identifier.sub_scorers,
        )

    def _insert_converter(self, identifier: Any) -> None:
        if identifier.hash in self._hashes["ConverterIdentifiers"]:
            return
        if identifier.converter_target is not None:
            self._insert_target(identifier.converter_target)
        if identifier.sub_converter is not None:
            self._insert_converter(identifier.sub_converter)
        self._insert_identifier(
            table="ConverterIdentifiers",
            identifier=identifier,
            extra={
                **identifier.promoted_scalar_values(),
                "converter_target_hash": (
                    identifier.converter_target.hash if identifier.converter_target is not None else None
                ),
                "sub_converter_hash": (
                    identifier.sub_converter.hash if identifier.sub_converter is not None else None
                ),
            },
        )

    def _insert_identifier(self, *, table: str, identifier: Any, extra: dict[str, Any]) -> None:
        columns = ["hash", "class_name", "class_module", "identifier_json", *extra, "pyrit_version"]
        placeholders = [f":{column}" for column in columns]
        values = {
            "hash": identifier.hash,
            "class_name": identifier.class_name,
            "class_module": identifier.class_module,
            "identifier_json": json.dumps(identifier.model_dump(), sort_keys=True),
            "pyrit_version": identifier.pyrit_version,
            **{
                name: json.dumps(value) if isinstance(value, (list, dict)) else value
                for name, value in extra.items()
            },
        }
        self._bind.execute(
            sa.text(f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join(placeholders)})'),
            values,
        )
        self._hashes[table].add(identifier.hash)

    def _insert_edges(
        self,
        *,
        table: str,
        parent_column: str,
        parent_hash: str,
        child_column: str,
        children: Sequence[Any],
    ) -> None:
        statement = sa.text(
            f'INSERT INTO "{table}" ({parent_column}, position, {child_column}) '
            f'VALUES (:parent_hash, :position, :child_hash)'
        )
        for position, child in enumerate(children):
            self._bind.execute(
                statement,
                {"parent_hash": parent_hash, "position": position, "child_hash": child.hash},
            )
