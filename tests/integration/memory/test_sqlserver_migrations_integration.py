# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run SQL Server migrations on a fresh database without Azure authentication."""

import os
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
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


def test_sqlserver_fresh_database_to_head(sqlserver_engine: sa.Engine) -> None:
    with sqlserver_engine.connect() as connection:
        assert not sa.inspect(connection).get_table_names()
    run_schema_migrations(engine=sqlserver_engine)
    check_schema_migrations(engine=sqlserver_engine)
    with sqlserver_engine.connect() as connection:
        heads = ScriptDirectory.from_config(_make_config(connection=connection)).get_heads()
        assert len(heads) == 1
        assert (
            connection.execute(sa.text(f"SELECT version_num FROM {PYRIT_MEMORY_ALEMBIC_VERSION_TABLE}")).scalars().all()
            == heads
        )
