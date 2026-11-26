# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Nest retained legacy atomic attack identifiers without changing historical graphs.

Revision ID: f2c6a8e0b419
Revises: d43b1bcf9c56
Create Date: 2026-11-26 14:35:00.000000
"""

import copy
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f2c6a8e0b419"
down_revision: str | None = "d43b1bcf9c56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500


def _nested_identifier(*, document: Any) -> dict[str, Any] | None:
    """Return a rewritten legacy object, or None when no safe rewrite is needed."""
    if not isinstance(document, dict):
        return None
    children = document.get("children")
    if not isinstance(children, dict) or not isinstance(children.get("attack"), dict):
        return None
    technique = children.get("attack_technique")
    if technique is not None:
        if not isinstance(technique, dict):
            return None
        technique_children = technique.get("children", {})
        if not isinstance(technique_children, dict) or "attack" in technique_children:
            # A canonical attack is authoritative. Do not overwrite a conflicting value.
            return None

    result = copy.deepcopy(document)
    result_children = result["children"]
    if technique is None:
        result_children["attack_technique"] = {
            "class_name": "AttackTechnique",
            "class_module": "pyrit.scenario.core.attack_technique",
            "children": {},
        }
    result_technique = result_children["attack_technique"]
    result_technique.setdefault("children", {})["attack"] = result_children.pop("attack")
    # Both identities changed structurally. Keep child hashes and historical normalized
    # graph rows untouched, but never advertise the old identity for the rewritten JSON.
    for node in (result, result_technique):
        node.pop("hash", None)
        node.pop("eval_hash", None)
    return result


def upgrade() -> None:
    """Normalize retained JSON in bounded batches on SQLite and SQL Server."""
    bind = op.get_bind()
    results = sa.Table(
        "AttackResultEntries",
        sa.MetaData(),
        sa.Column("atomic_attack_identifier", sa.UnicodeText()),
        autoload_with=bind,
        include_columns=["id", "atomic_attack_identifier", "atomic_attack_identifier_hash"],
        resolve_fks=False,
    )
    last_id = None
    while True:
        statement = (
            sa.select(results.c.id, results.c.atomic_attack_identifier).order_by(results.c.id).limit(_BATCH_SIZE)
        )
        if last_id is not None:
            statement = statement.where(results.c.id > last_id)
        # Finish reading each batch before writing, including with pyodbc without MARS.
        rows = bind.execute(statement).all()
        if not rows:
            break
        for result_id, raw_document in rows:
            try:
                document = json.loads(raw_document) if raw_document is not None else None
            except (TypeError, ValueError):
                continue
            rewritten = _nested_identifier(document=document)
            if rewritten is not None:
                bind.execute(
                    results.update()
                    .where(results.c.id == result_id)
                    .values(
                        atomic_attack_identifier=json.dumps(rewritten, ensure_ascii=False),
                        atomic_attack_identifier_hash=None,
                    )
                )
        last_id = rows[-1].id


def downgrade() -> None:
    """Keep normalized data: original and migrated nested layouts are indistinguishable."""
