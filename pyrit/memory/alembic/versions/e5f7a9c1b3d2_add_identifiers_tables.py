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

_TARGET_LINK_INSERT_BATCH_SIZE = 400
_IDENTIFIER_LINK_INSERT_BATCH_SIZE = 400
_CONVERTER_LINK_INSERT_BATCH_SIZE = 300
_IDENTIFIER_GRAPH_PROGRESS_INTERVAL = 100
_TARGET_LINK_PROGRESS_INTERVAL = 25
_IDENTIFIER_LINK_PROGRESS_INTERVAL = 25
_CONVERTER_LINK_PROGRESS_INTERVAL = 25
_TARGET_LINK_STAGING_TABLE = "_PyritConversationTargetLinks"
_TARGET_LINK_SQL_SERVER_STAGING_TABLE = "#PyritConversationTargetLinks"
_IDENTIFIER_LINK_STAGING_TABLE = "_PyritIdentifierLinks"
_IDENTIFIER_LINK_SQL_SERVER_STAGING_TABLE = "#PyritIdentifierLinks"


def _report_progress(message: str) -> None:
    """Write migration progress to Alembic stdout, or the logger outside a migration context."""
    try:
        context = op.get_context()
    except (AttributeError, NameError):
        logger.info(message)
        return
    config = context.config
    if config is not None:
        config.print_stdout(message)
    else:
        logger.info(message)


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


def _materialize_identifier_row_links(
    *,
    bind: Any,
    rows: Sequence[Any],
    name: str,
    insert: Callable[[IdentifierGraphInserter, dict[str, Any]], str | None],
) -> tuple[list[tuple[Any, str]], int]:
    """
    Materialize each unique retained identifier once and return its domain-row links.

    Returns:
        tuple[list[tuple[Any, str]], int]: Successful row links and skipped row count.
    """
    grouped_identifiers: dict[str, tuple[dict[str, Any], list[Any]]] = {}
    skipped = 0
    for row_id, raw_identifier in rows:
        identifier = load_identifier(raw_identifier)
        if identifier is None:
            skipped += 1
            continue
        key = json.dumps(identifier, sort_keys=True)
        grouped_identifiers.setdefault(key, (identifier, []))[1].append(str(row_id))

    links: list[tuple[Any, str]] = []
    inserter = IdentifierGraphInserter(bind=bind)
    group_count = len(grouped_identifiers)
    if group_count:
        _report_progress(
            f"{name} backfill: materializing {group_count} unique identifier graph(s) for {len(rows)} row(s)."
        )
    for group_number, (identifier, row_ids) in enumerate(grouped_identifiers.values(), start=1):
        if group_number == 1 or group_number % _IDENTIFIER_GRAPH_PROGRESS_INTERVAL == 0:
            _report_progress(f"{name} backfill: processing identifier graph {group_number}/{group_count}.")
        try:
            with bind.begin_nested():
                identifier_hash = insert(inserter, identifier)
        except Exception:
            skipped += len(row_ids)
            logger.warning(
                f"{name} backfill skipped {len(row_ids)} row(s) for identifier {identifier.get('hash')!r}",
                exc_info=True,
            )
            inserter = IdentifierGraphInserter(bind=bind)
            continue
        if identifier_hash:
            links.extend((row_id, identifier_hash) for row_id in row_ids)
        else:
            skipped += len(row_ids)

    if group_count:
        _report_progress(f"{name} backfill: processed {group_count} unique identifier graph(s).")
    return links, skipped


