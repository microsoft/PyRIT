# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import json
import logging
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultEntry
from pyrit.memory.memory_models import ScenarioPlannedUnitEntry
from pyrit.memory.migration import check_schema_migrations
from pyrit.models import ScenarioRunPlan

REVISION = "54eb9489b8f0"
PREVIOUS = "d43b1bcf9c56"
ALEMBIC_DIRECTORY = Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"
PLANNED_RUN = uuid.UUID(int=1)
LEGACY_RUN = uuid.UUID(int=2)
STAMP = "2026-11-20 12:00:00"
# Two configurations share the name "attack"; seeds s2 and s3 share objective "Y".
PLAN = {
    "atomic_groups": [
        {
            "id": "group-a",
            "atomic_attack_name": "attack",
            "technique_eval_hash": "eval-1",
            "seed_group_ids": ["s1", "s2", "s3"],
        },
        {"id": "group-b", "atomic_attack_name": "attack", "technique_eval_hash": "eval-2", "seed_group_ids": ["s1"]},
        {"id": "group-c", "atomic_attack_name": "other", "technique_eval_hash": "eval-3", "seed_group_ids": ["s1"]},
    ],
    "seed_groups": [
        {"id": "s1", "objective_sha256": to_sha256("X")},
        {"id": "s2", "objective_sha256": to_sha256("Y")},
        {"id": "s3", "objective_sha256": to_sha256("Y")},
    ],
}
# Attempt ID -> (scenario run, attribution, objective, expected planned group and seed or None).
ATTEMPTS = {
    "attributed": (
        PLANNED_RUN,
        {"parent_collection": "attack", "parent_eval_hash": "eval-2", "seed_group_id": "s1"},
        "X",
        ("group-b", "s1"),
    ),
    "objective_match": (
        PLANNED_RUN,
        {"parent_collection": "attack", "parent_eval_hash": "eval-1"},
        "X",
        ("group-a", "s1"),
    ),
    "unique_name_without_hash": (
        PLANNED_RUN,
        {"parent_collection": "other", "seed_group_id": "s1"},
        "X",
        ("group-c", "s1"),
    ),
    "ambiguous_objective": (PLANNED_RUN, {"parent_collection": "attack", "parent_eval_hash": "eval-1"}, "Y", None),
    "ambiguous_name_without_hash": (PLANNED_RUN, {"parent_collection": "attack", "seed_group_id": "s1"}, "X", None),
    "unplanned_seed": (
        PLANNED_RUN,
        {"parent_collection": "attack", "parent_eval_hash": "eval-1", "seed_group_id": "s9"},
        "X",
        None,
    ),
    "legacy_run": (LEGACY_RUN, {"parent_collection": "attack", "seed_group_id": "s1"}, "X", None),
}


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIRECTORY))
    config.attributes.update(connection=connection, version_table="pyrit_memory_alembic_version")
    return config


def _attempt_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, name))


def _insert_runs_and_attempts(connection: sa.Connection) -> None:
    for run_id, metadata in ((PLANNED_RUN, {"run_plan": PLAN}), (LEGACY_RUN, {})):
        connection.execute(
            sa.text(
                "INSERT INTO ScenarioResultEntries (id, scenario_name, scenario_version, pyrit_version, "
                "scenario_identifier, objective_target_identifier, scenario_run_state, number_tries, "
                "completion_time, timestamp, scenario_metadata) VALUES (:id, 'Scenario', 1, '1.3.0', '{}', '{}', "
                "'COMPLETED', 1, :stamp, :stamp, :metadata)"
            ),
            {"id": str(run_id), "stamp": STAMP, "metadata": json.dumps(metadata)},
        )
    for name, (run_id, attribution, objective, _) in ATTEMPTS.items():
        connection.execute(
            sa.text(
                "INSERT INTO AttackResultEntries (id, conversation_id, objective, objective_sha256, executed_turns, "
                "execution_time_ms, outcome, timestamp, attribution_parent_id, attribution_data) VALUES "
                "(:id, :id, :objective, :objective_sha256, 1, 1, 'success', :stamp, :run_id, :attribution)"
            ),
            {
                "id": _attempt_id(name),
                "objective": objective,
                "objective_sha256": to_sha256(objective),
                "stamp": STAMP,
                "run_id": str(run_id),
                "attribution": json.dumps(attribution),
            },
        )


