# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
pre-alembic release schema.

Revision ID: ab8f2c1a9d07
Revises:
Create Date: 2026-04-01 00:00:00.000000
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, Uuid


class _CustomUUID(TypeDecorator[uuid.UUID]):
    """
    Frozen copy of CustomUUID kept here so this revision stays self-contained.

    This class is embedded in the migration script rather than imported to ensure
    the migration remains reproducible and independent of future changes to the
    main CustomUUID implementation in memory_models.py. Any future modifications
    to CustomUUID must NOT affect this frozen version.
    """

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


# revision identifiers, used by Alembic.
revision: str = "ab8f2c1a9d07"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Static metadata representing the initial pre-Alembic baseline schema.
# Used by upgrade/downgrade below, and also imported by migration.py to
# validate unversioned legacy databases without spinning up a temp DB.
# ---------------------------------------------------------------------------
INITIAL_METADATA = sa.MetaData()

sa.Table(
    "PromptMemoryEntries",
    INITIAL_METADATA,
    sa.Column("id", _CustomUUID(), nullable=False, primary_key=True),
    sa.Column("role", sa.String(), nullable=False),
    sa.Column("conversation_id", sa.String(), nullable=False),
    sa.Column("sequence", sa.INTEGER(), nullable=False),
    sa.Column("timestamp", sa.DateTime(), nullable=False),
    sa.Column("labels", sa.JSON(), nullable=False),
    sa.Column("prompt_metadata", sa.JSON(), nullable=False),
    sa.Column("targeted_harm_categories", sa.JSON(), nullable=True),
    sa.Column("converter_identifiers", sa.JSON(), nullable=True),
    sa.Column("prompt_target_identifier", sa.JSON(), nullable=False),
    sa.Column("attack_identifier", sa.JSON(), nullable=False),
    sa.Column("response_error", sa.String(), nullable=True),
    sa.Column("original_value_data_type", sa.String(), nullable=False),
    sa.Column("original_value", sa.Unicode(), nullable=False),
    sa.Column("original_value_sha256", sa.String(), nullable=True),
    sa.Column("converted_value_data_type", sa.String(), nullable=False),
    sa.Column("converted_value", sa.Unicode(), nullable=True),
    sa.Column("converted_value_sha256", sa.String(), nullable=True),
    sa.Column("original_prompt_id", _CustomUUID(), nullable=False),
    sa.Column("pyrit_version", sa.String(), nullable=True),
)

sa.Table(
    "ScenarioResultEntries",
    INITIAL_METADATA,
    sa.Column("id", _CustomUUID(), nullable=False, primary_key=True),
    sa.Column("scenario_name", sa.String(), nullable=False),
    sa.Column("scenario_description", sa.Unicode(), nullable=True),
    sa.Column("scenario_version", sa.INTEGER(), nullable=False),
    sa.Column("pyrit_version", sa.String(), nullable=False),
    sa.Column("scenario_init_data", sa.JSON(), nullable=True),
    sa.Column("objective_target_identifier", sa.JSON(), nullable=False),
    sa.Column("objective_scorer_identifier", sa.JSON(), nullable=True),
    sa.Column("scenario_run_state", sa.String(), nullable=False),
    sa.Column("attack_results_json", sa.Unicode(), nullable=False),
    sa.Column("labels", sa.JSON(), nullable=True),
    sa.Column("number_tries", sa.INTEGER(), nullable=False),
    sa.Column("completion_time", sa.DateTime(), nullable=False),
    sa.Column("timestamp", sa.DateTime(), nullable=False),
)

