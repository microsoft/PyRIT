# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Execute the Unicode dataset-name migration against a disposable local SQL Server.

Set RUN_ALL_TESTS=true and PYRIT_TEST_SQL_SERVER_URL to a local mssql+pyodbc URL
with a driver and disposable database. Each test rolls back its schema and data.
"""

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from pyrit.memory.alembic.versions import a9c3e7f1b205_unicode_seed_dataset_names as migration

pytestmark = pytest.mark.run_only_if_all_tests


@pytest.fixture
def sql_server_connection() -> Iterator[sa.Connection]:
    url = os.getenv("PYRIT_TEST_SQL_SERVER_URL")
    if not url:
        pytest.skip("Set PYRIT_TEST_SQL_SERVER_URL to a disposable local SQL Server database.")
    parsed = make_url(url)
    if parsed.drivername != "mssql+pyodbc" or parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Unicode migration tests require a local mssql+pyodbc URL.")
    engine = sa.create_engine(parsed)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("collation", ["SQL_Latin1_General_CP1_CI_AS", "Latin1_General_100_CS_AS"])
def test_unicode_migration_preserves_sql_server_dataset_names(
    *, sql_server_connection: sa.Connection, collation: str
) -> None:
    connection = sql_server_connection
    connection.execute(
        sa.text(
            "CREATE TABLE SeedPromptEntries "
            f"(id int PRIMARY KEY, dataset_name VARCHAR(MAX) COLLATE {collation} NULL); "
            "CREATE TABLE DatasetLifecycleEntries "
            "(id int PRIMARY KEY, dataset_name NVARCHAR(256) NOT NULL);"
        )
    )
    legacy_name = "legacy-" + "x" * 300
    connection.execute(
        sa.text("INSERT INTO SeedPromptEntries (id, dataset_name) VALUES (:id, :name)"),
        [{"id": 1, "name": legacy_name}, {"id": 2, "name": None}, {"id": 3, "name": "Case"}],
    )
    unicode_name = "\u6570\u636e"
    connection.execute(
        sa.text("INSERT INTO DatasetLifecycleEntries (id, dataset_name) VALUES (1, :name)"),
        {"name": unicode_name},
    )

    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()

    columns = connection.execute(
        sa.text(
            "SELECT OBJECT_NAME(object_id), TYPE_NAME(user_type_id), max_length, is_nullable, collation_name "
            "FROM sys.columns WHERE name = 'dataset_name' "
            "AND object_id IN (OBJECT_ID('SeedPromptEntries'), OBJECT_ID('DatasetLifecycleEntries'))"
        )
    ).all()
    assert {tuple(row) for row in columns} == {
        ("SeedPromptEntries", "nvarchar", -1, True, collation),
        ("DatasetLifecycleEntries", "nvarchar", 512, False, collation),
    }
    assert connection.execute(sa.text("SELECT dataset_name FROM SeedPromptEntries ORDER BY id")).scalars().all() == [
        legacy_name,
        None,
        "Case",
    ]
    assert connection.scalar(sa.text("SELECT dataset_name FROM DatasetLifecycleEntries")) == unicode_name
    assert connection.scalar(sa.text("SELECT COUNT(*) FROM SeedPromptEntries WHERE dataset_name = N'case'")) == (
        0 if collation.endswith("CS_AS") else 1
    )

    long_unicode_name = unicode_name + "x" * 300
    connection.execute(
        sa.text("INSERT INTO SeedPromptEntries (id, dataset_name) VALUES (4, :name)"),
        {"name": long_unicode_name},
    )
    bounded_unicode_name = unicode_name + "x" * 254
    connection.execute(
        sa.text("INSERT INTO DatasetLifecycleEntries (id, dataset_name) VALUES (2, :name)"),
        {"name": bounded_unicode_name},
    )
    assert connection.scalar(sa.text("SELECT dataset_name FROM SeedPromptEntries WHERE id = 4")) == long_unicode_name
    assert (
        connection.scalar(sa.text("SELECT dataset_name FROM DatasetLifecycleEntries WHERE id = 2"))
        == bounded_unicode_name
    )
    connection.execute(sa.text("CREATE INDEX ix_dataset_name ON DatasetLifecycleEntries (dataset_name)"))
    with pytest.raises(IntegrityError):
        connection.execute(sa.text("INSERT INTO DatasetLifecycleEntries (id, dataset_name) VALUES (3, NULL)"))
    with pytest.raises(DBAPIError, match="truncated"):
        connection.execute(
            sa.text("INSERT INTO DatasetLifecycleEntries (id, dataset_name) VALUES (3, :name)"),
            {"name": bounded_unicode_name + "x"},
        )


def test_unicode_migration_rejects_missing_sql_server_collation(sql_server_connection: sa.Connection) -> None:
    connection = sql_server_connection
    connection.execute(sa.text("CREATE TABLE SeedPromptEntries (dataset_name int NULL)"))
    with (
        Operations.context(MigrationContext.configure(connection)),
        pytest.raises(DBAPIError, match="Seed dataset-name collation is missing"),
    ):
        migration.upgrade()