def _materialize_converter_links(
    *,
    bind: Any,
    rows: Sequence[Any],
) -> tuple[list[tuple[Any, list[tuple[Any, int, str]]]], int]:
    """
    Materialize converter graphs and build ordered associations grouped by prompt.

    Returns:
        tuple[list[tuple[Any, list[tuple[Any, int, str]]]], int]:
            Prompt-grouped associations and skipped prompt count.
    """
    grouped_identifiers: dict[str, tuple[dict[str, Any], set[Any]]] = {}
    prompt_references: list[tuple[Any, list[tuple[int, str]]]] = []
    for prompt_id, stored_identifiers, pyrit_version in rows:
        prompt_id = str(prompt_id)
        references: list[tuple[int, str]] = []
        for position, identifier in enumerate(load_identifier_list(stored_identifiers)):
            if identifier.get("pyrit_version") is None:
                identifier = {**identifier, "pyrit_version": pyrit_version}
            key = json.dumps(identifier, sort_keys=True)
            grouped_identifiers.setdefault(key, (identifier, set()))[1].add(prompt_id)
            references.append((position, key))
        if references:
            prompt_references.append((prompt_id, references))

    hashes_by_key: dict[str, str] = {}
    failed_keys: set[str] = set()
    inserter = IdentifierGraphInserter(bind=bind)
    group_count = len(grouped_identifiers)
    if group_count:
        _report_progress(
            f"ConverterIdentifiers backfill: materializing {group_count} unique identifier graph(s) "
            f"for {len(prompt_references)} prompt(s)."
        )
    for group_number, (key, (identifier, _)) in enumerate(grouped_identifiers.items(), start=1):
        if group_number == 1 or group_number % _IDENTIFIER_GRAPH_PROGRESS_INTERVAL == 0:
            _report_progress(
                f"ConverterIdentifiers backfill: processing identifier graph {group_number}/{group_count}."
            )
        try:
            with bind.begin_nested():
                identifier_hash = inserter.insert_converter(identifier)
        except Exception:
            failed_keys.add(key)
            logger.warning(
                f"ConverterIdentifiers backfill could not materialize identifier {identifier.get('hash')!r}",
                exc_info=True,
            )
            inserter = IdentifierGraphInserter(bind=bind)
            continue
        if identifier_hash:
            hashes_by_key[key] = identifier_hash

    if group_count:
        _report_progress(f"ConverterIdentifiers backfill: processed {group_count} unique identifier graph(s).")

    prompt_links: list[tuple[Any, list[tuple[Any, int, str]]]] = []
    skipped_prompt_ids: set[Any] = set()
    for prompt_id, references in prompt_references:
        if any(key in failed_keys for _, key in references):
            skipped_prompt_ids.add(prompt_id)
            continue
        links = [
            (prompt_id, position, identifier_hash)
            for position, key in references
            if (identifier_hash := hashes_by_key.get(key)) is not None
        ]
        if links:
            prompt_links.append((prompt_id, links))
    return prompt_links, len(skipped_prompt_ids)


def _pack_converter_link_batches(
    *,
    prompt_links: Sequence[tuple[Any, list[tuple[Any, int, str]]]],
) -> list[list[tuple[Any, list[tuple[Any, int, str]]]]]:
    """
    Pack complete prompt association groups into parameter-bounded batches.

    Returns:
        list[list[tuple[Any, list[tuple[Any, int, str]]]]]: Packed prompt groups.
    """
    batches: list[list[tuple[Any, list[tuple[Any, int, str]]]]] = []
    current_batch: list[tuple[Any, list[tuple[Any, int, str]]]] = []
    current_size = 0
    for prompt_group in prompt_links:
        group_size = len(prompt_group[1])
        if current_batch and current_size + group_size > _CONVERTER_LINK_INSERT_BATCH_SIZE:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(prompt_group)
        current_size += group_size
        if current_size >= _CONVERTER_LINK_INSERT_BATCH_SIZE:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
    if current_batch:
        batches.append(current_batch)
    return batches


def _insert_converter_link_batch(*, bind: Any, links: Sequence[tuple[Any, int, str]]) -> None:
    """Insert one parameter-bounded batch of prompt-converter associations."""
    values: list[str] = []
    parameters: dict[str, Any] = {}
    for index, (prompt_id, position, identifier_hash) in enumerate(links):
        values.append(f"(:prompt_id_{index}, :position_{index}, :hash_{index})")
        parameters[f"prompt_id_{index}"] = prompt_id
        parameters[f"position_{index}"] = position
        parameters[f"hash_{index}"] = identifier_hash
    bind.execute(
        sa.text(
            'INSERT INTO "PromptConverterIdentifiers" '
            "(prompt_memory_entry_id, position, converter_identifier_hash) "
            f"VALUES {', '.join(values)}"
        ),
        parameters,
    )


