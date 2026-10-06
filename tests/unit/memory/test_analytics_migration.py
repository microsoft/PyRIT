# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import importlib
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import String, create_engine, inspect, text
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateIndex, CreateTable

from pyrit.memory.alembic.versions.e5f7a9c1b3d2_add_identifiers_tables import IdentifierGraphInserter
from pyrit.memory.analytics_identity_v1 import ObjectiveTargetAnalyticsIdentityV1
from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import AtomicAttackIdentifier, AttackIdentifier, TargetIdentifier

if TYPE_CHECKING:
    from pytest import LogCaptureFixture
    from sqlalchemy.engine import Dialect

MAIN_REVISION = "34a18645c7e9"
ANALYTICS_REVISION = "901e6c7bf9d4"


def test_analytics_migration_is_single_successor_of_main() -> None:
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    assert scripts.get_heads() == [ANALYTICS_REVISION]
    revision = scripts.get_revision(ANALYTICS_REVISION)
    assert revision is not None
    assert revision.down_revision == MAIN_REVISION
    published_parent = scripts.get_revision(MAIN_REVISION)
    assert published_parent is not None
    assert published_parent.down_revision == "6ea3eb4b61c3"


def configuration(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes["connection"] = connection
    return config


def insert_result(connection: Connection, *, identifier: dict[str, Any] | None, outcome: str = "success") -> str:
    result_id = str(uuid.uuid4())
    connection.execute(
        text(
            'INSERT INTO "AttackResultEntries" '
            "(id, conversation_id, objective, outcome, timestamp, executed_turns, "
            "execution_time_ms, atomic_attack_identifier) "
            "VALUES (:id, 'shared-conversation', 'Synthetic objective', :outcome, '2026-01-01', 0, 0, :identifier)"
        ),
        {"id": result_id, "outcome": outcome, "identifier": json.dumps(identifier)},
    )
    return result_id


@pytest.mark.parametrize(
    "starting_revision",
    ["aca1eba410d9", "6ea3eb4b61c3", MAIN_REVISION],
)
def test_analytics_migration_upgrades_from_main_revisions(*, tmp_path: Path, starting_revision: str) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'merge.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, starting_revision)
            result_id = insert_result(connection, identifier={"hash": "a" * 64})
            command.upgrade(config, "head")
            head_revision = connection.execute(
                text("SELECT version_num FROM pyrit_memory_alembic_version")
            ).scalar_one()
            assert head_revision == ANALYTICS_REVISION
            assert (
                connection.execute(
                    text('SELECT resolved_atomic_attack_identifier_hash FROM "AttackResultEntries" WHERE id = :id'),
                    {"id": result_id},
                ).scalar_one()
                == "a" * 64
            )
            assert "conditions" in {column["name"] for column in inspect(connection).get_columns("SeedPromptEntries")}
            assert "adversarial_prompt_template" in {
                column["name"] for column in inspect(connection).get_columns("AttackIdentifiers")
            }
            assert "use_score_as_feedback" in {
                column["name"] for column in inspect(connection).get_columns("AttackIdentifiers")
            }
            assert "attack_result_id" in {column["name"] for column in inspect(connection).get_columns("Conversations")}
            assert "objective_target_eval_hash_v1" in {
                column["name"] for column in inspect(connection).get_columns("AttackResultEntries")
            }
            command.check(config)
    finally:
        engine.dispose()


def test_evaluation_hash_migration_backfills_supported_history_and_preserves_unsupported(
    *, tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'eval-history.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, MAIN_REVISION)
            target = TargetIdentifier(
                class_name="MockTarget",
                class_module="tests",
                model_name="deployment",
                underlying_model_name="model-x",
                endpoint="https://example.invalid",
            )
            atomic = AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name="ProbeAttack", class_module="tests", objective_target=target
                )
            )
            document = atomic.model_dump()
            identifier_hash = IdentifierGraphInserter(bind=connection).insert_atomic_attack(identifier=document)
            assert identifier_hash == atomic.hash
            embedded_id = insert_result(connection, identifier=document)
            normalized_id = insert_result(connection, identifier=None, outcome="failure")
            connection.execute(
                text('UPDATE "AttackResultEntries" SET atomic_attack_identifier_hash = :hash WHERE id = :id'),
                {"hash": identifier_hash, "id": normalized_id},
            )
            legacy_document = {
                "children": {
                    "attack": {
                        "__type__": "LegacyAttack",
                        "children": {"objective_target": {"__type__": "LegacyTarget", "model_name": "legacy-model"}},
                    }
                }
            }
            legacy_id = insert_result(connection, identifier=legacy_document, outcome="success")
            unsupported_id = insert_result(
                connection,
                identifier={
                    "children": {
                        "attack": {
                            "class_name": "Attack",
                            "children": {"objective_target": {"class_name": {"invalid": True}}},
                        }
                    }
                },
                outcome="error",
            )
            missing_id = insert_result(connection, identifier=None, outcome="undetermined")
            command.upgrade(config, "head")
            expected = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=target)
            legacy = ObjectiveTargetAnalyticsIdentityV1.from_atomic_document(document=legacy_document)
            assert legacy is not None
            rows = connection.execute(text('SELECT id, objective_target_eval_hash_v1 FROM "AttackResultEntries"')).all()
            expected_rows = {
                embedded_id: expected,
                normalized_id: expected,
                legacy_id: legacy,
                unsupported_id: None,
                missing_id: None,
            }
            assert dict(rows) == expected_rows
            assert str(unsupported_id) in caplog.text
            assert "unsupported embedded target identifier" in caplog.text
            assert "unavailable v1 target identity" in caplog.text
            indexes = {entry["name"] for entry in inspect(connection).get_indexes("AttackResultEntries")}
            assert "ix_AttackResultEntries_objective_target_eval_v1" in indexes
            command.check(config)
            command.downgrade(config, MAIN_REVISION)
            assert set(connection.execute(text('SELECT id FROM "AttackResultEntries"')).scalars()) == set(expected_rows)
            assert "objective_target_eval_hash_v1" not in {
                column["name"] for column in inspect(connection).get_columns("AttackResultEntries")
            }
            assert "use_score_as_feedback" in {
                column["name"] for column in inspect(connection).get_columns("AttackIdentifiers")
            }
            command.upgrade(config, "head")
            assert (
                dict(
                    connection.execute(
                        text('SELECT id, objective_target_eval_hash_v1 FROM "AttackResultEntries"')
                    ).all()
                )
                == expected_rows
            )
    finally:
        engine.dispose()


