# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Store each scenario run's planned units in a table and link attack results to the unit they execute.

A planned unit is one atomic group of a run plan paired with one of its seed groups. Until now the units
existed only in ``ScenarioResultEntries.scenario_metadata.run_plan``, and scenario history expanded that
JSON and matched attempts to it on every read. This adds ``ScenarioPlannedUnitEntries`` and
``AttackResultEntries.planned_unit_id``, stores the units of every saved run plan, and links existing
attempts with the rules history used: the planned group with the attempt's atomic attack name and
technique hash (or the only group with that name when the attempt has no hash), then the attributed seed
group, or else the only seed group in that group with the attempt's objective. Attempts matching no unit
stay unlinked, as history already left them out of unit counts.

Revision ID: 54eb9489b8f0
Revises: d43b1bcf9c56
Create Date: 2026-11-25 12:00:00.000000
"""

import json
import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from alembic import op

from pyrit.memory.memory_models import CustomUUID

# revision identifiers, used by Alembic.
revision: str = "54eb9489b8f0"
down_revision: str | None = "d43b1bcf9c56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_UNITS = "ScenarioPlannedUnitEntries"
_ATTEMPTS = "AttackResultEntries"
_FOREIGN_KEY = "fk_attack_results_planned_unit"
_TRIGGER = "trg_delete_scenario_planned_units"
_ID_MAX_LENGTH = 64
# Frozen copy of ScenarioRunPlan.PLANNED_UNIT_ID_NAMESPACE: resumed runs reference backfilled units by this ID.
_PLANNED_UNIT_ID_NAMESPACE = uuid.UUID("0321aa1d-fe5c-4c66-b8d4-e7a8352a88e4")


@dataclass(frozen=True)
class _PlannedUnit:
    """One planned unit read from a stored run plan, with the fields attempts are matched on."""

    unit_id: uuid.UUID
    group_ordinal: int
    atomic_group_id: str
    atomic_attack_name: Any
    technique_eval_hash: Any
    seed_group_id: str
    objective_sha256: Any


def upgrade() -> None:
    """Create the planned-unit table and attempt link, then backfill both from the stored run plans."""
    op.create_table(
        _UNITS,
        sa.Column("id", CustomUUID(), nullable=False),
        sa.Column("scenario_result_id", CustomUUID(), nullable=False),
        sa.Column("atomic_group_id", sa.String(_ID_MAX_LENGTH), nullable=False),
        sa.Column("seed_group_id", sa.String(_ID_MAX_LENGTH), nullable=False),
        sa.ForeignKeyConstraint(["scenario_result_id"], ["ScenarioResultEntries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{_UNITS}_scenario_result_id", _UNITS, ["scenario_result_id"])
    if op.get_context().dialect.name == "sqlite":
        # Avoid reflecting/rebuilding the existing generated analytics column.
        op.execute(
            sa.text(
                f'ALTER TABLE "{_ATTEMPTS}" ADD COLUMN planned_unit_id CHAR(36) '
                f'CONSTRAINT "{_FOREIGN_KEY}" REFERENCES "{_UNITS}" (id)'
            )
        )
        # SQLite runs with foreign keys disabled, so delete the units with their run explicitly.
        op.execute(
            f"""
            CREATE TRIGGER "{_TRIGGER}"
            AFTER DELETE ON "ScenarioResultEntries"
            BEGIN
                DELETE FROM "{_UNITS}" WHERE scenario_result_id = OLD.id;
            END
            """
        )
    else:
        op.add_column(_ATTEMPTS, sa.Column("planned_unit_id", CustomUUID(), nullable=True))
        # NO ACTION: SQL Server rejects a second cascade path from ScenarioResultEntries to attempts.
        op.create_foreign_key(_FOREIGN_KEY, _ATTEMPTS, _UNITS, ["planned_unit_id"], ["id"])
    op.create_index(f"ix_{_ATTEMPTS}_planned_unit_id", _ATTEMPTS, ["planned_unit_id"])
    if op.get_context().as_sql:
        logger.warning("Skipping planned unit backfill: offline SQL generation cannot read existing run plans.")
        return
    counts = backfill_planned_units(bind=op.get_bind())
    logger.info(
        "Planned unit backfill: stored %d planned unit(s) for %d run plan(s), skipping %d with invalid IDs; "
        "linked %d attempt(s) and left %d unlinked.",
        counts["units"],
        counts["runs"],
        counts["invalid_units"],
        counts["linked"],
        counts["unlinked"],
    )


def downgrade() -> None:
    """Drop the attempt links and planned units; the run plans still hold the units for a later upgrade."""
    op.drop_index(f"ix_{_ATTEMPTS}_planned_unit_id", table_name=_ATTEMPTS)
    if op.get_context().dialect.name == "sqlite":
        op.execute(f'DROP TRIGGER "{_TRIGGER}"')
    else:
        op.drop_constraint(_FOREIGN_KEY, _ATTEMPTS, type_="foreignkey")
    op.drop_column(_ATTEMPTS, "planned_unit_id")
    op.drop_index(f"ix_{_UNITS}_scenario_result_id", table_name=_UNITS)
    op.drop_table(_UNITS)


def backfill_planned_units(*, bind: sa.Connection) -> Counter[str]:
    """
    Store the planned units of every saved run plan and link each run's unlinked attempts to them.

    Rerunning it is safe: stored units and linked attempts are kept.

    Args:
        bind (sa.Connection): The connection to backfill through.

    Returns:
        Counter[str]: Counts of ``runs``, ``units`` stored, ``invalid_units`` skipped, and attempts
            ``linked`` and left ``unlinked``.
    """
    scenarios = sa.table(
        "ScenarioResultEntries", sa.column("id", CustomUUID()), sa.column("scenario_metadata", sa.JSON())
    )
    counts: Counter[str] = Counter()
    for scenario_id in bind.execute(sa.select(scenarios.c.id)).scalars().all():
        metadata = bind.execute(
            sa.select(scenarios.c.scenario_metadata).where(scenarios.c.id == scenario_id)
        ).scalar_one_or_none()
        units = _read_planned_units(scenario_id=scenario_id, metadata=metadata, counts=counts)
        if units is None:
            continue
        counts["runs"] += 1
        _insert_units(bind=bind, scenario_id=scenario_id, units=units, counts=counts)
        _link_attempts(bind=bind, scenario_id=scenario_id, units=units, counts=counts)
    return counts


def _read_planned_units(*, scenario_id: uuid.UUID, metadata: Any, counts: Counter[str]) -> list[_PlannedUnit] | None:
    """
    Read the planned units of one run's stored plan as leniently as history read the plan JSON.

    Returns:
        list[_PlannedUnit] | None: The units in plan order, or None when the run stored no plan.
    """
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    plan = metadata.get("run_plan") if isinstance(metadata, dict) else None
    if not isinstance(plan, dict) or not isinstance(plan.get("atomic_groups"), list):
        return None
    groups = plan["atomic_groups"]
    seeds = plan.get("seed_groups")
    objectives = {
        seed.get("id"): seed.get("objective_sha256")
        for seed in (seeds if isinstance(seeds, list) else [])
        if isinstance(seed, dict)
    }
    units: dict[uuid.UUID, _PlannedUnit] = {}
    for ordinal, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get("seed_group_ids"), list):
            continue
        for seed_group_id in group["seed_group_ids"]:
            group_id = group.get("id")
            if not _is_valid_id(group_id) or not _is_valid_id(seed_group_id):
                counts["invalid_units"] += 1
                continue
            unit_id = uuid.uuid5(_PLANNED_UNIT_ID_NAMESPACE, f"{scenario_id}:{group_id}:{seed_group_id}")
            units.setdefault(
                unit_id,
                _PlannedUnit(
                    unit_id=unit_id,
                    group_ordinal=ordinal,
                    atomic_group_id=group_id,
                    atomic_attack_name=group.get("atomic_attack_name"),
                    technique_eval_hash=group.get("technique_eval_hash"),
                    seed_group_id=seed_group_id,
                    objective_sha256=objectives.get(seed_group_id),
                ),
            )
    return list(units.values())


def _is_valid_id(value: Any) -> bool:
    """
    Return whether a plan ID fits the planned-unit columns.

    Returns:
        bool: True for a non-empty string of at most 64 characters.
    """
    return isinstance(value, str) and 0 < len(value) <= _ID_MAX_LENGTH


def _insert_units(
    *, bind: sa.Connection, scenario_id: uuid.UUID, units: list[_PlannedUnit], counts: Counter[str]
) -> None:
    """Insert one run's planned units that are not stored yet."""
    table = sa.table(
        _UNITS,
        sa.column("id", CustomUUID()),
        sa.column("scenario_result_id", CustomUUID()),
        sa.column("atomic_group_id", sa.String()),
        sa.column("seed_group_id", sa.String()),
    )
    stored = set(bind.execute(sa.select(table.c.id).where(table.c.scenario_result_id == scenario_id)).scalars())
    rows = [
        {
            "id": unit.unit_id,
            "scenario_result_id": scenario_id,
            "atomic_group_id": unit.atomic_group_id,
            "seed_group_id": unit.seed_group_id,
        }
        for unit in units
        if unit.unit_id not in stored
    ]
    if rows:
        bind.execute(table.insert(), rows)
        counts["units"] += len(rows)


