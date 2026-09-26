# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.schema import Index, SchemaItem

from pyrit.memory.memory_models import Base
from pyrit.memory.migration import PYRIT_MEMORY_ALEMBIC_VERSION_TABLE

config = context.config
connection: Connection | None = config.attributes.get("connection")
target_metadata = Base.metadata

if connection is None:
    raise RuntimeError("No connection found for Alembic migration")


def _include_object(
    obj: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: SchemaItem | None
) -> bool:
    """
    Keep dialect-specific metadata indexes out of other backends' schema comparisons.

    Returns:
        bool: Whether the object applies to this backend.
    """
    if isinstance(obj, Index) and not reflected:
        dialect = obj.info.get("dialect")
        return dialect is None or dialect == context.get_context().dialect.name
    return True


context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_type=True,
    include_object=_include_object,
    version_table=PYRIT_MEMORY_ALEMBIC_VERSION_TABLE,
)
with context.begin_transaction():
    context.run_migrations()
