# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist component identifiers as content-addressed rows.

Creates the normalized identifier tables, their graph edges, and nullable links
from existing domain tables. Retained identifier JSON is backfilled on a
best-effort basis and remains available when a legacy value cannot be linked.

Revision ID: e5f7a9c1b3d2
Revises: d4e6f8a0b2c4
Create Date: 2026-07-10 12:00:00.000000
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.types import TypeDecorator, Uuid

from pyrit.memory.alembic.identifier_backfill import (
    IdentifierGraphInserter,
    load_identifier,
    load_identifier_list,
    run_best_effort_backfill,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

# revision identifiers, used by Alembic.
revision: str = "e5f7a9c1b3d2"
down_revision: str | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger(__name__)


class _CustomUUID(TypeDecorator[uuid.UUID]):
    """Frozen UUID type matching ``PromptMemoryEntries.id`` across dialects."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(CHAR(36))
        return dialect.type_descriptor(Uuid())

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        return str(value) if value is not None else None

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "TargetIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("underlying_model_name", sa.String(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("top_p", sa.Float(), nullable=True),
        sa.Column("max_requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("supported_auth_modes", sa.JSON(), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
    )

    # Self-referential pivot mapping a multi-target to its inner target identifiers.
    # Both endpoints are content hashes into TargetIdentifiers; ``position`` preserves
    # the parent's ``targets`` list order. Named FK constraints for SQL Server / batch
    # portability.
    op.create_table(
        "TargetIdentifierChildren",
        sa.Column("parent_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("child_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("parent_hash", "position"),
        sa.ForeignKeyConstraint(
            ["parent_hash"], ["TargetIdentifiers.hash"], name="fk_target_identifier_children_parent_hash"
        ),
        sa.ForeignKeyConstraint(
            ["child_hash"], ["TargetIdentifiers.hash"], name="fk_target_identifier_children_child_hash"
        ),
    )

    op.create_table(
        "ScorerIdentifiers",
        *_common_columns(),
        sa.Column("scorer_type", sa.String(), nullable=True),
        sa.Column("score_aggregator", sa.String(), nullable=True),
        sa.Column("prompt_target_hash", sa.String(64), nullable=True),
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
    op.create_table(
        "ScenarioIdentifiers",
        *_common_columns(),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("techniques", sa.JSON(), nullable=True),
        sa.Column("datasets", sa.JSON(), nullable=True),
        sa.Column("objective_target_hash", sa.String(64), nullable=True),
        sa.Column("objective_scorer_hash", sa.String(64), nullable=True),
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
    op.create_table(
        "ConverterIdentifiers",
        *_common_columns(),
        sa.Column("supported_input_types", sa.JSON(), nullable=True),
        sa.Column("supported_output_types", sa.JSON(), nullable=True),
        sa.Column("converter_target_hash", sa.String(64), nullable=True),
        sa.Column("sub_converter_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["converter_target_hash"],
            ["TargetIdentifiers.hash"],
            name="fk_converter_identifiers_converter_target_hash",
        ),
        sa.ForeignKeyConstraint(
            ["sub_converter_hash"],
            ["ConverterIdentifiers.hash"],
            name="fk_converter_identifiers_sub_converter_hash",
        ),
    )
    op.create_table(
        "PromptConverterIdentifiers",
        sa.Column("prompt_memory_entry_id", _CustomUUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("converter_identifier_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_memory_entry_id"],
            ["PromptMemoryEntries.id"],
            name="fk_prompt_converter_identifiers_prompt_memory_entry_id",
        ),
        sa.ForeignKeyConstraint(
            ["converter_identifier_hash"],
            ["ConverterIdentifiers.hash"],
            name="fk_prompt_converter_identifiers_converter_identifier_hash",
        ),
        sa.PrimaryKeyConstraint("prompt_memory_entry_id", "position"),
    )
    _create_attack_identifier_tables()

    # Batch op for SQLite portability (no ALTER TABLE ADD FOREIGN KEY on SQLite).
    # The FK constraint must be named explicitly: Alembic batch mode rejects an
    # unnamed constraint.
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.add_column(sa.Column("target_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_conversations_target_identifier_hash",
            "TargetIdentifiers",
            ["target_identifier_hash"],
            ["hash"],
        )

    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.add_column(sa.Column("scorer_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_score_entries_scorer_identifier_hash",
            "ScorerIdentifiers",
            ["scorer_identifier_hash"],
            ["hash"],
        )
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.add_column(sa.Column("scenario_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenario_result_entries_scenario_identifier_hash",
            "ScenarioIdentifiers",
            ["scenario_identifier_hash"],
            ["hash"],
        )
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.add_column(sa.Column("atomic_attack_identifier_hash", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_attack_result_entries_atomic_attack_identifier_hash",
            "AtomicAttackIdentifiers",
            ["atomic_attack_identifier_hash"],
            ["hash"],
        )

    bind = op.get_bind()
    for name, backfill in (
        ("TargetIdentifiers", _backfill_target_identifiers),
        ("ScorerIdentifiers", _backfill_scorer_identifiers),
        ("ScenarioIdentifiers", _backfill_scenario_identifiers),
        ("ConverterIdentifiers", _backfill_converter_identifiers),
        ("AttackIdentifiers", _backfill_attack_identifiers),
    ):
        run_best_effort_backfill(bind=bind, name=name, backfill=backfill)


def downgrade() -> None:
    """Revert this schema upgrade."""
    with op.batch_alter_table("AttackResultEntries") as batch_op:
        batch_op.drop_column("atomic_attack_identifier_hash")
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_column("scenario_identifier_hash")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_column("scorer_identifier_hash")
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.drop_column("target_identifier_hash")

    op.drop_table("AtomicAttackSeedIdentifiers")
    op.drop_table("AtomicAttackIdentifiers")
    op.drop_table("AttackTechniqueSeedIdentifiers")
    op.drop_table("AttackTechniqueIdentifiers")
    op.drop_table("AttackResponseConverterIdentifiers")
    op.drop_table("AttackRequestConverterIdentifiers")
    op.drop_table("AttackIdentifiers")
    op.drop_table("SeedIdentifiers")
    op.drop_table("PromptConverterIdentifiers")
    op.drop_table("ConverterIdentifiers")
    op.drop_table("ScenarioIdentifiers")
    op.drop_table("ScorerIdentifierChildren")
    op.drop_table("ScorerIdentifiers")
    op.drop_table("TargetIdentifierChildren")
    op.drop_table("TargetIdentifiers")


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


def _create_attack_identifier_tables() -> None:
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
        sa.ForeignKeyConstraint(["attack_technique_identifier_hash"], ["AttackTechniqueIdentifiers.hash"]),
    )
    _create_ordered_edge_table(
        table_name="AtomicAttackSeedIdentifiers",
        parent_column="atomic_attack_identifier_hash",
        parent_table="AtomicAttackIdentifiers",
        child_column="seed_identifier_hash",
        child_table="SeedIdentifiers",
    )


def _backfill_target_identifiers() -> None:
    """
    Populate ``TargetIdentifiers`` / ``TargetIdentifierChildren`` and set
    ``Conversations.target_identifier_hash``.

    For every ``Conversations`` row with a non-null ``target_identifier`` JSON,
    reconstruct the ``TargetIdentifier`` (recomputing its content hash), insert the
    deduped ``TargetIdentifiers`` row if absent -- recursing into any inner
    ``targets`` first so the child edge foreign keys resolve -- record the
    ``parent_hash -> child_hash`` edges, and point the conversation's
    ``target_identifier_hash`` at the top-level row. Idempotent: hashes already present
    are not re-inserted. Rows whose stored target cannot be reconstructed are logged and
    skipped rather than aborting the upgrade.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text('SELECT conversation_id, target_identifier FROM "Conversations" WHERE target_identifier IS NOT NULL')
    ).fetchall()

    update_stmt = sa.text('UPDATE "Conversations" SET target_identifier_hash = :hash WHERE conversation_id = :cid')
    inserter = IdentifierGraphInserter(bind=bind)
    linked = 0
    skipped = 0
    for conversation_id, raw_target in rows:
        identifier = load_identifier(raw_target)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_target(identifier)
            if identifier_hash:
                bind.execute(update_stmt, {"hash": identifier_hash, "cid": conversation_id})
                linked += 1
        except Exception:
            skipped += 1
            logger.warning(f"TargetIdentifiers backfill skipped conversation {conversation_id!r}", exc_info=True)

    if linked or skipped:
        logger.info(f"TargetIdentifiers backfill linked {linked} conversation(s); skipped {skipped}.")


