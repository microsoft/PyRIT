# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from alembic import context
from sqlalchemy.engine import Connection

from pyrit.memory.memory_models import Base
from pyrit.memory.migration import PYRIT_MEMORY_ALEMBIC_VERSION_TABLE

config = context.config
connection: Connection | None = config.attributes.get("connection")
target_metadata = Base.metadata

if connection is None:
    raise RuntimeError("No connection found for Alembic migration")

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_type=True,
    version_table=PYRIT_MEMORY_ALEMBIC_VERSION_TABLE,
)
with context.begin_transaction():
    context.run_migrations()