def _insert_converter_link_chunks(*, bind: Any, links: Sequence[tuple[Any, int, str]]) -> None:
    """Insert all associations for a batch while respecting parameter limits."""
    for start in range(0, len(links), _CONVERTER_LINK_INSERT_BATCH_SIZE):
        _insert_converter_link_batch(bind=bind, links=links[start : start + _CONVERTER_LINK_INSERT_BATCH_SIZE])


def _insert_prompt_converter_links(
    *,
    bind: Any,
    prompt_links: Sequence[tuple[Any, list[tuple[Any, int, str]]]],
) -> tuple[int, int]:
    """
    Insert prompt associations in batches with per-prompt fallback.

    Returns:
        tuple[int, int]: Inserted association and skipped prompt counts.
    """
    batches = _pack_converter_link_batches(prompt_links=prompt_links)
    inserted = 0
    skipped_prompts = 0
    if batches:
        _report_progress(
            f"ConverterIdentifiers backfill: inserting {sum(len(links) for _, links in prompt_links)} "
            f"association(s) in {len(batches)} batch(es)."
        )
    for batch_number, prompt_batch in enumerate(batches, start=1):
        links = [link for _, prompt_group in prompt_batch for link in prompt_group]
        try:
            with bind.begin_nested():
                _insert_converter_link_chunks(bind=bind, links=links)
            inserted += len(links)
        except Exception:
            logger.warning(
                f"ConverterIdentifiers association batch failed for {len(prompt_batch)} prompt(s); retrying per prompt",
                exc_info=True,
            )
            for prompt_id, prompt_group in prompt_batch:
                operation = partial(_insert_converter_link_chunks, bind=bind, links=prompt_group)
                if _run_best_effort_row(
                    bind=bind,
                    description=f"ConverterIdentifiers backfill skipped prompt {prompt_id}",
                    operation=operation,
                ):
                    inserted += len(prompt_group)
                else:
                    skipped_prompts += 1
        if batch_number % _CONVERTER_LINK_PROGRESS_INTERVAL == 0 or batch_number == len(batches):
            _report_progress(
                f"ConverterIdentifiers backfill: completed association batch {batch_number}/{len(batches)}."
            )
    return inserted, skipped_prompts


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

    links, skipped = _materialize_identifier_row_links(
        bind=bind,
        rows=rows,
        name="TargetIdentifiers",
        insert=IdentifierGraphInserter.insert_target,
    )
    linked, update_skipped = _update_conversation_target_links(bind=bind, links=links)
    skipped += update_skipped

    if linked or skipped:
        logger.info(f"TargetIdentifiers backfill linked {linked} conversation(s); skipped {skipped}.")


def _update_conversation_target_links(*, bind: Any, links: Sequence[tuple[str, str]]) -> tuple[int, int]:
    """
    Stage target links in bounded batches, then update all conversations once.

    Returns:
        tuple[int, int]: The linked and skipped conversation counts.
    """
    if not links:
        return 0, 0

    table_name = _create_target_link_staging_table(bind=bind)
    try:
        staged_links, skipped = _stage_conversation_target_links(bind=bind, table_name=table_name, links=links)
        try:
            _report_progress(f"TargetIdentifiers backfill: applying {len(staged_links)} staged target link(s).")
            with bind.begin_nested():
                _update_conversation_target_links_from_staging(bind=bind, table_name=table_name)
            _report_progress("TargetIdentifiers backfill: staged target-link update completed.")
            return len(staged_links), skipped
        except Exception:
            logger.warning(
                "TargetIdentifiers staged link update failed; retrying individually",
                exc_info=True,
            )
            linked, update_skipped = _update_conversation_target_links_individually(
                bind=bind,
                links=staged_links,
            )
            return linked, skipped + update_skipped
    finally:
        bind.execute(sa.text(f'DROP TABLE "{table_name}"'))