def test_evaluation_hash_migration_backfill_crosses_batch_boundary(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'eval-batches.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, MAIN_REVISION)
            target = TargetIdentifier(class_name="MockTarget", class_module="tests", model_name="model-a")
            atomic = AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name="ProbeAttack", class_module="tests", objective_target=target
                )
            )
            document = json.dumps(atomic.model_dump())
            insert = text(
                'INSERT INTO "AttackResultEntries" '
                "(id, conversation_id, objective, outcome, timestamp, executed_turns, "
                "execution_time_ms, atomic_attack_identifier) "
                "VALUES (:id, :conversation_id, 'Synthetic objective', 'success', '2026-01-01', 0, 0, :identifier)"
            )
            connection.execute(
                insert,
                [
                    {"id": str(uuid.UUID(int=index)), "conversation_id": f"conv-{index}", "identifier": document}
                    for index in range(1, 502)
                ],
            )
            command.upgrade(config, "head")
            assert (
                connection.execute(
                    text('SELECT COUNT(*) FROM "AttackResultEntries" WHERE objective_target_eval_hash_v1 = :hash'),
                    {"hash": ObjectiveTargetAnalyticsIdentityV1.hash(identifier=target)},
                ).scalar_one()
                == 501
            )
    finally:
        engine.dispose()


def test_analytics_migration_roundtrip_preserves_result_ids_and_generated_lookup(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, MAIN_REVISION)
            first = insert_result(connection, identifier={"hash": "a" * 64})
            second = insert_result(connection, identifier={"hash": "b" * 64}, outcome="failure")
            command.upgrade(config, "head")
            command.check(config)
            rows = connection.execute(
                text(
                    "SELECT id, outcome, conversation_id, atomic_attack_identifier_hash, "
                    'resolved_atomic_attack_identifier_hash FROM "AttackResultEntries" ORDER BY outcome DESC'
                )
            ).all()
            assert {(row.id, row.outcome) for row in rows} == {(first, "success"), (second, "failure")}
            assert {row.conversation_id for row in rows} == {"shared-conversation"}
            assert all(row.atomic_attack_identifier_hash is None for row in rows)
            assert {row.resolved_atomic_attack_identifier_hash for row in rows} == {"a" * 64, "b" * 64}
            indexes = {entry["name"] for entry in inspect(connection).get_indexes("AttackResultEntries")}
            assert "ix_AttackResultEntries_analytics_facts_sqlite" in indexes
            assert "ix_AttackResultEntries_analytics_labels_sqlite" in indexes
            assert not any(name.endswith("_mssql") for name in indexes)
            connection.execute(
                text('UPDATE "AttackResultEntries" SET atomic_attack_identifier = :value WHERE id = :id'),
                {"value": json.dumps({"hash": "c" * 64}), "id": first},
            )
            assert (
                connection.execute(
                    text('SELECT resolved_atomic_attack_identifier_hash FROM "AttackResultEntries" WHERE id = :id'),
                    {"id": first},
                ).scalar_one()
                == "c" * 64
            )
            command.downgrade(config, MAIN_REVISION)
            columns = {column["name"] for column in inspect(connection).get_columns("AttackResultEntries")}
            assert "resolved_atomic_attack_identifier_hash" not in columns
            assert set(connection.execute(text('SELECT id, outcome FROM "AttackResultEntries"'))) == {
                (first, "success"),
                (second, "failure"),
            }
            command.upgrade(config, "head")
            command.check(config)
    finally:
        engine.dispose()


