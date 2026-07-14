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

import json
import logging
import uuid
from collections.abc import Sequence  # noqa: TC003
from functools import partial
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.types import TypeDecorator, Uuid

if TYPE_CHECKING:
    from collections.abc import Callable

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


def run_best_effort_backfill(*, bind: Any, name: str, backfill: Callable[[], None]) -> None:
    """Run a data backfill in a savepoint without blocking the schema upgrade."""
    try:
        with bind.begin_nested():
            backfill()
    except Exception:
        logger.warning(f"{name} backfill failed; leaving new identifier links nullable", exc_info=True)


def _run_best_effort_row(*, bind: Any, description: str, operation: Callable[[], None]) -> bool:
    """
    Run one backfill row in a savepoint and report whether it succeeded.

    Returns:
        bool: Whether the row operation completed successfully.
    """
    try:
        with bind.begin_nested():
            operation()
    except Exception:
        logger.warning(description, exc_info=True)
        return False
    return True


def _insert_identifier_link(
    *,
    bind: Any,
    insert: Callable[[dict[str, Any]], str | None],
    identifier: dict[str, Any],
    update_statement: Any,
    update_values: dict[str, Any],
) -> None:
    """Insert an identifier graph and update its domain-row link."""
    identifier_hash = insert(identifier)
    if not identifier_hash:
        return
    bind.execute(update_statement, {**update_values, "hash": identifier_hash})


def load_identifier(raw_identifier: Any) -> dict[str, Any] | None:
    """
    Load a retained identifier JSON value without importing domain models.

    Returns:
        dict[str, Any] | None: The identifier dictionary when it has a usable hash.
    """
    try:
        value = json.loads(raw_identifier) if isinstance(raw_identifier, str) else raw_identifier
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    identifier_hash = value.get("hash")
    if not isinstance(identifier_hash, str) or len(identifier_hash) != 64:
        return None
    return value


def load_identifier_list(raw_identifiers: Any) -> list[dict[str, Any]]:
    """
    Load the valid identifiers from a retained JSON list.

    Returns:
        list[dict[str, Any]]: Identifier dictionaries carrying usable hashes.
    """
    try:
        values = json.loads(raw_identifiers) if isinstance(raw_identifiers, str) else raw_identifiers
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [identifier for value in values if (identifier := load_identifier(value)) is not None]