def _backfill_scorer_identifiers() -> None:
    """Backfill scorer rows and score foreign keys from retained JSON."""
    bind = op.get_bind()
    score_rows = bind.execute(
        sa.text('SELECT id, scorer_class_identifier FROM "ScoreEntries" WHERE scorer_class_identifier IS NOT NULL')
    ).fetchall()
    score_update = sa.text('UPDATE "ScoreEntries" SET scorer_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for score_id, raw_scorer in score_rows:
        identifier = load_identifier(raw_scorer)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_scorer(identifier)
            if identifier_hash:
                bind.execute(score_update, {"hash": identifier_hash, "id": score_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScorerIdentifiers backfill: could not reconstruct scorer for score {score_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"ScorerIdentifiers backfill skipped {skipped} score row(s)")


def _backfill_scenario_identifiers() -> None:
    """Backfill scenario rows and result foreign keys from retained JSON."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text('SELECT id, scenario_identifier FROM "ScenarioResultEntries" WHERE scenario_identifier IS NOT NULL')
    ).fetchall()
    update_stmt = sa.text('UPDATE "ScenarioResultEntries" SET scenario_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for result_id, raw_scenario in result_rows:
        identifier = load_identifier(raw_scenario)
        if identifier is None:
            skipped += 1
            continue
        try:
            identifier_hash = inserter.insert_scenario(identifier)
            if identifier_hash:
                bind.execute(update_stmt, {"hash": identifier_hash, "id": result_id})
        except Exception:
            skipped += 1
            logger.warning(
                f"ScenarioIdentifiers backfill: could not reconstruct scenario for result {result_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"ScenarioIdentifiers backfill skipped {skipped} scenario result row(s)")


def _backfill_converter_identifiers() -> None:
    """Materialize converter graphs and prompt associations from retained JSON."""
    bind = op.get_bind()
    prompt_rows = bind.execute(
        sa.text(
            'SELECT id, converter_identifiers, pyrit_version FROM "PromptMemoryEntries" '
            "WHERE converter_identifiers IS NOT NULL"
        )
    ).fetchall()
    link_insert = sa.text(
        'INSERT INTO "PromptConverterIdentifiers" '
        "(prompt_memory_entry_id, position, converter_identifier_hash) "
        "VALUES (:prompt_memory_entry_id, :position, :converter_identifier_hash)"
    )
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for prompt_id, stored_identifiers, pyrit_version in prompt_rows:
        try:
            for position, identifier in enumerate(load_identifier_list(stored_identifiers)):
                if identifier.get("pyrit_version") is None:
                    identifier = {**identifier, "pyrit_version": pyrit_version}
                identifier_hash = inserter.insert_converter(identifier)
                if identifier_hash:
                    bind.execute(
                        link_insert,
                        {
                            "prompt_memory_entry_id": prompt_id,
                            "position": position,
                            "converter_identifier_hash": identifier_hash,
                        },
                    )
        except Exception:
            skipped += 1
            logger.warning(
                f"ConverterIdentifiers backfill: could not reconstruct converters for prompt {prompt_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"ConverterIdentifiers backfill skipped {skipped} prompt row(s)")


def _backfill_attack_identifiers() -> None:
    """Backfill attack identifier graphs and result links from retained JSON."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text(
            'SELECT id, atomic_attack_identifier FROM "AttackResultEntries" WHERE atomic_attack_identifier IS NOT NULL'
        )
    ).fetchall()
    inserter = IdentifierGraphInserter(bind=bind)
    update_stmt = sa.text('UPDATE "AttackResultEntries" SET atomic_attack_identifier_hash = :hash WHERE id = :id')
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