def _create_target_link_staging_table(*, bind: Any) -> str:
    """
    Create a connection-local table for target-link updates.

    Returns:
        str: The dialect-specific staging table name.
    """
    if bind.dialect.name == "mssql":
        table_name = _TARGET_LINK_SQL_SERVER_STAGING_TABLE
        create_prefix = "CREATE TABLE"
    else:
        table_name = _TARGET_LINK_STAGING_TABLE
        create_prefix = "CREATE TEMPORARY TABLE"
    bind.execute(
        sa.text(
            f'{create_prefix} "{table_name}" ('
            "conversation_id VARCHAR(36) NOT NULL PRIMARY KEY, "
            "target_identifier_hash VARCHAR(64) NOT NULL)"
        )
    )
    return table_name


def _stage_conversation_target_links(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """
    Insert target links into the staging table with per-row fallback.

    Returns:
        tuple[list[tuple[str, str]], int]: The staged links and skipped count.
    """
    staged_links: list[tuple[str, str]] = []
    skipped = 0
    batch_count = (len(links) + _TARGET_LINK_INSERT_BATCH_SIZE - 1) // _TARGET_LINK_INSERT_BATCH_SIZE
    _report_progress(f"TargetIdentifiers backfill: staging {len(links)} target link(s) in {batch_count} batch(es).")
    for batch_number, start in enumerate(range(0, len(links), _TARGET_LINK_INSERT_BATCH_SIZE), start=1):
        batch = links[start : start + _TARGET_LINK_INSERT_BATCH_SIZE]
        try:
            with bind.begin_nested():
                _insert_target_link_batch(bind=bind, table_name=table_name, links=batch)
            staged_links.extend(batch)
        except Exception:
            logger.warning(
                f"TargetIdentifiers staging insert failed for {len(batch)} conversation(s); retrying individually",
                exc_info=True,
            )
            batch_staged, batch_skipped = _stage_conversation_target_links_individually(
                bind=bind,
                table_name=table_name,
                links=batch,
            )
            staged_links.extend(batch_staged)
            skipped += batch_skipped
        if batch_number % _TARGET_LINK_PROGRESS_INTERVAL == 0 or batch_number == batch_count:
            _report_progress(f"TargetIdentifiers backfill: completed staging batch {batch_number}/{batch_count}.")
    return staged_links, skipped


def _insert_target_link_batch(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[str, str]],
) -> None:
    """Insert one non-empty target-link batch into the staging table."""
    values: list[str] = []
    parameters: dict[str, str] = {}
    for index, (conversation_id, identifier_hash) in enumerate(links):
        values.append(f"(:cid_{index}, :hash_{index})")
        parameters[f"cid_{index}"] = conversation_id
        parameters[f"hash_{index}"] = identifier_hash
    bind.execute(
        sa.text(f'INSERT INTO "{table_name}" (conversation_id, target_identifier_hash) VALUES {", ".join(values)}'),
        parameters,
    )


