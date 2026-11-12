# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run real SQL Server DDL and data upgrades without Azure authentication."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from pyrit.memory.migration import (
    PYRIT_MEMORY_ALEMBIC_VERSION_TABLE,
    _make_config,
    check_schema_migrations,
    run_schema_migrations,
)

pytestmark = pytest.mark.run_only_if_all_tests


@pytest.fixture
def sqlserver_engine() -> Iterator[sa.Engine]:
    url = os.environ.get("PYRIT_TEST_SQLSERVER_URL")
    if not url:
        pytest.fail("Set PYRIT_TEST_SQLSERVER_URL to a disposable SQL Server with CREATE DATABASE permission.")
    master_url = sa.engine.make_url(url)
    if master_url.drivername != "mssql+pyodbc" or master_url.database != "master":
        pytest.fail("PYRIT_TEST_SQLSERVER_URL must use mssql+pyodbc and the master database.")
    database = f"pyrit_migration_{uuid.uuid4().hex}"
    admin = sa.create_engine(master_url, isolation_level="AUTOCOMMIT", connect_args={"timeout": 5})
    engine = sa.create_engine(master_url.set(database=database), connect_args={"timeout": 5})
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE [{database}]")
        try:
            yield engine
        finally:
            engine.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                connection.exec_driver_sql(f"DROP DATABASE [{database}]")
    finally:
        admin.dispose()


def _assert_head(engine: sa.Engine) -> None:
    check_schema_migrations(engine=engine)
    with engine.connect() as connection:
        heads = ScriptDirectory.from_config(_make_config(connection=connection)).get_heads()
        assert len(heads) == 1
        assert (
            connection.execute(sa.text(f"SELECT version_num FROM {PYRIT_MEMORY_ALEMBIC_VERSION_TABLE}")).scalars().all()
            == heads
        )


def test_sqlserver_fresh_database_to_head(sqlserver_engine: sa.Engine) -> None:
    with sqlserver_engine.connect() as connection:
        assert not sa.inspect(connection).get_table_names()
    run_schema_migrations(engine=sqlserver_engine)
    _assert_head(sqlserver_engine)


def test_sqlserver_existing_schema_to_head(sqlserver_engine: sa.Engine) -> None:
    # This fixed baseline predates conversation/identifier backfills and dataset-name widening.
    with sqlserver_engine.begin() as connection:
        command.upgrade(_make_config(connection=connection), "9c8b7a6d5e4f")
        connection.exec_driver_sql(
            "ALTER TABLE SeedPromptEntries ALTER COLUMN dataset_name VARCHAR(MAX) COLLATE Latin1_General_100_CS_AS NULL"
        )
        connection.execute(
            sa.text(
                "INSERT INTO PromptMemoryEntries "
                "(id, role, conversation_id, sequence, timestamp, labels, prompt_metadata, "
                "prompt_target_identifier, attack_identifier, converter_identifiers, original_value_data_type, "
                "original_value, converted_value_data_type, original_prompt_id) "
                "VALUES (:id, 'user', :conversation, 0, CURRENT_TIMESTAMP, '{}', '{}', "
                ":target, '{}', '[]', 'text', 'retained prompt', 'text', :id)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "conversation": "00000000-0000-0000-0000-000000000002",
                "target": json.dumps(
                    {
                        "class_name": "OpenAIChatTarget",
                        "class_module": "pyrit.prompt_target",
                        "hash": "a" * 64,
                        "model_name": "migration-test",
                    }
                ),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO ScoreEntries "
                "(id, score_value, score_type, score_metadata, scorer_class_identifier, "
                "prompt_request_response_id, timestamp) "
                "VALUES (:id, 'true', 'true_false', '{}', '{}', :prompt, CURRENT_TIMESTAMP)"
            ),
            {"id": "00000000-0000-0000-0000-000000000005", "prompt": "00000000-0000-0000-0000-000000000001"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO AttackResultEntries "
                "(id, conversation_id, objective, attack_identifier, last_response_id, last_score_id, "
                "executed_turns, execution_time_ms, outcome, timestamp) "
                "VALUES (:id, :conversation, 'retained objective', '{}', :prompt, :score, "
                "1, 10, 'success', CURRENT_TIMESTAMP)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000006",
                "conversation": "00000000-0000-0000-0000-000000000002",
                "prompt": "00000000-0000-0000-0000-000000000001",
                "score": "00000000-0000-0000-0000-000000000005",
            },
        )
        command.upgrade(_make_config(connection=connection), "f8a2c6e0b419")
        connection.execute(
            sa.text(
                "INSERT INTO DatasetLifecycleEntries (id, dataset_name, kind, status, deleted_seeds) "
                "VALUES (:id, 'retained journal', 'delete', 'pending', 0)"
            ),
            {"id": "00000000-0000-0000-0000-000000000004"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO SeedPromptEntries "
                "(id, value, data_type, dataset_name, date_added, added_by, seed_type) "
                "VALUES (:id, 'retained seed', 'text', :name, CURRENT_TIMESTAMP, 'test', 'prompt')"
            ),
            {"id": "00000000-0000-0000-0000-000000000003", "name": "legacy-" + "x" * 300},
        )
        original_collation = connection.scalar(
            sa.text(
                "SELECT collation_name FROM sys.columns "
                "WHERE object_id = OBJECT_ID(N'SeedPromptEntries') AND name = N'dataset_name'"
            )
        )

    run_schema_migrations(engine=sqlserver_engine)
    _assert_head(sqlserver_engine)
    # A repeated startup must be harmless and must not rewrite retained data.
    run_schema_migrations(engine=sqlserver_engine)
    with sqlserver_engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT original_value FROM PromptMemoryEntries")) == "retained prompt"
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM Conversations")) == 1
        assert connection.scalar(sa.text("SELECT target_identifier_hash FROM Conversations")) is not None
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM TargetIdentifiers")) == 1
        assert connection.scalar(sa.text("SELECT score_value FROM ScoreEntries")) == "true"
        assert (
            connection.scalar(
                sa.text(
                    "SELECT COUNT(*) FROM AttackResultEntries a JOIN ScoreEntries s ON a.automated_score_id = s.id "
                    "JOIN PromptMemoryEntries p ON a.last_response_id = p.id WHERE a.objective = 'retained objective'"
                )
            )
            == 1
        )
        assert connection.scalar(sa.text("SELECT dataset_name FROM SeedPromptEntries")) == "legacy-" + "x" * 300
        assert connection.scalar(sa.text("SELECT dataset_name FROM DatasetLifecycleEntries")) == "retained journal"
        assert (
            connection.scalar(
                sa.text(
                    "SELECT collation_name FROM sys.columns "
                    "WHERE object_id = OBJECT_ID(N'SeedPromptEntries') AND name = N'dataset_name'"
                )
            )
            == original_collation
        )
        unicode_name = "\u6570\u636e-\u03b1"
        connection.execute(sa.text("UPDATE SeedPromptEntries SET dataset_name = :name"), {"name": unicode_name})
        connection.execute(sa.text("UPDATE DatasetLifecycleEntries SET dataset_name = :name"), {"name": unicode_name})
        assert connection.scalar(sa.text("SELECT dataset_name FROM SeedPromptEntries")) == unicode_name
        assert connection.scalar(sa.text("SELECT dataset_name FROM DatasetLifecycleEntries")) == unicode_name
