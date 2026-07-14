# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist converter identifiers as content-addressed rows.

Revision ID: c8e1f3a5b7d9
Revises: b7d9f1a3c5e7
Create Date: 2026-07-13 15:00:00.000000
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.types import TypeDecorator, Uuid

from pyrit.memory.alembic.identifier_backfill import (
    IdentifierGraphInserter,
    load_identifier_list,
    run_best_effort_backfill,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect


class _CustomUUID(TypeDecorator[uuid.UUID]):
    """Frozen UUID type matching ``PromptMemoryEntries.id`` across dialects."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(CHAR(36))
        return dialect.type_descriptor(Uuid())

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        return str(value) if value is not None else None

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


revision: str = "c8e1f3a5b7d9"
down_revision: str | None = "b7d9f1a3c5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ConverterIdentifiers",
        sa.Column("hash", sa.String(64), primary_key=True, nullable=False),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("class_module", sa.String(), nullable=True),
        sa.Column("identifier_json", sa.JSON(), nullable=True),
        sa.Column("supported_input_types", sa.JSON(), nullable=True),
        sa.Column("supported_output_types", sa.JSON(), nullable=True),
        sa.Column("converter_target_hash", sa.String(64), nullable=True),
        sa.Column("sub_converter_hash", sa.String(64), nullable=True),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["converter_target_hash"],
            ["TargetIdentifiers.hash"],
            name="fk_converter_identifiers_converter_target_hash",
        ),
        sa.ForeignKeyConstraint(
            ["sub_converter_hash"],
            ["ConverterIdentifiers.hash"],
            name="fk_converter_identifiers_sub_converter_hash",
        ),
    )
    op.create_table(
        "PromptConverterIdentifiers",
        sa.Column("prompt_memory_entry_id", _CustomUUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("converter_identifier_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_memory_entry_id"],
            ["PromptMemoryEntries.id"],
            name="fk_prompt_converter_identifiers_prompt_memory_entry_id",
        ),
        sa.ForeignKeyConstraint(
            ["converter_identifier_hash"],
            ["ConverterIdentifiers.hash"],
            name="fk_prompt_converter_identifiers_converter_identifier_hash",
        ),
        sa.PrimaryKeyConstraint("prompt_memory_entry_id", "position"),
    )
    bind = op.get_bind()
    run_best_effort_backfill(bind=bind, name="ConverterIdentifiers", backfill=_backfill_converter_identifiers)


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_table("PromptConverterIdentifiers")
    op.drop_table("ConverterIdentifiers")


def _backfill_converter_identifiers() -> None:
    """Materialize converter graphs and prompt associations from retained JSON."""
    bind = op.get_bind()
    prompt_rows = bind.execute(
        sa.text(
            'SELECT id, converter_identifiers, pyrit_version FROM "PromptMemoryEntries" '
            "WHERE converter_identifiers IS NOT NULL"
        )
    ).fetchall()
    link_insert = sa.text(
        'INSERT INTO "PromptConverterIdentifiers" '
        "(prompt_memory_entry_id, position, converter_identifier_hash) "
        "VALUES (:prompt_memory_entry_id, :position, :converter_identifier_hash)"
    )

    inserter = IdentifierGraphInserter(bind=bind)
    skipped = 0
    for prompt_id, stored_identifiers, pyrit_version in prompt_rows:
        try:
            for position, identifier in enumerate(load_identifier_list(stored_identifiers)):
                if identifier.get("pyrit_version") is None:
                    identifier = {**identifier, "pyrit_version": pyrit_version}
                identifier_hash = inserter.insert_converter(identifier)
                if identifier_hash:
                    bind.execute(
                        link_insert,
                        {
                            "prompt_memory_entry_id": prompt_id,
                            "position": position,
                            "converter_identifier_hash": identifier_hash,
                        },
                    )
        except Exception:
            skipped += 1
            logger.warning(
                f"ConverterIdentifiers backfill: could not reconstruct converters for prompt {prompt_id}",
                exc_info=True,
            )
    if skipped:
        logger.warning(f"ConverterIdentifiers backfill skipped {skipped} prompt row(s)")
