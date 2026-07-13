# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist converter identifiers as content-addressed rows.

Revision ID: c8e1f3a5b7d9
Revises: b7d9f1a3c5e7
Create Date: 2026-07-13 15:00:00.000000
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.types import TypeDecorator, Uuid

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
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("class_module", sa.String(), nullable=False),
        sa.Column("identifier_json", sa.JSON(), nullable=False),
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
    _backfill_converter_identifiers()


def downgrade() -> None:
    """Revert this schema upgrade."""
    op.drop_table("PromptConverterIdentifiers")
    op.drop_table("ConverterIdentifiers")


def _backfill_converter_identifiers() -> None:
    """Materialize converter graphs and prompt associations from retained JSON."""
    from pyrit.models import ComponentIdentifier, ConverterIdentifier, TargetIdentifier

    bind = op.get_bind()
    prompt_rows = bind.execute(
        sa.text(
            'SELECT id, converter_identifiers, pyrit_version FROM "PromptMemoryEntries" '
            "WHERE converter_identifiers IS NOT NULL"
        )
    ).fetchall()
    converter_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "ConverterIdentifiers"'))}
    target_hashes = {row[0] for row in bind.execute(sa.text('SELECT hash FROM "TargetIdentifiers"'))}

    target_insert = sa.text(
        'INSERT INTO "TargetIdentifiers" '
        "(hash, class_name, class_module, identifier_json, endpoint, model_name, underlying_model_name, "
        "temperature, top_p, max_requests_per_minute, supported_auth_modes, pyrit_version) "
        "VALUES (:hash, :class_name, :class_module, :identifier_json, :endpoint, :model_name, "
        ":underlying_model_name, :temperature, :top_p, :max_requests_per_minute, "
        ":supported_auth_modes, :pyrit_version)"
    )
    target_edge_insert = sa.text(
        'INSERT INTO "TargetIdentifierChildren" (parent_hash, position, child_hash) '
        "VALUES (:parent_hash, :position, :child_hash)"
    )
    converter_insert = sa.text(
        'INSERT INTO "ConverterIdentifiers" '
        "(hash, class_name, class_module, identifier_json, supported_input_types, supported_output_types, "
        "converter_target_hash, sub_converter_hash, pyrit_version) "
        "VALUES (:hash, :class_name, :class_module, :identifier_json, :supported_input_types, "
        ":supported_output_types, :converter_target_hash, :sub_converter_hash, :pyrit_version)"
    )
    link_insert = sa.text(
        'INSERT INTO "PromptConverterIdentifiers" '
        "(prompt_memory_entry_id, position, converter_identifier_hash) "
        "VALUES (:prompt_memory_entry_id, :position, :converter_identifier_hash)"
    )

    def _insert_target(identifier: TargetIdentifier) -> None:
        if identifier.hash in target_hashes:
            return
        for child in identifier.targets:
            _insert_target(child)
        bind.execute(target_insert, _identifier_values(identifier))
        for position, child in enumerate(identifier.targets):
            bind.execute(
                target_edge_insert,
                {"parent_hash": identifier.hash, "position": position, "child_hash": child.hash},
            )
        target_hashes.add(identifier.hash)

    def _insert_converter(identifier: ConverterIdentifier) -> None:
        if identifier.hash in converter_hashes:
            return
        if identifier.converter_target is not None:
            _insert_target(identifier.converter_target)
        if identifier.sub_converter is not None:
            _insert_converter(identifier.sub_converter)
        bind.execute(converter_insert, _identifier_values(identifier))
        converter_hashes.add(identifier.hash)

    skipped = 0
    for prompt_id, stored_identifiers, pyrit_version in prompt_rows:
        try:
            values = json.loads(stored_identifiers) if isinstance(stored_identifiers, str) else stored_identifiers
            for position, stored_identifier in enumerate(values):
                stored_identifier["pyrit_version"] = pyrit_version
                identifier = ConverterIdentifier.from_component_identifier(
                    ComponentIdentifier.model_validate(stored_identifier)
                )
                _insert_converter(identifier)
                bind.execute(
                    link_insert,
                    {
                        "prompt_memory_entry_id": prompt_id,
                        "position": position,
                        "converter_identifier_hash": identifier.hash,
                    },
                )
        except (TypeError, ValueError, KeyError):
            skipped += 1
            logger.warning(f"ConverterIdentifiers backfill: could not reconstruct converters for prompt {prompt_id}")
    if skipped:
        logger.warning(f"ConverterIdentifiers backfill skipped {skipped} prompt row(s)")


def _identifier_values(identifier: Any) -> dict[str, Any]:
    """Return common and promoted values for a normalized identifier insert."""
    promoted_values = {
        name: json.dumps(value) if isinstance(value, (list, dict)) else value
        for name, value in identifier.promoted_scalar_values().items()
    }
    return {
        "hash": identifier.hash,
        "class_name": identifier.class_name,
        "class_module": identifier.class_module,
        "identifier_json": json.dumps(identifier.model_dump()),
        "pyrit_version": identifier.pyrit_version,
        **promoted_values,
        **{
            f"{field_name}_hash": child.hash if child is not None else None
            for field_name in identifier.promoted_child_field_names()
            if not isinstance((child := getattr(identifier, field_name)), list)
        },
    }