class IdentifierGraphInserter:
    """Best-effort inserter for the frozen flat identifier JSON shape."""

    _TABLES = (
        "TargetIdentifiers",
        "ScorerIdentifiers",
        "ConverterIdentifiers",
        "ScenarioIdentifiers",
        "SeedIdentifiers",
        "AttackIdentifiers",
        "AttackTechniqueIdentifiers",
        "AtomicAttackIdentifiers",
    )

    def __init__(self, *, bind: Any) -> None:
        """Initialize the inserter from tables available at this migration revision."""
        self._bind = bind
        table_names = set(sa.inspect(bind).get_table_names())
        self._hashes = {
            table: set(bind.execute(sa.text(f'SELECT hash FROM "{table}"')).scalars())
            for table in self._TABLES
            if table in table_names
        }

    def insert_target(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a target graph.

        Returns:
            str | None: The stored hash when successful.
        """
        children = self._children(identifier, "targets")
        child_hashes = [child_hash for child in children if (child_hash := self.insert_target(child))]
        identifier_hash = self._insert_identifier(
            table="TargetIdentifiers",
            identifier=identifier,
            promoted=(
                "endpoint",
                "model_name",
                "underlying_model_name",
                "temperature",
                "top_p",
                "max_requests_per_minute",
                "supported_auth_modes",
            ),
        )
        if identifier_hash:
            self._insert_edges(
                table="TargetIdentifierChildren",
                parent_column="parent_hash",
                parent_hash=identifier_hash,
                child_column="child_hash",
                child_hashes=child_hashes,
            )
        return identifier_hash

    def insert_scorer(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a scorer graph.

        Returns:
            str | None: The stored hash when successful.
        """
        prompt_target = self._child(identifier, "prompt_target", aliases=("chat_target",))
        prompt_target_hash = self.insert_target(prompt_target) if prompt_target else None
        sub_scorers = self._children(identifier, "sub_scorers", aliases=("scorers",))
        child_hashes = [child_hash for child in sub_scorers if (child_hash := self.insert_scorer(child))]
        identifier_hash = self._insert_identifier(
            table="ScorerIdentifiers",
            identifier=identifier,
            promoted=("scorer_type", "score_aggregator"),
            extra={"prompt_target_hash": prompt_target_hash},
        )
        if identifier_hash:
            self._insert_edges(
                table="ScorerIdentifierChildren",
                parent_column="parent_hash",
                parent_hash=identifier_hash,
                child_column="child_hash",
                child_hashes=child_hashes,
            )
        return identifier_hash

    def insert_converter(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a converter graph.

        Returns:
            str | None: The stored hash when successful.
        """
        converter_target = self._child(identifier, "converter_target")
        sub_converter = self._child(identifier, "sub_converter")
        return self._insert_identifier(
            table="ConverterIdentifiers",
            identifier=identifier,
            promoted=("supported_input_types", "supported_output_types"),
            extra={
                "converter_target_hash": self.insert_target(converter_target) if converter_target else None,
                "sub_converter_hash": self.insert_converter(sub_converter) if sub_converter else None,
            },
        )

    def insert_scenario(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a scenario graph.

        Returns:
            str | None: The stored hash when successful.
        """
        objective_target = self._child(identifier, "objective_target")
        objective_scorer = self._child(identifier, "objective_scorer")
        return self._insert_identifier(
            table="ScenarioIdentifiers",
            identifier=identifier,
            promoted=("version", "techniques", "datasets"),
            extra={
                "objective_target_hash": self.insert_target(objective_target) if objective_target else None,
                "objective_scorer_hash": self.insert_scorer(objective_scorer) if objective_scorer else None,
            },
        )

    def insert_atomic_attack(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert an atomic attack graph.

        Returns:
            str | None: The stored hash when successful.
        """
        attack_technique = self._child(identifier, "attack_technique")
        seeds = self._children(identifier, "seed_identifiers")
        seed_hashes = [seed_hash for seed in seeds if (seed_hash := self._insert_seed(seed))]
        identifier_hash = self._insert_identifier(
            table="AtomicAttackIdentifiers",
            identifier=identifier,
            extra={
                "attack_technique_identifier_hash": (
                    self._insert_attack_technique(attack_technique) if attack_technique else None
                )
            },
        )
        if identifier_hash:
            self._insert_edges(
                table="AtomicAttackSeedIdentifiers",
                parent_column="atomic_attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="seed_identifier_hash",
                child_hashes=seed_hashes,
            )
        return identifier_hash

    def _insert_attack_technique(self, identifier: dict[str, Any]) -> str | None:
        attack = self._child(identifier, "attack")
        seeds = self._children(identifier, "technique_seeds")
        seed_hashes = [seed_hash for seed in seeds if (seed_hash := self._insert_seed(seed))]
        identifier_hash = self._insert_identifier(
            table="AttackTechniqueIdentifiers",
            identifier=identifier,
            extra={"attack_identifier_hash": self._insert_attack(attack) if attack else None},
        )
        if identifier_hash:
            self._insert_edges(
                table="AttackTechniqueSeedIdentifiers",
                parent_column="attack_technique_identifier_hash",
                parent_hash=identifier_hash,
                child_column="seed_identifier_hash",
                child_hashes=seed_hashes,
            )
        return identifier_hash

    def _insert_attack(self, identifier: dict[str, Any]) -> str | None:
        objective_target = self._child(identifier, "objective_target")
        adversarial_chat = self._child(identifier, "adversarial_chat")
        objective_scorer = self._child(identifier, "objective_scorer")
        request_hashes = [
            value for item in self._children(identifier, "request_converters") if (value := self.insert_converter(item))
        ]
        response_hashes = [
            value
            for item in self._children(identifier, "response_converters")
            if (value := self.insert_converter(item))
        ]
        identifier_hash = self._insert_identifier(
            table="AttackIdentifiers",
            identifier=identifier,
            promoted=("adversarial_system_prompt", "adversarial_seed_prompt"),
            extra={
                "objective_target_hash": self.insert_target(objective_target) if objective_target else None,
                "adversarial_chat_hash": self.insert_target(adversarial_chat) if adversarial_chat else None,
                "objective_scorer_hash": self.insert_scorer(objective_scorer) if objective_scorer else None,
            },
        )
        if identifier_hash:
            self._insert_edges(
                table="AttackRequestConverterIdentifiers",
                parent_column="attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="converter_identifier_hash",
                child_hashes=request_hashes,
            )
            self._insert_edges(
                table="AttackResponseConverterIdentifiers",
                parent_column="attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="converter_identifier_hash",
                child_hashes=response_hashes,
            )
        return identifier_hash

    def _insert_seed(self, identifier: dict[str, Any]) -> str | None:
        return self._insert_identifier(
            table="SeedIdentifiers",
            identifier=identifier,
            promoted=("value", "value_sha256", "data_type", "dataset_name", "is_general_technique"),
        )

    def _insert_identifier(
        self,
        *,
        table: str,
        identifier: dict[str, Any],
        promoted: Sequence[str] = (),
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        identifier_hash = identifier.get("hash")
        if not isinstance(identifier_hash, str) or len(identifier_hash) != 64 or table not in self._hashes:
            return None
        if identifier_hash in self._hashes[table]:
            return identifier_hash
        values: dict[str, Any] = {
            "hash": identifier_hash,
            "class_name": identifier.get("class_name"),
            "class_module": identifier.get("class_module"),
            "identifier_json": json.dumps(identifier, sort_keys=True),
            "pyrit_version": identifier.get("pyrit_version"),
        }
        values.update({name: self._json_value(identifier.get(name)) for name in promoted})
        values.update(extra or {})
        columns = list(values)
        statement = sa.text(
            f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join(f":{column}" for column in columns)})'
        )
        self._bind.execute(statement, values)
        self._hashes[table].add(identifier_hash)
        return identifier_hash

    def _insert_edges(
        self,
        *,
        table: str,
        parent_column: str,
        parent_hash: str,
        child_column: str,
        child_hashes: Sequence[str],
    ) -> None:
        select_statement = sa.text(
            f'SELECT "{child_column}" FROM "{table}" WHERE "{parent_column}" = :parent_hash AND position = :position'
        )
        statement = sa.text(
            f'INSERT INTO "{table}" ("{parent_column}", position, "{child_column}") '
            f"VALUES (:parent_hash, :position, :child_hash)"
        )
        for position, child_hash in enumerate(child_hashes):
            parameters = {"parent_hash": parent_hash, "position": position}
            existing_child_hash = self._bind.execute(select_statement, parameters).scalar_one_or_none()
            if existing_child_hash == child_hash:
                continue
            if existing_child_hash is not None:
                raise ValueError(
                    f"Conflicting {table} edge for parent {parent_hash!r} at position {position}: "
                    f"stored child {existing_child_hash!r}, retained child {child_hash!r}."
                )
            self._bind.execute(
                statement,
                {**parameters, "child_hash": child_hash},
            )

    @staticmethod
    def _child(
        identifier: dict[str, Any],
        name: str,
        aliases: Sequence[str] = (),
    ) -> dict[str, Any] | None:
        children = identifier.get("children")
        children = children if isinstance(children, dict) else {}
        for key in (name, *aliases):
            value = identifier.get(key, children.get(key))
            if isinstance(value, dict):
                return load_identifier(value)
        return None

    @staticmethod
    def _children(
        identifier: dict[str, Any],
        name: str,
        aliases: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        children = identifier.get("children")
        children = children if isinstance(children, dict) else {}
        for key in (name, *aliases):
            value = identifier.get(key, children.get(key))
            if isinstance(value, list):
                return [child for item in value if (child := load_identifier(item)) is not None]
        return []

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.dumps(value) if isinstance(value, (list, dict)) else value


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
        batch_op.drop_constraint("fk_attack_result_entries_atomic_attack_identifier_hash", type_="foreignkey")
        batch_op.drop_column("atomic_attack_identifier_hash")
    with op.batch_alter_table("ScenarioResultEntries") as batch_op:
        batch_op.drop_constraint("fk_scenario_result_entries_scenario_identifier_hash", type_="foreignkey")
        batch_op.drop_column("scenario_identifier_hash")
    with op.batch_alter_table("ScoreEntries") as batch_op:
        batch_op.drop_constraint("fk_score_entries_scorer_identifier_hash", type_="foreignkey")
        batch_op.drop_column("scorer_identifier_hash")
    with op.batch_alter_table("Conversations") as batch_op:
        batch_op.drop_constraint("fk_conversations_target_identifier_hash", type_="foreignkey")
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


def _insert_converter_links(
    *,
    bind: Any,
    inserter: IdentifierGraphInserter,
    link_statement: Any,
    prompt_id: Any,
    stored_identifiers: Any,
    pyrit_version: str | None,
) -> None:
    """Insert converter graphs and their ordered links for one prompt row."""
    for position, identifier in enumerate(load_identifier_list(stored_identifiers)):
        if identifier.get("pyrit_version") is None:
            identifier = {**identifier, "pyrit_version": pyrit_version}
        identifier_hash = inserter.insert_converter(identifier)
        if identifier_hash:
            bind.execute(
                link_statement,
                {
                    "prompt_memory_entry_id": prompt_id,
                    "position": position,
                    "converter_identifier_hash": identifier_hash,
                },
            )


def _backfill_target_identifiers() -> None:
    """
    Populate ``TargetIdentifiers`` / ``TargetIdentifierChildren`` and set
    ``Conversations.target_identifier_hash``.

    For every ``Conversations`` row with a non-null ``target_identifier`` JSON,
    load the retained ``TargetIdentifier`` shape and its stored hash, insert the
    deduped ``TargetIdentifiers`` row if absent -- recursing into any inner
    ``targets`` first so the child edge foreign keys resolve -- record the
    ``parent_hash -> child_hash`` edges, and point the conversation's
    ``target_identifier_hash`` at the top-level row. Idempotent: hashes already present
    are not re-inserted. Rows whose stored target cannot be reconstructed are logged and
    skipped rather than aborting the upgrade.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            'SELECT conversation_id, target_identifier FROM "Conversations" '
            "WHERE target_identifier IS NOT NULL ORDER BY conversation_id"
        )
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
        operation = partial(
            _insert_identifier_link,
            bind=bind,
            insert=inserter.insert_target,
            identifier=identifier,
            update_statement=update_stmt,
            update_values={"cid": conversation_id},
        )
        if _run_best_effort_row(
            bind=bind,
            description=f"TargetIdentifiers backfill skipped conversation {conversation_id!r}",
            operation=operation,
        ):
            linked += 1
        else:
            skipped += 1
            inserter = IdentifierGraphInserter(bind=bind)

    if linked or skipped:
        logger.info(f"TargetIdentifiers backfill linked {linked} conversation(s); skipped {skipped}.")


def _backfill_scorer_identifiers() -> None:
    """Backfill scorer rows and score foreign keys from retained JSON."""
    bind = op.get_bind()
    score_rows = bind.execute(
        sa.text(
            'SELECT id, scorer_class_identifier FROM "ScoreEntries" '
            "WHERE scorer_class_identifier IS NOT NULL ORDER BY id"
        )
    ).fetchall()
    score_update = sa.text('UPDATE "ScoreEntries" SET scorer_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for score_id, raw_scorer in score_rows:
        identifier = load_identifier(raw_scorer)
        if identifier is None:
            skipped += 1
            continue
        operation = partial(
            _insert_identifier_link,
            bind=bind,
            insert=inserter.insert_scorer,
            identifier=identifier,
            update_statement=score_update,
            update_values={"id": score_id},
        )
        if not _run_best_effort_row(
            bind=bind,
            description=f"ScorerIdentifiers backfill: could not reconstruct scorer for score {score_id}",
            operation=operation,
        ):
            skipped += 1
            inserter = IdentifierGraphInserter(bind=bind)
    if skipped:
        logger.warning(f"ScorerIdentifiers backfill skipped {skipped} score row(s)")


def _backfill_scenario_identifiers() -> None:
    """Backfill scenario rows and result foreign keys from retained JSON."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text(
            'SELECT id, scenario_identifier FROM "ScenarioResultEntries" '
            "WHERE scenario_identifier IS NOT NULL ORDER BY id"
        )
    ).fetchall()
    update_stmt = sa.text('UPDATE "ScenarioResultEntries" SET scenario_identifier_hash = :hash WHERE id = :id')
    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for result_id, raw_scenario in result_rows:
        identifier = load_identifier(raw_scenario)
        if identifier is None:
            skipped += 1
            continue
        operation = partial(
            _insert_identifier_link,
            bind=bind,
            insert=inserter.insert_scenario,
            identifier=identifier,
            update_statement=update_stmt,
            update_values={"id": result_id},
        )
        if not _run_best_effort_row(
            bind=bind,
            description=f"ScenarioIdentifiers backfill: could not reconstruct scenario for result {result_id}",
            operation=operation,
        ):
            skipped += 1
            inserter = IdentifierGraphInserter(bind=bind)
    if skipped:
        logger.warning(f"ScenarioIdentifiers backfill skipped {skipped} scenario result row(s)")


def _backfill_converter_identifiers() -> None:
    """Materialize converter graphs and prompt associations from retained JSON."""
    bind = op.get_bind()
    prompt_rows = bind.execute(
        sa.text(
            'SELECT id, converter_identifiers, pyrit_version FROM "PromptMemoryEntries" '
            "WHERE converter_identifiers IS NOT NULL ORDER BY id"
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
        operation = partial(
            _insert_converter_links,
            bind=bind,
            inserter=inserter,
            link_statement=link_insert,
            prompt_id=prompt_id,
            stored_identifiers=stored_identifiers,
            pyrit_version=pyrit_version,
        )
        if not _run_best_effort_row(
            bind=bind,
            description=f"ConverterIdentifiers backfill: could not reconstruct converters for prompt {prompt_id}",
            operation=operation,
        ):
            skipped += 1
            inserter = IdentifierGraphInserter(bind=bind)
    if skipped:
        logger.warning(f"ConverterIdentifiers backfill skipped {skipped} prompt row(s)")


def _backfill_attack_identifiers() -> None:
    """Backfill attack identifier graphs and result links from retained JSON."""
    bind = op.get_bind()
    result_rows = bind.execute(
        sa.text(
            'SELECT id, atomic_attack_identifier FROM "AttackResultEntries" '
            "WHERE atomic_attack_identifier IS NOT NULL ORDER BY id"
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
        operation = partial(
            _insert_identifier_link,
            bind=bind,
            insert=inserter.insert_atomic_attack,
            identifier=identifier,
            update_statement=update_stmt,
            update_values={"id": result_id},
        )
        if not _run_best_effort_row(
            bind=bind,
            description=f"Attack identifier backfill could not reconstruct result {result_id}",
            operation=operation,
        ):
            skipped += 1
            inserter = IdentifierGraphInserter(bind=bind)
    if skipped:
        logger.warning(f"Attack identifier backfill skipped {skipped} attack result row(s)")
