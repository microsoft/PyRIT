# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from pyrit.memory.migration import check_schema_migrations

REVISION = "d6de1045cca4"
PARENT_REVISION = "e7c4a2d9f013"


def _target_identifier_columns(connection: sa.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(connection).get_columns("TargetIdentifiers")}


def test_http_request_fingerprint_column_upgrades_and_downgrades() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision(REVISION).down_revision == PARENT_REVISION
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            config.attributes["version_table"] = "pyrit_memory_alembic_version"
            command.upgrade(config, PARENT_REVISION)
            assert "http_request_fingerprint" not in _target_identifier_columns(connection)
            command.upgrade(config, REVISION)
            assert "http_request_fingerprint" in _target_identifier_columns(connection)
            command.downgrade(config, PARENT_REVISION)
            assert "http_request_fingerprint" not in _target_identifier_columns(connection)
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()
