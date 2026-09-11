# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Persist scorer observations and their ordered score links.

Revision ID: 2c4e6a8b0d1f
Revises: a4c6e8f0b2d1
Create Date: 2026-09-02 14:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c4e6a8b0d1f"
down_revision: str | None = "a4c6e8f0b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.Uuid().with_variant(sa.CHAR(36), "sqlite")
_OBSERVATION_CONTENT_INDEX = "ix_ObservationEntries_scorable_content_id"
_OBSERVATION_SCORED_PIECE_INDEX = "ix_ObservationEntries_scored_message_piece_id"
_OBSERVATION_MESSAGE_PIECE_INDEX = "ix_ObservationMessagePieceEntries_message_piece_id"
_SCORE_OBSERVATION_INDEX = "ix_ScoreObservationEntries_observation_id"


def upgrade() -> None:
    """Apply this schema upgrade."""
    op.create_table(
        "ObservationEntries",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("source_identifier", sa.JSON(), nullable=False),
        sa.Column("acquisition", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("scorable", sa.JSON(), nullable=False),
        sa.Column("scorable_content_id", _UUID, nullable=True),
        sa.Column("scored_message_piece_id", _UUID, nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("pyrit_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scorable_content_id"],
            ["ScorableContentEntries.id"],
            name="fk_observation_entries_scorable_content_id",
        ),
        sa.ForeignKeyConstraint(
            ["scored_message_piece_id"],
            ["PromptMemoryEntries.id"],
            name="fk_observation_entries_scored_message_piece_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ObservationMessagePieceEntries",
        sa.Column("observation_id", _UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("message_piece_id", _UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["ObservationEntries.id"],
            name="fk_observation_message_pieces_observation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_piece_id"],
            ["PromptMemoryEntries.id"],
            name="fk_observation_message_pieces_message_piece_id",
        ),
        sa.PrimaryKeyConstraint("observation_id", "position"),
        sa.UniqueConstraint(
            "observation_id",
            "message_piece_id",
            name="uq_observation_message_pieces_piece",
        ),
    )
    op.create_table(
        "ScoreObservationEntries",
        sa.Column("score_id", _UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("observation_id", _UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["score_id"],
            ["ScoreEntries.id"],
            name="fk_score_observations_score_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["ObservationEntries.id"],
            name="fk_score_observations_observation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("score_id", "position"),
        sa.UniqueConstraint("score_id", "observation_id", name="uq_score_observations_observation"),
    )
    op.create_index(
        _OBSERVATION_CONTENT_INDEX,
        "ObservationEntries",
        ["scorable_content_id"],
    )
    op.create_index(
        _OBSERVATION_SCORED_PIECE_INDEX,
        "ObservationEntries",
        ["scored_message_piece_id"],
    )
    op.create_index(
        _OBSERVATION_MESSAGE_PIECE_INDEX,
        "ObservationMessagePieceEntries",
        ["message_piece_id"],
    )
    op.create_index(
        _SCORE_OBSERVATION_INDEX,
        "ScoreObservationEntries",
        ["observation_id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_evidence_triggers()


def downgrade() -> None:
    """Revert this schema upgrade."""
    if op.get_bind().dialect.name == "sqlite":
        _drop_sqlite_evidence_triggers()
    op.drop_index(_SCORE_OBSERVATION_INDEX, table_name="ScoreObservationEntries")
    op.drop_index(_OBSERVATION_MESSAGE_PIECE_INDEX, table_name="ObservationMessagePieceEntries")
    op.drop_index(_OBSERVATION_SCORED_PIECE_INDEX, table_name="ObservationEntries")
    op.drop_index(_OBSERVATION_CONTENT_INDEX, table_name="ObservationEntries")
    op.drop_table("ScoreObservationEntries")
    op.drop_table("ObservationMessagePieceEntries")
    op.drop_table("ObservationEntries")


def _create_sqlite_evidence_triggers() -> None:
    """Protect observation evidence on SQLite, where foreign keys are disabled by default."""
    prompt_reference = """
        EXISTS (
            SELECT 1 FROM "ObservationMessagePieceEntries"
            WHERE message_piece_id = OLD.id
        )
        OR EXISTS (
            SELECT 1 FROM "ObservationEntries"
            WHERE scored_message_piece_id = OLD.id
        )
    """
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER "trg_observation_prompt_immutable_update"
            BEFORE UPDATE ON "PromptMemoryEntries"
            WHEN {prompt_reference}
            BEGIN
                SELECT RAISE(ABORT, 'prompt entry is immutable observation evidence');
            END
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER "trg_observation_prompt_immutable_delete"
            BEFORE DELETE ON "PromptMemoryEntries"
            WHEN {prompt_reference}
            BEGIN
                SELECT RAISE(ABORT, 'prompt entry is immutable observation evidence');
            END
            """
        )
    )
    content_reference = """
        EXISTS (
            SELECT 1 FROM "ObservationEntries"
            WHERE scorable_content_id = OLD.id
        )
    """
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER "trg_observation_content_immutable_update"
            BEFORE UPDATE ON "ScorableContentEntries"
            WHEN {content_reference}
            BEGIN
                SELECT RAISE(ABORT, 'content entry is immutable observation evidence');
            END
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER "trg_observation_content_immutable_delete"
            BEFORE DELETE ON "ScorableContentEntries"
            WHEN {content_reference}
            BEGIN
                SELECT RAISE(ABORT, 'content entry is immutable observation evidence');
            END
            """
        )
    )


def _drop_sqlite_evidence_triggers() -> None:
    """Remove SQLite evidence-protection triggers before their referenced tables."""
    for trigger_name in (
        "trg_observation_prompt_immutable_update",
        "trg_observation_prompt_immutable_delete",
        "trg_observation_content_immutable_update",
        "trg_observation_content_immutable_delete",
    ):
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS "{trigger_name}"'))
