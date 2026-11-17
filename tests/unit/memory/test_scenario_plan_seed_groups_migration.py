# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from pyrit.memory.memory_models import ScenarioPlanSeedGroupEntry
from pyrit.memory.migration import check_schema_migrations

REVISION = "c7d1e9a3b5f2"
PREVIOUS = "a3f7c9e2b641"
TABLE = "ScenarioPlanSeedGroupEntries"


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes.update(connection=connection, version_table="pyrit_memory_alembic_version")
    return config


def test_scenario_plan_seed_groups_migration_upgrades_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            assert TABLE not in sa.inspect(connection).get_table_names()
            command.upgrade(config, REVISION)
            inspector = sa.inspect(connection)
            assert {index["name"] for index in inspector.get_indexes(TABLE)} == {
                "ix_ScenarioPlanSeedGroupEntries_scenario_result_id_position",
                "ix_ScenarioPlanSeedGroupEntries_has_media",
            }
            assert [key["referred_table"] for key in inspector.get_foreign_keys(TABLE)] == ["ScenarioResultEntries"]
            command.downgrade(config, PREVIOUS)
            assert TABLE not in sa.inspect(connection).get_table_names()
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_scenario_plan_seed_groups_schema_compiles_for_sql_server() -> None:
    dialect = mssql.dialect()
    table = ScenarioPlanSeedGroupEntry.__table__
    assert "CREATE TABLE [ScenarioPlanSeedGroupEntries]" in str(CreateTable(table).compile(dialect=dialect))
    for index in table.indexes:
        CreateIndex(index).compile(dialect=dialect)
