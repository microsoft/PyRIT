# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from pyrit.memory.memory_models import FindingEntry, FindingEvidenceEntry, OperationEntry
from pyrit.memory.migration import check_schema_migrations, run_schema_migrations

PREVIOUS_REVISION = "e7c4a2d9f013"
REVISION = "b5d7f9a1c3e6"
HTTP_REVISION = "d6de1045cca4"
MERGE_REVISION = "f8a6c2d4e901"
MAIN_REVISION = "d9e4f6a8b201"
INTEGRATION_REVISION = "a3f7c9e2b641"
INDEX = "ix_FindingEvidenceEntries_attack_result_id"


def _config(connection: sa.Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    if connection is not None:
        config.attributes["connection"] = connection
        config.attributes["version_table"] = "pyrit_memory_alembic_version"
    return config


def _evidence_indexes(connection: sa.Connection) -> dict[str, list[str]]:
    return {
        str(index["name"]): [str(column) for column in index["column_names"]]
        for index in sa.inspect(connection).get_indexes("FindingEvidenceEntries")
    }


def test_migration_branches_have_one_merge_head_without_rewriting_history() -> None:
    scripts = ScriptDirectory.from_config(_config())
    assert len(scripts.get_heads()) == 1
    assert set(scripts.get_revision(INTEGRATION_REVISION).down_revision) == {MERGE_REVISION, MAIN_REVISION}
    assert set(scripts.get_revision(MERGE_REVISION).down_revision) == {REVISION, HTTP_REVISION}
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS_REVISION
    assert scripts.get_revision(HTTP_REVISION).down_revision == PREVIOUS_REVISION


def test_migration_adds_and_drops_attack_result_index() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            assert INDEX not in _evidence_indexes(connection)
            command.upgrade(config, REVISION)
            assert _evidence_indexes(connection)[INDEX] == ["attack_result_id"]
            command.downgrade(config, PREVIOUS_REVISION)
            assert INDEX not in _evidence_indexes(connection)
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_migration_compiles_for_sql_server() -> None:
    migration = importlib.import_module(
        "pyrit.memory.alembic.versions.b5d7f9a1c3e6_index_finding_evidence_attack_result"
    )
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    assert f"CREATE INDEX [{INDEX}] ON [FindingEvidenceEntries] (attack_result_id)" in output.getvalue()


def test_model_declares_attack_result_index() -> None:
    indexes = {
        index.name: [column.name for column in index.columns] for index in FindingEvidenceEntry.__table__.indexes
    }
    assert indexes[INDEX] == ["attack_result_id"]


def _assert_merged_schema(connection: sa.Connection) -> None:
    context = MigrationContext.configure(connection, opts={"version_table": "pyrit_memory_alembic_version"})
    assert set(context.get_current_heads()) == set(ScriptDirectory.from_config(_config()).get_heads())
    assert _evidence_indexes(connection)[INDEX] == ["attack_result_id"]
    columns = {column["name"]: column for column in sa.inspect(connection).get_columns("TargetIdentifiers")}
    assert columns["http_request_fingerprint"]["nullable"] is True


def _seed_upgrade_rows(connection: sa.Connection) -> None:
    operation_id, finding_id = uuid4(), uuid4()
    timestamp = datetime.now(UTC)
    connection.execute(
        sa.insert(OperationEntry).values(
            id=operation_id, name="Preserved", name_key="preserved", created_at=timestamp, description=""
        )
    )
    connection.execute(
        sa.insert(FindingEntry).values(
            id=finding_id,
            operation_id=operation_id,
            title="Preserved finding",
            description="",
            severity="important",
            created_at=timestamp,
        )
    )
    connection.execute(
        sa.insert(FindingEvidenceEntry).values(
            id=uuid4(), finding_id=finding_id, attack_result_id=uuid4(), attached_at=timestamp
        )
    )
    targets = sa.Table("TargetIdentifiers", sa.MetaData(), autoload_with=connection)
    connection.execute(
        targets.insert().values(hash="a" * 64, class_name="HTTPTarget", identifier_json={"marker": "retained"})
    )


@pytest.mark.parametrize(
    "revisions",
    [
        (PREVIOUS_REVISION,),
        (REVISION,),
        (HTTP_REVISION,),
        (REVISION, HTTP_REVISION),
        (MAIN_REVISION,),
        (MERGE_REVISION, MAIN_REVISION),
    ],
    ids=["common-parent", "finding-index", "http-fingerprint", "both-branches", "main", "both-heads"],
)
def test_startup_upgrades_either_migration_branch_without_losing_data(revisions: tuple[str, ...]) -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            _seed_upgrade_rows(connection)
            evidence = dict(connection.execute(sa.select(FindingEvidenceEntry)).mappings().one())
            for revision in revisions:
                command.upgrade(config, revision)
            context = MigrationContext.configure(connection, opts={"version_table": "pyrit_memory_alembic_version"})
            assert set(context.get_current_heads()) == set(revisions)
            fingerprint = (
                "recorded-fingerprint"
                if any(revision in revisions for revision in (HTTP_REVISION, MERGE_REVISION, MAIN_REVISION))
                else None
            )
            if fingerprint is not None:
                connection.execute(
                    sa.text("UPDATE TargetIdentifiers SET http_request_fingerprint = :value"),
                    {"value": fingerprint},
                )

        run_schema_migrations(engine=engine, silent=True)
        run_schema_migrations(engine=engine, silent=True)
        with engine.connect() as connection:
            _assert_merged_schema(connection)
            assert dict(connection.execute(sa.select(FindingEvidenceEntry)).mappings().one()) == evidence
            assert connection.scalar(sa.select(FindingEntry.title)) == "Preserved finding"
            assert connection.scalar(sa.select(OperationEntry.name)) == "Preserved"
            targets = sa.Table("TargetIdentifiers", sa.MetaData(), autoload_with=connection)
            row = connection.execute(sa.select(targets)).mappings().one()
            assert row["hash"] == "a" * 64
            assert row["identifier_json"] == {"marker": "retained"}
            assert row["http_request_fingerprint"] == fingerprint
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_startup_creates_merged_schema_from_empty_database() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        run_schema_migrations(engine=engine, silent=True)
        with engine.connect() as connection:
            _assert_merged_schema(connection)
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_merge_downgrades_both_branches_and_upgrades_again() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, "head")
            _assert_merged_schema(connection)
            command.downgrade(config, PREVIOUS_REVISION)
            assert INDEX not in _evidence_indexes(connection)
            assert "http_request_fingerprint" not in {
                column["name"] for column in sa.inspect(connection).get_columns("TargetIdentifiers")
            }
        run_schema_migrations(engine=engine, silent=True)
        with engine.connect() as connection:
            _assert_merged_schema(connection)
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect_name", ["sqlite", "mssql"])
def test_merge_revision_adds_no_dialect_specific_ddl(dialect_name: str) -> None:
    migration = importlib.import_module(
        "pyrit.memory.alembic.versions.f8a6c2d4e901_merge_finding_index_and_http_fingerprint"
    )
    output = StringIO()
    context = MigrationContext.configure(dialect_name=dialect_name, opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    assert output.getvalue() == ""