@pytest.mark.parametrize("outcome", ["x" * 17, "unexpected-" * 3])
def test_analytics_migration_rejects_oversized_outcomes_before_altering_rows(*, tmp_path: Path, outcome: str) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, MAIN_REVISION)
            result_id = insert_result(connection, identifier={}, outcome=outcome)
            with pytest.raises(ValueError, match="16 characters"):
                command.upgrade(config, "head")
            assert (
                connection.execute(
                    text('SELECT outcome FROM "AttackResultEntries" WHERE id = :id'), {"id": result_id}
                ).scalar_one()
                == outcome
            )
            columns = {column["name"] for column in inspect(connection).get_columns("AttackResultEntries")}
            assert "resolved_atomic_attack_identifier_hash" not in columns
    finally:
        engine.dispose()


def test_analytics_migration_preserves_outcome_at_the_index_key_limit(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'outcome-limit.sqlite'}")
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            command.upgrade(config, MAIN_REVISION)
            result_id = insert_result(connection, identifier=None, outcome="x" * 16)
            command.upgrade(config, "head")
            row = connection.execute(
                text('SELECT id, outcome, resolved_atomic_attack_identifier_hash FROM "AttackResultEntries"')
            ).one()
            assert tuple(row) == (result_id, "x" * 16, None)
            command.check(config)
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_generated_lookup_and_index_ddl_are_dialect_specific(dialect: Dialect) -> None:
    sql = str(CreateTable(AttackResultEntry.__table__).compile(dialect=dialect))
    assert ("json_extract" if dialect.name == "sqlite" else "JSON_VALUE") in sql
    assert "resolved_atomic_attack_identifier_hash" in sql
    for index in AttackResultEntry.__table__.indexes:
        if index.info.get("dialect") != dialect.name:
            continue
        ddl = str(CreateIndex(index).compile(dialect=dialect))
        assert "CREATE INDEX" in ddl
        for column in index.columns:
            if isinstance(column.type, String):
                assert column.type.length is not None
        if dialect.name == "mssql":
            assert "INCLUDE" in ddl
            assert "json_extract" not in ddl
            assert "targeted_harm_categories" not in {column.name for column in index.columns}
            assert "labels" not in {column.name for column in index.columns}


def test_mssql_migration_uses_bounded_computed_key_and_included_json_columns() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.901e6c7bf9d4_index_attack_analytics")
    operations = MagicMock(spec=Operations)
    operations.get_bind.return_value.dialect.name = "mssql"
    operations.get_bind.return_value.execute.return_value.scalar_one.return_value = 0
    with patch.object(migration, "op", operations), patch.object(migration, "_backfill") as backfill:
        migration.upgrade()
    backfill.assert_called_once()
    computed, evaluation_column = (call.args[1] for call in operations.add_column.call_args_list)
    assert computed.type.length == evaluation_column.type.length == 64
    assert "CONVERT(varchar(64)" in str(computed.computed.sqltext)
    assert "JSON_VALUE" in str(computed.computed.sqltext)
    assert "OPENJSON" not in str(computed.computed.sqltext)
    facts, labels, evaluation = operations.create_index.call_args_list
    assert facts.kwargs["mssql_include"] == ["targeted_harm_categories"]
    assert labels.kwargs["mssql_include"] == ["labels"]
    assert "targeted_harm_categories" not in facts.args[2]
    assert "labels" not in labels.args[2]
    assert evaluation.args[2] == ["objective_target_eval_hash_v1", "outcome"]


def test_mssql_evaluation_migration_uses_a_bounded_index_and_one_result_source() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.901e6c7bf9d4_index_attack_analytics")
    operations = MagicMock(spec=Operations)
    operations.get_bind.return_value.dialect.name = "mssql"
    operations.get_bind.return_value.execute.return_value.scalar_one.return_value = 0
    with patch.object(migration, "op", operations), patch.object(migration, "_backfill") as backfill:
        migration.upgrade()
    column = operations.add_column.call_args.args[1]
    assert isinstance(column.type, String)
    assert column.type.length == 64
    assert column.nullable
    backfill.assert_called_once()
    assert operations.create_index.call_args.args[2] == ["objective_target_eval_hash_v1", "outcome"]

    connection = MagicMock(spec=Connection)
    connection.execute.return_value.mappings.return_value.all.return_value = []
    migration._backfill(connection=connection)
    statement = connection.execute.call_args.args[0]
    sql = str(statement.compile(dialect=mssql.dialect()))
    assert sql.count("FROM [AttackResultEntries]") == 1
    assert "LEFT OUTER JOIN [TargetIdentifiers]" in sql
    assert "ScoreEntries" not in sql and "PromptMemoryEntries" not in sql
