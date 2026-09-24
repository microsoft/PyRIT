# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_seed_conditions_migration_preserves_existing_rows() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.9b2d4f6a8c0e_add_seed_conditions")
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
            columns = {column["name"]: column for column in sa.inspect(connection).get_columns(seeds.name)}
            assert columns["conditions"]["nullable"]
            assert isinstance(columns["conditions"]["type"], sa.JSON)
            assert connection.execute(
                sa.text('SELECT conditions FROM "SeedPromptEntries" ORDER BY id')
            ).scalars().all() == [None, None]

            upgraded = sa.Table(seeds.name, sa.MetaData(), autoload_with=connection)
            payload = [{"condition_type": "answer_matches", "correct_answer": "Paris", "correct_answer_label": "A"}]
            connection.execute(upgraded.update().where(upgraded.c.id == 1).values(conditions=payload))
            stored = connection.execute(sa.select(upgraded.c.conditions).where(upgraded.c.id == 1)).scalar_one()
            assert stored == payload

            with patch.object(migration, "op", operations):
                migration.downgrade()
            assert "conditions" not in {column["name"] for column in sa.inspect(connection).get_columns(seeds.name)}
            assert connection.execute(sa.select(seeds.c.value).order_by(seeds.c.id)).scalars().all() == [
                "legacy objective",
                "legacy prompt",
            ]
    finally:
        engine.dispose()


def test_seed_conditions_migration_sql_server_uses_nullable_json_without_index() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.9b2d4f6a8c0e_add_seed_conditions")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "ALTER TABLE [SeedPromptEntries] ADD conditions NVARCHAR(max) NULL" in sql
    assert "ALTER TABLE [SeedPromptEntries] DROP COLUMN conditions" in sql
    assert "CREATE INDEX" not in sql
    assert "UPDATE" not in sql