sa.Table(
    "SeedPromptEntries",
    INITIAL_METADATA,
    sa.Column("id", _CustomUUID(), nullable=False, primary_key=True),
    sa.Column("value", sa.Unicode(), nullable=False),
    sa.Column("value_sha256", sa.Unicode(), nullable=True),
    sa.Column("data_type", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=True),
    sa.Column("dataset_name", sa.String(), nullable=True),
    sa.Column("harm_categories", sa.JSON(), nullable=True),
    sa.Column("description", sa.String(), nullable=True),
    sa.Column("authors", sa.JSON(), nullable=True),
    sa.Column("groups", sa.JSON(), nullable=True),
    sa.Column("source", sa.String(), nullable=True),
    sa.Column("date_added", sa.DateTime(), nullable=False),
    sa.Column("added_by", sa.String(), nullable=False),
    sa.Column("prompt_metadata", sa.JSON(), nullable=True),
    sa.Column("parameters", sa.JSON(), nullable=True),
    sa.Column("prompt_group_id", _CustomUUID(), nullable=True),
    sa.Column("sequence", sa.INTEGER(), nullable=True),
    sa.Column("role", sa.String(), nullable=True),
    sa.Column("seed_type", sa.String(), nullable=False),
)

sa.Table(
    "EmbeddingData",
    INITIAL_METADATA,
    sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
    sa.Column(
        "embedding",
        sa.ARRAY(sa.Float()).with_variant(sa.JSON(), "mssql").with_variant(sa.JSON(), "sqlite"),
        nullable=True,
    ),
    sa.Column("embedding_type_name", sa.String(), nullable=True),
    sa.ForeignKeyConstraint(["id"], ["PromptMemoryEntries.id"]),
)

sa.Table(
    "ScoreEntries",
    INITIAL_METADATA,
    sa.Column("id", _CustomUUID(), nullable=False, primary_key=True),
    sa.Column("score_value", sa.String(), nullable=False),
    sa.Column("score_value_description", sa.String(), nullable=True),
    sa.Column("score_type", sa.String(), nullable=False),
    sa.Column("score_category", sa.JSON(), nullable=True),
    sa.Column("score_rationale", sa.String(), nullable=True),
    sa.Column("score_metadata", sa.JSON(), nullable=False),
    sa.Column("scorer_class_identifier", sa.JSON(), nullable=False),
    sa.Column("prompt_request_response_id", _CustomUUID(), nullable=True),
    sa.Column("timestamp", sa.DateTime(), nullable=False),
    sa.Column("task", sa.String(), nullable=True),
    sa.Column("objective", sa.String(), nullable=True),
    sa.Column("pyrit_version", sa.String(), nullable=True),
    sa.ForeignKeyConstraint(["prompt_request_response_id"], ["PromptMemoryEntries.id"]),
)

sa.Table(
    "AttackResultEntries",
    INITIAL_METADATA,
    sa.Column("id", _CustomUUID(), nullable=False, primary_key=True),
    sa.Column("conversation_id", sa.String(), nullable=False),
    sa.Column("objective", sa.Unicode(), nullable=False),
    sa.Column("attack_identifier", sa.JSON(), nullable=False),
    sa.Column("atomic_attack_identifier", sa.JSON(), nullable=True),
    sa.Column("objective_sha256", sa.String(), nullable=True),
    sa.Column("last_response_id", _CustomUUID(), nullable=True),
    sa.Column("last_score_id", _CustomUUID(), nullable=True),
    sa.Column("executed_turns", sa.INTEGER(), nullable=False),
    sa.Column("execution_time_ms", sa.INTEGER(), nullable=False),
    sa.Column("outcome", sa.String(), nullable=False),
    sa.Column("outcome_reason", sa.String(), nullable=True),
    sa.Column("attack_metadata", sa.JSON(), nullable=True),
    sa.Column("pruned_conversation_ids", sa.JSON(), nullable=True),
    sa.Column("adversarial_chat_conversation_ids", sa.JSON(), nullable=True),
    sa.Column("timestamp", sa.DateTime(), nullable=False),
    sa.Column("pyrit_version", sa.String(), nullable=True),
    sa.ForeignKeyConstraint(["last_response_id"], ["PromptMemoryEntries.id"]),
    sa.ForeignKeyConstraint(["last_score_id"], ["ScoreEntries.id"]),
)


def upgrade() -> None:
    """Apply this schema upgrade."""
    INITIAL_METADATA.create_all(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    """Revert this schema upgrade."""
    INITIAL_METADATA.drop_all(op.get_bind(), checkfirst=False)