def test_planned_units_migration_is_single_successor_of_finding_field_values() -> None:
    scripts = ScriptDirectory(str(ALEMBIC_DIRECTORY))
    assert len(scripts.get_heads()) == 1
    revision = scripts.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS


def test_upgrade_backfills_planned_units_and_links_resolvable_attempts(caplog: pytest.LogCaptureFixture) -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            _insert_runs_and_attempts(connection)
            with caplog.at_level(logging.INFO):
                command.upgrade(config, REVISION)

            units = connection.execute(
                sa.text("SELECT id, scenario_result_id, atomic_group_id, seed_group_id FROM ScenarioPlannedUnitEntries")
            ).all()
            links = dict(connection.execute(sa.text("SELECT id, planned_unit_id FROM AttackResultEntries")).all())

        planned_units = {(group["id"], seed) for group in PLAN["atomic_groups"] for seed in group["seed_group_ids"]}
        assert {(uuid.UUID(run_id), group, seed) for _, run_id, group, seed in units} == {
            (PLANNED_RUN, group, seed) for group, seed in planned_units
        }
        # Units keep the IDs a resumed run computes for its attempts.
        assert {uuid.UUID(unit_id) for unit_id, *_ in units} == {
            uuid.UUID(
                ScenarioRunPlan.compute_planned_unit_id(
                    scenario_result_id=PLANNED_RUN, atomic_group_id=g, seed_group_id=s
                )
            )
            for g, s in planned_units
        }
        for name, (run_id, _, _, expected) in ATTEMPTS.items():
            expected_link = (
                ScenarioRunPlan.compute_planned_unit_id(
                    scenario_result_id=run_id, atomic_group_id=expected[0], seed_group_id=expected[1]
                )
                if expected
                else None
            )
            assert links[_attempt_id(name)] == expected_link, name
        assert "stored 5 planned unit(s) for 1 run plan(s)" in caplog.text
        assert "linked 3 attempt(s) and left 3 unlinked" in caplog.text
    finally:
        engine.dispose()


def test_planned_units_migration_downgrades_and_upgrades_again() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            _insert_runs_and_attempts(connection)
            command.upgrade(config, REVISION)
            command.downgrade(config, PREVIOUS)
            inspector = sa.inspect(connection)
            assert "ScenarioPlannedUnitEntries" not in inspector.get_table_names()
            assert "planned_unit_id" not in {column["name"] for column in inspector.get_columns("AttackResultEntries")}
            command.upgrade(config, "head")
            linked = connection.execute(
                sa.text("SELECT COUNT(*) FROM AttackResultEntries WHERE planned_unit_id IS NOT NULL")
            ).scalar()
            assert linked == 3
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_backfill_keeps_stored_units_and_links() -> None:
    migration = importlib.import_module(f"pyrit.memory.alembic.versions.{REVISION}_add_scenario_planned_units")
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            _insert_runs_and_attempts(connection)
            command.upgrade(config, REVISION)

            counts = migration.backfill_planned_units(bind=connection)

        assert (counts["units"], counts["linked"], counts["unlinked"]) == (0, 0, 3)
    finally:
        engine.dispose()


def test_planned_units_schema_compiles_for_sql_server() -> None:
    dialect = mssql.dialect()
    units = str(CreateTable(ScenarioPlannedUnitEntry.__table__).compile(dialect=dialect))
    assert "atomic_group_id VARCHAR(64) NOT NULL" in units
    assert "seed_group_id VARCHAR(64) NOT NULL" in units
    for index in ScenarioPlannedUnitEntry.__table__.indexes:
        CreateIndex(index).compile(dialect=dialect)
    attempts = str(CreateTable(AttackResultEntry.__table__).compile(dialect=dialect))
    link = next(line for line in attempts.splitlines() if "fk_attack_results_planned_unit" in line)
    assert "ON DELETE" not in link
