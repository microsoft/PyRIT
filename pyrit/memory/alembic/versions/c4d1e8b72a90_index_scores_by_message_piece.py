# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Index ScoreEntries by the message piece they score.

Revision ID: c4d1e8b72a90
Revises: d6de1045cca4
Create Date: 2026-11-09 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d1e8b72a90"
down_revision: str | None = "d6de1045cca4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Conversation, attack-summary and score reads look scores up by message piece id."""
    op.create_index(
        "ix_ScoreEntries_prompt_request_response_id",
        "ScoreEntries",
        ["prompt_request_response_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the score lookup index."""
    op.drop_index("ix_ScoreEntries_prompt_request_response_id", table_name="ScoreEntries")