def _stage_conversation_target_links_individually(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """
    Retry a failed staging batch one link at a time.

    Returns:
        tuple[list[tuple[str, str]], int]: The staged links and skipped count.
    """
    statement = sa.text(f'INSERT INTO "{table_name}" (conversation_id, target_identifier_hash) VALUES (:cid, :hash)')
    staged_links: list[tuple[str, str]] = []
    for conversation_id, identifier_hash in links:
        operation = partial(bind.execute, statement, {"cid": conversation_id, "hash": identifier_hash})
        if _run_best_effort_row(
            bind=bind,
            description=f"TargetIdentifiers backfill skipped conversation {conversation_id!r}",
            operation=operation,
        ):
            staged_links.append((conversation_id, identifier_hash))
    return staged_links, len(links) - len(staged_links)


def _update_conversation_target_links_from_staging(*, bind: Any, table_name: str) -> None:
    """Update conversation links from the connection-local staging table."""
    if bind.dialect.name == "mssql":
        statement = sa.text(
            "UPDATE conversations SET target_identifier_hash = links.target_identifier_hash "
            'FROM "Conversations" AS conversations '
            f'INNER JOIN "{table_name}" AS links ON links.conversation_id = conversations.conversation_id'
        )
    else:
        statement = sa.text(
            'UPDATE "Conversations" SET target_identifier_hash = ('
            f'SELECT links.target_identifier_hash FROM "{table_name}" AS links '
            'WHERE links.conversation_id = "Conversations".conversation_id) '
            f'WHERE EXISTS (SELECT 1 FROM "{table_name}" AS links '
            'WHERE links.conversation_id = "Conversations".conversation_id)'
        )
    bind.execute(statement)


def _update_conversation_target_links_individually(
    *,
    bind: Any,
    links: Sequence[tuple[str, str]],
) -> tuple[int, int]:
    """
    Retry a failed target-link batch one conversation at a time.

    Returns:
        tuple[int, int]: The linked and skipped conversation counts.
    """
    statement = sa.text('UPDATE "Conversations" SET target_identifier_hash = :hash WHERE conversation_id = :cid')
    linked = 0
    for conversation_id, identifier_hash in links:
        operation = partial(bind.execute, statement, {"cid": conversation_id, "hash": identifier_hash})
        if _run_best_effort_row(
            bind=bind,
            description=f"TargetIdentifiers backfill skipped conversation {conversation_id!r}",
            operation=operation,
        ):
            linked += 1
    return linked, len(links) - linked


def _update_identifier_row_links(
    *,
    bind: Any,
    links: Sequence[tuple[Any, str]],
    name: str,
    target_table: str,
    target_id_column: str,
    target_hash_column: str,
) -> tuple[int, int]:
    """
    Stage identifier links in bounded batches, then update all domain rows once.

    Returns:
        tuple[int, int]: The linked and skipped row counts.
    """
    if not links:
        return 0, 0

    table_name = _create_identifier_link_staging_table(bind=bind)
    try:
        staged_links, skipped = _stage_identifier_row_links(
            bind=bind,
            table_name=table_name,
            links=links,
            name=name,
        )
        try:
            _report_progress(f"{name} backfill: applying {len(staged_links)} staged row link(s).")
            with bind.begin_nested():
                _update_identifier_row_links_from_staging(
                    bind=bind,
                    table_name=table_name,
                    target_table=target_table,
                    target_id_column=target_id_column,
                    target_hash_column=target_hash_column,
                )
            _report_progress(f"{name} backfill: staged row-link update completed.")
            return len(staged_links), skipped
        except Exception:
            logger.warning(f"{name} staged row-link update failed; retrying individually", exc_info=True)
            linked, update_skipped = _update_identifier_row_links_individually(
                bind=bind,
                links=staged_links,
                name=name,
                target_table=target_table,
                target_id_column=target_id_column,
                target_hash_column=target_hash_column,
            )
            return linked, skipped + update_skipped
    finally:
        bind.execute(sa.text(f'DROP TABLE "{table_name}"'))


def _create_identifier_link_staging_table(*, bind: Any) -> str:
    """
    Create a connection-local table for domain-row identifier links.

    Returns:
        str: The dialect-specific staging table name.
    """
    if bind.dialect.name == "mssql":
        table_name = _IDENTIFIER_LINK_SQL_SERVER_STAGING_TABLE
        create_prefix = "CREATE TABLE"
        row_id_type = "UNIQUEIDENTIFIER"
    else:
        table_name = _IDENTIFIER_LINK_STAGING_TABLE
        create_prefix = "CREATE TEMPORARY TABLE"
        row_id_type = "VARCHAR(36)"
    bind.execute(
        sa.text(
            f'{create_prefix} "{table_name}" ('
            f"row_id {row_id_type} NOT NULL PRIMARY KEY, "
            "identifier_hash VARCHAR(64) NOT NULL)"
        )
    )
    return table_name


def _stage_identifier_row_links(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[Any, str]],
    name: str,
) -> tuple[list[tuple[Any, str]], int]:
    """
    Insert domain-row links into the staging table with per-row fallback.

    Returns:
        tuple[list[tuple[Any, str]], int]: Staged links and skipped row count.
    """
    staged_links: list[tuple[Any, str]] = []
    skipped = 0
    batch_count = (len(links) + _IDENTIFIER_LINK_INSERT_BATCH_SIZE - 1) // _IDENTIFIER_LINK_INSERT_BATCH_SIZE
    _report_progress(f"{name} backfill: staging {len(links)} row link(s) in {batch_count} batch(es).")
    for batch_number, start in enumerate(range(0, len(links), _IDENTIFIER_LINK_INSERT_BATCH_SIZE), start=1):
        batch = links[start : start + _IDENTIFIER_LINK_INSERT_BATCH_SIZE]
        try:
            with bind.begin_nested():
                _insert_identifier_row_link_batch(bind=bind, table_name=table_name, links=batch)
            staged_links.extend(batch)
        except Exception:
            logger.warning(
                f"{name} staging insert failed for {len(batch)} row(s); retrying individually",
                exc_info=True,
            )
            batch_staged, batch_skipped = _stage_identifier_row_links_individually(
                bind=bind,
                table_name=table_name,
                links=batch,
                name=name,
            )
            staged_links.extend(batch_staged)
            skipped += batch_skipped
        if batch_number % _IDENTIFIER_LINK_PROGRESS_INTERVAL == 0 or batch_number == batch_count:
            _report_progress(f"{name} backfill: completed staging batch {batch_number}/{batch_count}.")
    return staged_links, skipped


