# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Seed old-layout rows before upgrading, on SQLite and disposable SQL Server."""

import copy
import datetime as dt
import os
import uuid

import pytest
import sqlalchemy as sa
from alembic import command

from build_scripts.check_sqlserver_migrations import _temporary_database
from pyrit.memory.alembic.versions import f2c6a8e0b419_nest_legacy_atomic_attack_identifiers as migration
from pyrit.memory.alembic.versions.ab8f2c1a9d07_pre_alembic_release_schema import _CustomUUID
from pyrit.memory.migration import _make_config, check_schema_migrations, run_schema_migrations


@pytest.mark.parametrize("with_converters", [False, True])
def test_nested_identifier_preserves_legacy_fields(with_converters: bool) -> None:
    attack = {"class_name": "LegacyAttack", "hash": "child-hash", "children": {}}
    if with_converters:
        attack["children"] = {
            "request_converters": [{"class_name": "RequestConverter", "custom": "retained"}],
            "response_converters": [{"class_name": "ResponseConverter"}],
        }
    document = {
        "hash": "old-hash",
        "eval_hash": "old-eval-hash",
        "class_name": "AtomicAttack",
        "custom": {"unicode": "caf\u00e9", "nested": [1, None]},
        "children": {"attack": attack, "seed_identifiers": [{"hash": "seed-hash"}]},
    }
    original = copy.deepcopy(document)
    actual = migration._nested_identifier(document=document)
    assert actual is not None
    assert document == original
    assert actual["custom"] == original["custom"]
    assert actual["class_name"] == "AtomicAttack"
    assert actual["children"]["seed_identifiers"] == original["children"]["seed_identifiers"]
    technique = actual["children"]["attack_technique"]
    assert technique["class_name"] == "AttackTechnique"
    assert technique["class_module"] == "pyrit.scenario.core.attack_technique"
    assert technique["children"]["attack"] == attack
    assert "attack" not in actual["children"]
    assert "hash" not in actual and "eval_hash" not in actual
    assert migration._nested_identifier(document=actual) is None


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        123,
        "not-an-object",
        {},
        {"children": None},
        {"children": []},
        {"children": {"attack": None}},
        {"children": {"attack": []}},
        {"children": {"attack": {}, "attack_technique": "unsupported"}},
        {"children": {"attack": {}, "attack_technique": {"children": []}}},
        {"children": {"attack": {}, "attack_technique": {"children": {"attack": {"class_name": "Current"}}}}},
    ],
)
def test_nested_identifier_leaves_other_layouts_untouched(document: object) -> None:
    original = copy.deepcopy(document)
    assert migration._nested_identifier(document=document) is None
    assert document == original


def test_nested_identifier_preserves_existing_technique_fields() -> None:
    actual = migration._nested_identifier(
        document={
            "children": {
                "attack": {"class_name": "Legacy"},
                "attack_technique": {
                    "class_name": "CustomTechnique",
                    "hash": "stale",
                    "eval_hash": "stale",
                    "custom": True,
                    "children": {"technique_seeds": [{"hash": "seed"}]},
                },
            },
        }
    )
    assert actual is not None
    technique = actual["children"]["attack_technique"]
    assert technique == {
        "class_name": "CustomTechnique",
        "custom": True,
        "children": {"technique_seeds": [{"hash": "seed"}], "attack": {"class_name": "Legacy"}},
    }


@pytest.fixture(params=["sqlite", "sqlserver"])
def legacy_engine(request):
    if request.param == "sqlserver":
        if not os.environ.get("PYRIT_TEST_SQLSERVER_URL"):
            pytest.skip("Requires a disposable SQL Server, not Azure production access")
        with _temporary_database() as engine:
            yield engine
    else:
        engine = sa.create_engine("sqlite:///:memory:")
        try:
            yield engine
        finally:
            engine.dispose()


