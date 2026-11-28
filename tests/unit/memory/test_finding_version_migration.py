# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.migration import check_schema_migrations


def test_finding_version_upgrade_preserves_existing_rows_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = Config()
            config.set_main_option(
                "script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic")
            )
            config.attributes["connection"] = connection
            config.attributes["version_table"] = "pyrit_memory_alembic_version"
            command.upgrade(config, "d43b1bcf9c56")
            connection.execute(
                sa.text(
                    "INSERT INTO OperationEntries (id, name, name_key, created_at, description) "
                    "VALUES ('operation', 'Case', 'case', '2026-11-25', '')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO FindingEntries (id, operation_id, title, severity, field_values, created_at) "
                    "VALUES ('finding', 'operation', 'Original', 'low', '{\"description\":\"Notes\"}', '2026-11-25')"
                )
            )
            before = connection.execute(sa.text('SELECT * FROM "FindingEntries"')).mappings().one()
            command.upgrade(config, "head")
            after = connection.execute(sa.text('SELECT * FROM "FindingEntries"')).mappings().one()
            assert dict(after) == {**before, "version": 1}
        check_schema_migrations(engine=engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "d43b1bcf9c56")
            assert dict(connection.execute(sa.text('SELECT * FROM "FindingEntries"')).mappings().one()) == before
    finally:
        engine.dispose()


def test_finding_version_sql_server_upgrade_initializes_existing_rows() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = importlib.import_module("pyrit.memory.alembic.versions.8c2d4e6f0a13_version_finding_edits")
    with Operations.context(context):
        migration.upgrade()
    assert "ADD version INTEGER NOT NULL DEFAULT '1'" in output.getvalue()