def _insert_identifier_row_link_batch(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[Any, str]],
) -> None:
    """Insert one non-empty batch of domain-row identifier links."""
    values: list[str] = []
    parameters: dict[str, Any] = {}
    for index, (row_id, identifier_hash) in enumerate(links):
        values.append(f"(:row_id_{index}, :hash_{index})")
        parameters[f"row_id_{index}"] = row_id
        parameters[f"hash_{index}"] = identifier_hash
    bind.execute(
        sa.text(f'INSERT INTO "{table_name}" (row_id, identifier_hash) VALUES {", ".join(values)}'),
        parameters,
    )


def _stage_identifier_row_links_individually(
    *,
    bind: Any,
    table_name: str,
    links: Sequence[tuple[Any, str]],
    name: str,
) -> tuple[list[tuple[Any, str]], int]:
    """
    Retry a failed staging batch one row at a time.

    Returns:
        tuple[list[tuple[Any, str]], int]: Staged links and skipped row count.
    """
    statement = sa.text(f'INSERT INTO "{table_name}" (row_id, identifier_hash) VALUES (:row_id, :hash)')
    staged_links: list[tuple[Any, str]] = []
    for row_id, identifier_hash in links:
        operation = partial(bind.execute, statement, {"row_id": row_id, "hash": identifier_hash})
        if _run_best_effort_row(
            bind=bind,
            description=f"{name} backfill skipped row {row_id!r}",
            operation=operation,
        ):
            staged_links.append((row_id, identifier_hash))
    return staged_links, len(links) - len(staged_links)


def _update_identifier_row_links_from_staging(
    *,
    bind: Any,
    table_name: str,
    target_table: str,
    target_id_column: str,
    target_hash_column: str,
) -> None:
    """Update domain-row identifier links from the connection-local staging table."""
    if bind.dialect.name == "mssql":
        statement = sa.text(
            f'UPDATE domain_rows SET "{target_hash_column}" = links.identifier_hash '
            f'FROM "{target_table}" AS domain_rows '
            f'INNER JOIN "{table_name}" AS links ON links.row_id = domain_rows."{target_id_column}"'
        )
    else:
        statement = sa.text(
            f'UPDATE "{target_table}" SET "{target_hash_column}" = ('
            f'SELECT links.identifier_hash FROM "{table_name}" AS links '
            f'WHERE links.row_id = "{target_table}"."{target_id_column}") '
            f'WHERE EXISTS (SELECT 1 FROM "{table_name}" AS links '
            f'WHERE links.row_id = "{target_table}"."{target_id_column}")'
        )
    bind.execute(statement)


