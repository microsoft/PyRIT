# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from pyrit.memory import SeedEntry
from pyrit.models import AttackTechniqueSeedGroup, SeedIdentifier, SeedObjective, SeedPrompt, SeedSimulatedConversation


def _config_for(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes["connection"] = connection
    return config


def _backfill_migration() -> ModuleType:
    return importlib.import_module(
        "pyrit.memory.alembic.versions.a8f3c1d9e620_preserve_legacy_simulation_technique_flags"
    )


def test_seed_is_general_technique_migration_preserves_legacy_rows() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.e7a1b3c5d9f0_add_seed_is_general_technique")
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    'CREATE TABLE "SeedPromptEntries" (id INTEGER PRIMARY KEY, value TEXT, seed_type TEXT NOT NULL)'
                )
            )
            connection.execute(
                sa.text(
                    'INSERT INTO "SeedPromptEntries" VALUES '
                    "(1, 'legacy', 'prompt'), (2, 'simulation', 'simulated_conversation'), "
                    "(3, 'objective', 'objective')"
                )
            )
            operations = Operations(
                MigrationContext.configure(connection, opts={"pyrit_applied_revisions": {"e7a1b3c5d9f0"}})
            )
            with patch.object(migration, "op", operations):
                migration.upgrade()
            backfill = _backfill_migration()
            with patch.object(backfill, "op", operations):
                backfill.upgrade()
            rows = connection.execute(
                sa.text('SELECT id, value, is_general_technique FROM "SeedPromptEntries" ORDER BY id')
            ).fetchall()
            assert [tuple(row) for row in rows] == [(1, "legacy", 0), (2, "simulation", 1), (3, "objective", 0)]

            connection.execute(
                sa.text(
                    'INSERT INTO "SeedPromptEntries" (id, value, seed_type, is_general_technique) '
                    "VALUES (4, 'general', 'prompt', 1)"
                )
            )
            row2 = connection.execute(
                sa.text('SELECT id, value, is_general_technique FROM "SeedPromptEntries" WHERE id = 4')
            ).one()
            assert tuple(row2) == (4, "general", 1)

            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            assert "is_general_technique" in columns
            assert not columns["is_general_technique"]["nullable"]

            with patch.object(backfill, "op", operations):
                backfill.downgrade()
            with patch.object(migration, "op", operations):
                migration.downgrade()
            assert connection.execute(sa.text('SELECT id, value FROM "SeedPromptEntries" ORDER BY id')).fetchall() == [
                (1, "legacy"),
                (2, "simulation"),
                (3, "objective"),
                (4, "general"),
            ]
            assert "is_general_technique" not in {
                column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")
            }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "seed",
    [
        SeedPrompt(value="Legacy prompt.", added_by="operator"),
        SeedObjective(value="Legacy objective.", added_by="operator"),
        SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation."),
            added_by="operator",
        ),
    ],
)
def test_seed_migration_preserves_legacy_reconstruction(
    seed: SeedPrompt | SeedObjective | SeedSimulatedConversation,
) -> None:
    entry = SeedEntry(entry=seed)
    values = {
        attribute.columns[0].name: getattr(entry, attribute.key)
        for attribute in sa.inspect(SeedEntry).column_attrs
        if attribute.columns[0].name != "is_general_technique"
    }
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, "c8d3e5f7a901")
            columns = {column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            legacy_table = sa.Table(
                "SeedPromptEntries",
                sa.MetaData(),
                *(
                    sa.Column(column.name, column.type)
                    for column in SeedEntry.__table__.columns
                    if column.name in columns
                ),
            )
            connection.execute(
                legacy_table.insert().values({key: value for key, value in values.items() if key in columns})
            )
            command.upgrade(config, "head")
            with Session(bind=connection) as session:
                stored = session.get(SeedEntry, seed.id)
                assert stored is not None
                restored = stored.get_seed()
                assert restored.is_general_technique is seed.is_general_technique
                assert stored.get_seed_record().is_general_technique is seed.is_general_technique
                assert SeedIdentifier.from_seed(restored).hash == SeedIdentifier.from_seed(seed).hash
                [filtered] = session.scalars(
                    sa.select(SeedEntry).where(SeedEntry.is_general_technique.is_(seed.is_general_technique))
                ).all()
                assert filtered.id == seed.id
                if isinstance(restored, SeedSimulatedConversation):
                    assert AttackTechniqueSeedGroup(seeds=[restored]).seeds[0].is_general_technique is True
    finally:
        engine.dispose()


@pytest.mark.parametrize("starting_revision", ["e7a1b3c5d9f0", "f4b8c2e6a190"])
@pytest.mark.parametrize("is_general_technique", [False, True])
def test_seed_backfill_preserves_existing_explicit_flags(*, starting_revision: str, is_general_technique: bool) -> None:
    seed = SeedSimulatedConversation(
        adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation."),
        is_general_technique=is_general_technique,
        added_by="operator",
    )
    entry = SeedEntry(entry=seed)
    values = {
        attribute.columns[0].name: getattr(entry, attribute.key) for attribute in sa.inspect(SeedEntry).column_attrs
    }
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, starting_revision)
            columns = {column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            historical_table = sa.Table(
                "SeedPromptEntries",
                sa.MetaData(),
                *(
                    sa.Column(column.name, column.type)
                    for column in SeedEntry.__table__.columns
                    if column.name in columns
                ),
            )
            connection.execute(
                historical_table.insert().values({key: value for key, value in values.items() if key in columns})
            )
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            with Session(bind=connection) as session:
                stored = session.get(SeedEntry, seed.id)
                assert stored is not None
                restored = stored.get_seed()
                assert restored.is_general_technique is is_general_technique
                assert SeedIdentifier.from_seed(restored).hash == SeedIdentifier.from_seed(seed).hash
    finally:
        engine.dispose()


def test_seed_backfill_requires_provenance() -> None:
    migration = _backfill_migration()
    context = MigrationContext.configure(dialect_name="sqlite")
    with patch.object(migration, "op", Operations(context)):
        with pytest.raises(RuntimeError, match="requires revision provenance"):
            migration.upgrade()


def test_seed_is_general_technique_migration_uses_sql_server_bit() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.e7a1b3c5d9f0_add_seed_is_general_technique")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql",
        opts={"as_sql": True, "output_buffer": output, "pyrit_applied_revisions": {"e7a1b3c5d9f0"}},
    )
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
    backfill = _backfill_migration()
    with patch.object(backfill, "op", Operations(context)):
        backfill.upgrade()
        backfill.downgrade()
    with patch.object(migration, "op", Operations(context)):
        migration.downgrade()
    sql = output.getvalue()
    assert "ADD is_general_technique BIT NOT NULL DEFAULT 0" in sql
    assert "UPDATE [SeedPromptEntries] SET is_general_technique=1" in sql
    assert "WHERE [SeedPromptEntries].seed_type = 'simulated_conversation'" in sql
    assert "DROP COLUMN is_general_technique" in sql
    assert SeedEntry.__table__.c.is_general_technique.type.python_type is bool
