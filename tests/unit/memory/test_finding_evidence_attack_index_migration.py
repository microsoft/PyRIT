# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from pyrit.memory.memory_models import FindingEvidenceEntry
from pyrit.memory.migration import check_schema_migrations

PREVIOUS_REVISION = "e7c4a2d9f013"
REVISION = "b5d7f9a1c3e6"
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


def test_migration_is_the_single_head_after_score_participation() -> None:
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS_REVISION


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