def _update_identifier_row_links_individually(
    *,
    bind: Any,
    links: Sequence[tuple[Any, str]],
    name: str,
    target_table: str,
    target_id_column: str,
    target_hash_column: str,
) -> tuple[int, int]:
    """
    Retry a failed staged update one row at a time.

    Returns:
        tuple[int, int]: Linked and skipped row counts.
    """
    statement = sa.text(
        f'UPDATE "{target_table}" SET "{target_hash_column}" = :hash WHERE "{target_id_column}" = :row_id'
    )
    linked = 0
    for row_id, identifier_hash in links:
        operation = partial(bind.execute, statement, {"row_id": row_id, "hash": identifier_hash})
        if _run_best_effort_row(
            bind=bind,
            description=f"{name} backfill skipped row {row_id!r}",
            operation=operation,
        ):
            linked += 1
    return linked, len(links) - linked


def _backfill_scorer_identifiers() -> None:
    """Backfill scorer rows and score foreign keys from retained JSON."""
    bind = op.get_bind()
    score_rows = bind.execute(
        sa.text(
            'SELECT id, scorer_class_identifier FROM "ScoreEntries" '
            "WHERE scorer_class_identifier IS NOT NULL ORDER BY id"
        )
    ).fetchall()
    links, skipped = _materialize_identifier_row_links(
        bind=bind,
        rows=score_rows,
        name="ScorerIdentifiers",
        insert=IdentifierGraphInserter.insert_scorer,
    )
    linked, update_skipped = _update_identifier_row_links(
        bind=bind,
        links=links,
        name="ScorerIdentifiers",
        target_table="ScoreEntries",
        target_id_column="id",
        target_hash_column="scorer_identifier_hash",
    )
    skipped += update_skipped
    if linked or skipped:
        _report_progress(f"ScorerIdentifiers backfill: linked {linked} score row(s); skipped {skipped}.")
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
    links, skipped = _materialize_identifier_row_links(
        bind=bind,
        rows=result_rows,
        name="ScenarioIdentifiers",
        insert=IdentifierGraphInserter.insert_scenario,
    )
    linked, update_skipped = _update_identifier_row_links(
        bind=bind,
        links=links,
        name="ScenarioIdentifiers",
        target_table="ScenarioResultEntries",
        target_id_column="id",
        target_hash_column="scenario_identifier_hash",
    )
    skipped += update_skipped
    if linked or skipped:
        _report_progress(f"ScenarioIdentifiers backfill: linked {linked} result row(s); skipped {skipped}.")
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
    prompt_links, skipped = _materialize_converter_links(bind=bind, rows=prompt_rows)
    inserted, association_skipped = _insert_prompt_converter_links(bind=bind, prompt_links=prompt_links)
    skipped += association_skipped
    if inserted or skipped:
        _report_progress(
            f"ConverterIdentifiers backfill: inserted {inserted} prompt association(s); skipped {skipped} prompt(s)."
        )
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
    links, skipped = _materialize_identifier_row_links(
        bind=bind,
        rows=result_rows,
        name="AttackIdentifiers",
        insert=IdentifierGraphInserter.insert_atomic_attack,
    )
    linked, update_skipped = _update_identifier_row_links(
        bind=bind,
        links=links,
        name="AttackIdentifiers",
        target_table="AttackResultEntries",
        target_id_column="id",
        target_hash_column="atomic_attack_identifier_hash",
    )
    skipped += update_skipped
    if linked or skipped:
        _report_progress(f"AttackIdentifiers backfill: linked {linked} attack result row(s); skipped {skipped}.")
    if skipped:
        logger.warning(f"Attack identifier backfill skipped {skipped} attack result row(s)")