def test_upgrade_normalizes_seeded_rows_and_clears_stale_hashes(legacy_engine) -> None:
    engine = legacy_engine
    legacy = {
        "class_name": "AtomicAttack",
        "custom": "retained",
        "children": {
            "attack": {
                "class_name": "LegacyAttack",
                "children": {
                    "request_converters": [{"class_name": "RequestConverter"}],
                    "response_converters": [{"class_name": "ResponseConverter"}],
                },
            },
            "seed_identifiers": [],
        },
    }
    plain = {"children": {"attack": {"class_name": "WithoutConverters"}}}
    current = {
        "hash": "c" * 64,
        "children": {"attack_technique": {"children": {"attack": {"class_name": "CurrentAttack"}}}},
    }
    mixed = copy.deepcopy(current)
    mixed["children"]["attack"] = {"class_name": "IgnoredLegacy"}
    hashed = copy.deepcopy(legacy)
    hashed["hash"] = "a" * 64
    hashed["eval_hash"] = "b" * 64
    documents = [legacy, plain, hashed, current, mixed, None, [], {"unrelated": "value"}]
    # More than one production-sized batch, with UUID ordering unrelated to insertion.
    documents.extend(copy.deepcopy(plain) for _ in range(501))
    surrogate = copy.deepcopy(plain)
    surrogate["prompt"] = "escaped \ud800 surrogate"
    documents.append(surrogate)
    with engine.begin() as connection:
        config = _make_config(connection=connection)
        command.upgrade(config, migration.down_revision)
        results = sa.Table(
            "AttackResultEntries",
            sa.MetaData(),
            sa.Column("id", _CustomUUID(), primary_key=True),
            sa.Column("atomic_attack_identifier", sa.JSON()),
            autoload_with=connection,
        )
        identifiers = sa.Table(
            "AtomicAttackIdentifiers", sa.MetaData(), sa.Column("identifier_json", sa.JSON()), autoload_with=connection
        )
        connection.execute(identifiers.insert().values(hash="a" * 64, identifier_json=hashed))
        ids = [uuid.uuid4() for _ in documents]
        for index, (result_id, document) in enumerate(zip(ids, documents, strict=True)):
            connection.execute(
                results.insert().values(
                    id=result_id,
                    conversation_id="migration-test",
                    objective="Synthetic objective",
                    outcome="success",
                    timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                    executed_turns=0,
                    execution_time_ms=0,
                    atomic_attack_identifier=document,
                    atomic_attack_identifier_hash="a" * 64 if index in (2, 5) else None,
                )
            )
        # Include a genuine SQL NULL as well as the JSON null above.
        null_id = uuid.uuid4()
        connection.execute(
            results.insert().values(
                id=null_id,
                conversation_id="migration-test",
                objective="Synthetic objective",
                outcome="success",
                timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                executed_turns=0,
                execution_time_ms=0,
                atomic_attack_identifier=sa.null(),
            )
        )
    run_schema_migrations(engine=engine)
    run_schema_migrations(engine=engine)
    with engine.begin() as connection:
        rows = {row.id: row for row in connection.execute(sa.select(results)).mappings()}
        for index, result_id in enumerate(ids):
            row = rows[result_id]
            actual = row.atomic_attack_identifier
            if index < 3 or index >= 8:
                assert "attack" not in actual["children"]
                assert (
                    actual["children"]["attack_technique"]["children"]["attack"]
                    == documents[index]["children"]["attack"]
                )
                assert actual["children"]["attack_technique"]["class_name"] == "AttackTechnique"
                if "prompt" in documents[index]:
                    assert actual["prompt"] == documents[index]["prompt"]
                assert "hash" not in actual and "eval_hash" not in actual
                if index in (0, 2):
                    assert actual["custom"] == "retained"
                    assert actual["children"]["seed_identifiers"] == []
                assert row.atomic_attack_identifier_hash is None
                assert row.resolved_atomic_attack_identifier_hash is None
            else:
                assert actual == documents[index]
        assert rows[null_id].atomic_attack_identifier is None
        assert rows[ids[5]].atomic_attack_identifier_hash == "a" * 64
        assert rows[ids[5]].resolved_atomic_attack_identifier_hash == "a" * 64
        assert rows[ids[3]].resolved_atomic_attack_identifier_hash == "c" * 64
        assert connection.execute(sa.select(identifiers.c.identifier_json)).scalar_one() == hashed
        before = {key: dict(value) for key, value in rows.items()}
        config = _make_config(connection=connection)
        command.downgrade(config, migration.down_revision)
        command.upgrade(config, "head")
        after = {row.id: dict(row) for row in connection.execute(sa.select(results)).mappings()}
        assert before == after
    check_schema_migrations(engine=engine)
