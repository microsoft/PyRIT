# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
${message}.

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = "${up_revision}"
down_revision: str | None = ${repr(down_revision).replace("'", '"')}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels).replace("'", '"')}
depends_on: str | Sequence[str] | None = ${repr(depends_on).replace("'", '"')}


def upgrade() -> None:
    """Apply this schema upgrade."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revert this schema upgrade."""
    ${downgrades if downgrades else "pass"}