def _link_attempts(
    *, bind: sa.Connection, scenario_id: uuid.UUID, units: list[_PlannedUnit], counts: Counter[str]
) -> None:
    """Link each of one run's unlinked attempts to the planned unit it resolves to."""
    attempts = sa.table(
        _ATTEMPTS,
        sa.column("id", CustomUUID()),
        sa.column("attribution_parent_id", CustomUUID()),
        sa.column("attribution_data", sa.JSON()),
        sa.column("objective_sha256", sa.String()),
        sa.column("planned_unit_id", CustomUUID()),
    )
    rows = bind.execute(
        sa.select(attempts.c.id, attempts.c.attribution_data, attempts.c.objective_sha256).where(
            attempts.c.attribution_parent_id == scenario_id, attempts.c.planned_unit_id.is_(None)
        )
    ).all()
    groups_per_name = Counter(name for name, _ in {(unit.atomic_attack_name, unit.atomic_group_id) for unit in units})
    units_per_objective = Counter((unit.atomic_group_id, unit.objective_sha256) for unit in units)
    links = []
    for attempt_id, attribution_data, objective_sha256 in rows:
        unit_id = _resolve_unit(
            attribution_data=attribution_data,
            objective_sha256=objective_sha256,
            units=units,
            groups_per_name=groups_per_name,
            units_per_objective=units_per_objective,
        )
        if unit_id is None:
            counts["unlinked"] += 1
        else:
            links.append({"b_attempt_id": attempt_id, "b_unit_id": unit_id})
    if links:
        bind.execute(
            attempts.update()
            .where(attempts.c.id == sa.bindparam("b_attempt_id", type_=CustomUUID()))
            .values(planned_unit_id=sa.bindparam("b_unit_id", type_=CustomUUID())),
            links,
        )
        counts["linked"] += len(links)


