# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run SQL Server migrations on a fresh database without Azure authentication."""

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from pyrit.memory.migration import (
    PYRIT_MEMORY_ALEMBIC_VERSION_TABLE,
    _make_config,
    check_schema_migrations,
    run_schema_migrations,
)


@contextmanager
def _temporary_database() -> Generator[sa.Engine, None, None]:
    """Create and remove an isolated database on the configured SQL Server."""
    url = os.environ.get("PYRIT_TEST_SQLSERVER_URL")
    if not url:
        raise ValueError("Set PYRIT_TEST_SQLSERVER_URL to a disposable SQL Server with CREATE DATABASE permission.")
    master_url = sa.engine.make_url(url)
    if master_url.drivername != "mssql+pyodbc" or master_url.database != "master":
        raise ValueError("PYRIT_TEST_SQLSERVER_URL must use mssql+pyodbc and the master database.")
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


def main() -> None:
    """Apply all migrations to a fresh database and validate the resulting schema."""
    with _temporary_database() as engine:
        with engine.connect() as connection:
            if sa.inspect(connection).get_table_names():
                raise RuntimeError("SQL Server migration validation requires a fresh database.")
        run_schema_migrations(engine=engine)
        check_schema_migrations(engine=engine)
        with engine.connect() as connection:
            heads = ScriptDirectory.from_config(_make_config(connection=connection)).get_heads()
            versions = (
                connection.execute(sa.text(f"SELECT version_num FROM {PYRIT_MEMORY_ALEMBIC_VERSION_TABLE}"))
                .scalars()
                .all()
            )
            if len(heads) != 1 or versions != heads:
                raise RuntimeError(f"Expected a single Alembic head {heads}; database revisions: {versions}")
    print("SQL Server migrations and schema validation passed.")


if __name__ == "__main__":
    main()
