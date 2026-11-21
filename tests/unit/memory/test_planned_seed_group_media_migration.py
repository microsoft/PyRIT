# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import uuid
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.memory_models import ScenarioPlanSeedGroupEntry
from pyrit.memory.migration import check_schema_migrations
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt, SeedSimulatedConversation

REVISION = "cc714ae17d07"
PREVIOUS = "3e55dd099e38"
TABLE = "ScenarioPlanSeedGroupEntries"
POSITION_INDEX = "ix_ScenarioPlanSeedGroupEntries_scenario_result_id_position"
MEDIA_INDEX = "ix_ScenarioPlanSeedGroupEntries_has_media"
DELETE_TRIGGER = "trg_delete_scenario_plan_seed_groups"
TRIGGERS = sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes.update(connection=connection, version_table="pyrit_memory_alembic_version")
    return config


def _simulated(adversarial: SeedPrompt) -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[
            SeedObjective(value="objective"),
            SeedSimulatedConversation(
                adversarial_chat_system_prompt=adversarial, simulated_target_system_prompt=SeedPrompt(value="target")
            ),
        ]
    )


STORED_GROUPS = {
    "text": (AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="text")]), False),
    "media": (
        AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="/m/a.png", data_type="image_path")]),
        True,
    ),
    "simulated-text": (_simulated(SeedPrompt(value="adversarial")), False),
    "simulated-media": (_simulated(SeedPrompt(value="/m/b.png", data_type="image_path")), True),
}


def test_media_flag_migration_classifies_stored_groups_and_downgrades_without_losing_them() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            table = sa.Table(TABLE, sa.MetaData(), autoload_with=connection)
            result_id = str(uuid.uuid4())
            stored = {
                name: {
                    "id": str(uuid.uuid4()),
                    "scenario_result_id": result_id,
                    "dataset_name": name,
                    "position": position,
                    "seed_group": group.model_dump(mode="json"),
                }
                for position, (name, (group, _)) in enumerate(STORED_GROUPS.items())
            }
            connection.execute(table.insert(), list(stored.values()))

            command.upgrade(config, REVISION)
            flags = dict(connection.execute(sa.text(f'SELECT dataset_name, has_media FROM "{TABLE}"')).all())
            assert flags == {name: int(has_media) for name, (_, has_media) in STORED_GROUPS.items()}
            inspector = sa.inspect(connection)
            assert {index["name"] for index in inspector.get_indexes(TABLE)} == {POSITION_INDEX, MEDIA_INDEX}
            assert not {column["name"]: column for column in inspector.get_columns(TABLE)}["has_media"]["nullable"]
            assert DELETE_TRIGGER in connection.scalars(TRIGGERS).all()

            command.downgrade(config, PREVIOUS)
            inspector = sa.inspect(connection)
            assert "has_media" not in {column["name"] for column in inspector.get_columns(TABLE)}
            assert {index["name"] for index in inspector.get_indexes(TABLE)} == {POSITION_INDEX}
            assert DELETE_TRIGGER in connection.scalars(TRIGGERS).all()
            assert [key["referred_table"] for key in inspector.get_foreign_keys(TABLE)] == ["ScenarioResultEntries"]
            table = sa.Table(TABLE, sa.MetaData(), autoload_with=connection)
            assert {row["dataset_name"]: dict(row) for row in connection.execute(sa.select(table)).mappings()} == stored
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_media_flag_migration_compiles_for_sql_server() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.cc714ae17d07_flag_planned_seed_group_media")
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context), patch.object(migration, "_backfill_has_media") as backfill:
        migration.upgrade()
        migration.downgrade()
    backfill.assert_called_once()
    sql = output.getvalue()
    assert f"ALTER TABLE [{TABLE}] ADD has_media BIT NULL" in sql
    assert f"ALTER TABLE [{TABLE}] ALTER COLUMN has_media BIT NOT NULL" in sql
    assert f"CREATE INDEX [{MEDIA_INDEX}] ON [{TABLE}] (has_media)" in sql
    assert f"DROP INDEX [{MEDIA_INDEX}] ON [{TABLE}]" in sql
    assert f"ALTER TABLE [{TABLE}] DROP COLUMN has_media" in sql
    assert MEDIA_INDEX in {index.name for index in ScenarioPlanSeedGroupEntry.__table__.indexes}