def _resolve_unit(
    *,
    attribution_data: Any,
    objective_sha256: Any,
    units: list[_PlannedUnit],
    groups_per_name: Counter[Any],
    units_per_objective: Counter[tuple[str, Any]],
) -> uuid.UUID | None:
    """
    Resolve one attempt to its planned unit with the rules scenario history used before this revision.

    An attributed seed group must match exactly; without one, the attempt's objective must identify a single
    seed group of the matched atomic group. Atomic groups sharing a name and technique hash go to the first
    planned one.

    Returns:
        uuid.UUID | None: The planned unit, or None when no unit matches.
    """
    if isinstance(attribution_data, str):
        attribution_data = json.loads(attribution_data)
    data = attribution_data if isinstance(attribution_data, dict) else {}
    name = data.get("parent_collection")
    name = "" if name is None else name
    eval_hash = data.get("parent_eval_hash")
    eval_hash = "" if eval_hash is None else eval_hash
    seed_group_id = data.get("seed_group_id") or None
    best: tuple[tuple[int, str], uuid.UUID] | None = None
    for unit in units:
        if unit.atomic_attack_name != name:
            continue
        if not ((eval_hash == "" and groups_per_name[name] == 1) or unit.technique_eval_hash == eval_hash):
            continue
        if seed_group_id is not None:
            matches = unit.seed_group_id == seed_group_id
        else:
            matches = (
                objective_sha256 is not None
                and unit.objective_sha256 == objective_sha256
                and units_per_objective[(unit.atomic_group_id, objective_sha256)] == 1
            )
        if not matches:
            continue
        rank = (unit.group_ordinal, unit.seed_group_id)
        if best is None or rank < best[0]:
            best = (rank, unit.unit_id)
    return best[1] if best is not None else None
