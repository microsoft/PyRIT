# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Recompute frozen v1 objective-target keys after the model-less target correction.

Revision ``901e6c7bf9d4`` grouped every target without a model name (HTTP, Gandalf and
other non-LLM targets) of one class into a single key. The v1 rules now fall back to the
target content hash for those targets. Databases that already applied ``901e6c7bf9d4``
keep the old keys, because Alembic does not rerun an applied revision, so this revision
recomputes them with the same bounded backfill. Keys for targets with a model name come
out unchanged.

Revision ID: 3f6a9d2c8b1e
Revises: 901e6c7bf9d4
Create Date: 2026-10-13 09:00:00.000000
"""

import importlib
from collections.abc import Sequence

from alembic import op

revision: str = "3f6a9d2c8b1e"
down_revision: str | None = "901e6c7bf9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANALYTICS_REVISION = importlib.import_module("pyrit.memory.alembic.versions.901e6c7bf9d4_index_attack_analytics")


def upgrade() -> None:
    """Recompute every stored v1 objective-target key with the corrected rules."""
    _ANALYTICS_REVISION._backfill(connection=op.get_bind())


def downgrade() -> None:
    """
    Keep the corrected keys.

    The pre-correction keys merged distinct targets and can't be told apart again,
    so downgrading leaves the stored values as they are.
    """
