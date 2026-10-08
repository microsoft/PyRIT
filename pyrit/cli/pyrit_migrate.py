# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Upgrade the configured Azure SQL memory schema without starting the backend."""

import argparse
import asyncio
import logging
import sys
from typing import cast

logger = logging.getLogger(__name__)


def _validate_database_target(*, connection_string: str, expected_server: str, expected_database: str) -> None:
    from sqlalchemy.engine import make_url

    url = make_url(connection_string)
    if (
        url.drivername != "mssql+pyodbc"
        or not url.host
        or url.host.casefold() != expected_server.casefold()
        or url.database != expected_database
        or any(key.casefold() in {"odbc_connect", "dsn", "host", "server", "database"} for key in url.query)
    ):
        raise ValueError("SQL connection target does not match the deployment server and database.")


async def _migrate_async(*, config_file: str, expected_server: str, expected_database: str) -> None:
    from pyrit.backend.services.configuration_file_service import ConfigurationFileService
    from pyrit.common import default_values
    from pyrit.memory import AzureSQLMemory
    from pyrit.memory.migration import check_schema_migrations, run_schema_migrations
    from pyrit.setup.configuration_loader import ConfigurationLoader
    from pyrit.setup.environment_loading import load_environment_async

    source = ConfigurationFileService(config_file_value=config_file)
    async with source.resolve_async() as file:
        config = await asyncio.to_thread(ConfigurationLoader.load_with_overrides, config_file=file, strict=True)
    if config.memory_db_type != "azure_sql":
        raise ValueError("Migration requires memory_db_type: AzureSQL.")
    await load_environment_async(
        env_files=config.resolve_env_files(),
        env_akv_ref=config.resolve_env_akv_ref(),
        env_akv_strict=config.env_akv_strict,
        silent=True,
    )
    connection_string = default_values.get_required_value(
        env_var_name=AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING, passed_value=None
    )
    _validate_database_target(
        connection_string=connection_string, expected_server=expected_server, expected_database=expected_database
    )
    memory = cast(
        AzureSQLMemory,
        AzureSQLMemory(
            connection_string=connection_string, _defer_initialization=True, skip_schema_migration=True, silent=True
        ),
    )
    try:
        engine = memory.engine
        if engine is None:
            raise RuntimeError("Azure SQL engine was not created.")
        await asyncio.to_thread(run_schema_migrations, engine=engine, silent=True)
        await asyncio.to_thread(check_schema_migrations, engine=engine, silent=True)
    finally:
        await memory.dispose_engine_async()


def main(*, args: list[str] | None = None) -> int:
    """
    Run the schema upgrade and check.

    Returns:
        int: 0 when the schema is current, otherwise 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--expected-database", required=True)
    parsed = parser.parse_args(args)
    try:
        asyncio.run(
            _migrate_async(
                config_file=parsed.config_file,
                expected_server=parsed.expected_server,
                expected_database=parsed.expected_database,
            )
        )
    except Exception:
        logger.exception("Database migration failed.")
        return 1
    print("Database schema is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
