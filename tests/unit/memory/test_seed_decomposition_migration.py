# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
import uuid
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "pyrit.memory.alembic.versions.5d7f9b1c3e2a_add_seed_decomposition"


def test_seed_decomposition_migration_preserves_existing_rows() -> None:
    migration = importlib.import_module(MIGRATION)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    seeds = sa.Table(
        "SeedPromptEntries",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("value", sa.String, nullable=False),
        sa.Column("seed_type", sa.String, nullable=False),
    )
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                seeds.insert(),
                [
                    {"id": 1, "value": "legacy objective", "seed_type": "objective"},
                    {"id": 2, "value": "legacy prompt", "seed_type": "prompt"},
                ],
            )
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
            inspector = sa.inspect(connection)
            columns = {column["name"]: column for column in inspector.get_columns(seeds.name)}
            assert columns["parent_objective_id"]["nullable"]
            assert columns["paths"]["nullable"]
            assert isinstance(columns["paths"]["type"], sa.JSON)
            assert {"ix_SeedPromptEntries_parent_objective_id"} <= {
                index["name"] for index in inspector.get_indexes(seeds.name)
            }
            assert connection.execute(
                sa.text('SELECT parent_objective_id, paths FROM "SeedPromptEntries" ORDER BY id')
            ).all() == [(None, None), (None, None)]

            upgraded = sa.Table(seeds.name, sa.MetaData(), autoload_with=connection)
            payload = [{"id": str(uuid.uuid4()), "name": None, "description": "", "steps": [str(uuid.uuid4())]}]
            connection.execute(upgraded.update().where(upgraded.c.id == 1).values(paths=payload))
            assert connection.execute(sa.select(upgraded.c.paths).where(upgraded.c.id == 1)).scalar_one() == payload

            with patch.object(migration, "op", operations):
                migration.downgrade()
            remaining = {column["name"] for column in sa.inspect(connection).get_columns(seeds.name)}
            assert not {"parent_objective_id", "paths"} & remaining
            assert connection.execute(sa.select(seeds.c.value).order_by(seeds.c.id)).scalars().all() == [
                "legacy objective",
                "legacy prompt",
            ]
    finally:
        engine.dispose()


def test_seed_decomposition_migration_sql_server_uses_bounded_index_key() -> None:
    migration = importlib.import_module(MIGRATION)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "ALTER TABLE [SeedPromptEntries] ADD parent_objective_id UNIQUEIDENTIFIER NULL" in sql
    assert "ALTER TABLE [SeedPromptEntries] ADD paths NVARCHAR(max) NULL" in sql
    assert "CREATE INDEX [ix_SeedPromptEntries_parent_objective_id]" in sql
    assert "DROP INDEX [ix_SeedPromptEntries_parent_objective_id]" in sql
    assert "UPDATE" not in sql
