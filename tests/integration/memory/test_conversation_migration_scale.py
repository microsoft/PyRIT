# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text

from pyrit.memory.alembic.versions import b2f4c6a8d1e3_add_conversations_table as conversations_migration
from pyrit.memory.alembic.versions import e5f7a9c1b3d2_add_identifiers_tables as identifiers_migration

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection

_PRODUCTION_REVISION = "9c8b7a6d5e4f"
_HEAD_REVISION = "3f6e8a0c2d4b"
_PROMPT_ROW_COUNT = 135_069
_PROMPT_CONVERSATION_COUNT = 81_295
_SECOND_PROMPT_COUNT = _PROMPT_ROW_COUNT - _PROMPT_CONVERSATION_COUNT
_ATTACK_ONLY_COUNT = 137
_EXPECTED_CONVERSATION_COUNT = _PROMPT_CONVERSATION_COUNT + _ATTACK_ONLY_COUNT
_SEED_BATCH_SIZE = 5_000
_LARGE_TARGET_CONVERSATION_INDEX = 42
_CONFLICT_INDICES = frozenset({1, 10_001, 20_001, 30_001, 40_001, 50_001})
_NULL_TARGET_INTERVAL = 1_009
_CONVERTER_LINKED_PROMPT_COUNT = 601
_MAX_UPGRADE_SECONDS = 600

_TARGET_A_HASH = "a" * 64
_TARGET_B_HASH = "b" * 64
_LARGE_TARGET_HASH = "c" * 64
_CONVERTER_HASH = "d" * 64


def _target_json(*, target_hash: str, class_name: str, payload: str | None = None) -> str:
    identifier: dict[str, Any] = {
        "class_module": "tests.integration.memory.test_conversation_migration_scale",
        "class_name": class_name,
        "hash": target_hash,
        "model_name": class_name.lower(),
        "pyrit_version": "1.0.0",
    }
    if payload is not None:
        identifier["params"] = {"payload": payload}
    return json.dumps(identifier, sort_keys=True)


_TARGET_A = _target_json(target_hash=_TARGET_A_HASH, class_name="ScaleTargetA")
_TARGET_B = _target_json(target_hash=_TARGET_B_HASH, class_name="ScaleTargetB")
_LARGE_TARGET = _target_json(
    target_hash=_LARGE_TARGET_HASH,
    class_name="LargeScaleTarget",
    payload="x" * (64 * 1024),
)
_CONVERTER_IDENTIFIERS = json.dumps(
    [
        {
            "class_module": "tests.integration.memory.test_conversation_migration_scale",
            "class_name": "ScaleConverter",
            "hash": _CONVERTER_HASH,
        }
    ],
    sort_keys=True,
)


@dataclass
class _InsertMetrics:
    total_statements: int = 0
    statement_rows: list[int] = field(default_factory=list)
    parameter_counts: list[int] = field(default_factory=list)
    target_link_statement_rows: list[int] = field(default_factory=list)
    target_link_parameter_counts: list[int] = field(default_factory=list)
    target_link_update_statements: int = 0
    converter_link_statement_rows: list[int] = field(default_factory=list)
    converter_link_parameter_counts: list[int] = field(default_factory=list)

    def record(
        self,
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.total_statements += 1
        if statement.startswith('INSERT INTO "Conversations"'):
            self.statement_rows.append(statement.count("), (") + 1)
            self.parameter_counts.append(len(parameters))
        elif statement.startswith(
            ('INSERT INTO "_PyritConversationTargetLinks"', 'INSERT INTO "#PyritConversationTargetLinks"')
        ):
            self.target_link_statement_rows.append(statement.count("), (") + 1)
            self.target_link_parameter_counts.append(len(parameters))
        elif statement.startswith(
            (
                'UPDATE "Conversations" SET target_identifier_hash = (',
                "UPDATE conversations SET target_identifier_hash = links.target_identifier_hash",
            )
        ):
            self.target_link_update_statements += 1
        elif statement.startswith('INSERT INTO "PromptConverterIdentifiers"'):
            self.converter_link_statement_rows.append(statement.count("), (") + 1)
            self.converter_link_parameter_counts.append(len(parameters))


def _config_for(connection: Connection) -> Config:
    pyrit_root = Path(__file__).resolve().parents[3] / "pyrit"
    config = Config()
    config.set_main_option("script_location", str(pyrit_root / "memory" / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["version_table"] = "pyrit_memory_alembic_version"
    return config


def _make_prompt_target_identifier_nullable(connection: Connection) -> None:
    operations = Operations(MigrationContext.configure(connection))
    with operations.batch_alter_table("PromptMemoryEntries") as batch_op:
        batch_op.alter_column(
            "prompt_target_identifier",
            existing_type=sa.JSON(),
            existing_nullable=False,
            nullable=True,
        )


def _conversation_id(index: int) -> str:
    return f"conversation-{index:05d}"


def _attack_only_conversation_id(index: int) -> str:
    return f"attack-only-{index:05d}"


def _first_target(index: int) -> str | None:
    if index == _LARGE_TARGET_CONVERSATION_INDEX:
        return _LARGE_TARGET
    if index in _CONFLICT_INDICES:
        return _TARGET_A
    if index % _NULL_TARGET_INTERVAL == 0:
        return None
    return _TARGET_B if index % 5 == 0 else _TARGET_A


def _second_target(index: int) -> str:
    if index in _CONFLICT_INDICES:
        return _TARGET_B
    return _first_target(index) or _TARGET_A


def _expected_target(index: int) -> str | None:
    first_target = _first_target(index)
    if first_target is not None or index >= _SECOND_PROMPT_COUNT:
        return first_target
    return _second_target(index)


def _prompt_parameters() -> Iterator[dict[str, Any]]:
    row_number = 0
    for index in range(_PROMPT_CONVERSATION_COUNT):
        row_number += 1
        yield {
            "conv": _conversation_id(index),
            "id": str(uuid.UUID(int=row_number)),
            "seq": 0,
            "target": _first_target(index),
            "converters": _CONVERTER_IDENTIFIERS if row_number <= _CONVERTER_LINKED_PROMPT_COUNT else "[]",
        }
    for index in range(_SECOND_PROMPT_COUNT):
        row_number += 1
        yield {
            "conv": _conversation_id(index),
            "id": str(uuid.UUID(int=row_number)),
            "seq": 1,
            "target": _second_target(index),
            "converters": "[]",
        }


def _batched(values: Iterator[dict[str, Any]], *, size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _seed_prompt_rows(connection: Connection) -> None:
    statement = text(
        'INSERT INTO "PromptMemoryEntries" '
        "(id, role, conversation_id, sequence, timestamp, labels, prompt_metadata, "
        "prompt_target_identifier, attack_identifier, converter_identifiers, original_value_data_type, "
        "original_value, converted_value_data_type, original_prompt_id) "
        "VALUES (:id, 'user', :conv, :seq, '2026-07-22', '{}', '{}', "
        ":target, '{}', :converters, 'text', 'scale prompt', 'text', :id)"
    )
    for batch in _batched(_prompt_parameters(), size=_SEED_BATCH_SIZE):
        connection.execute(statement, batch)


def _seed_attack_only_rows(connection: Connection) -> None:
    statement = text(
        'INSERT INTO "AttackResultEntries" '
        "(id, conversation_id, objective, attack_identifier, objective_sha256, "
        "executed_turns, execution_time_ms, outcome, timestamp) "
        "VALUES (:id, :conv, 'scale objective', '{}', 'sha', 1, 0, 'success', '2026-07-22')"
    )
    connection.execute(
        statement,
        [
            {
                "conv": _attack_only_conversation_id(index),
                "id": str(uuid.UUID(int=_PROMPT_ROW_COUNT + index + 1)),
            }
            for index in range(_ATTACK_ONLY_COUNT)
        ],
    )


def _assert_sampled_conversations(connection: Connection) -> None:
    null_only_index = math.ceil(_SECOND_PROMPT_COUNT / _NULL_TARGET_INTERVAL) * _NULL_TARGET_INTERVAL
    samples = {
        _attack_only_conversation_id(0): (None, None),
        _conversation_id(0): (_TARGET_A, _TARGET_A_HASH),
        _conversation_id(1): (_TARGET_A, _TARGET_A_HASH),
        _conversation_id(5): (_TARGET_B, _TARGET_B_HASH),
        _conversation_id(_LARGE_TARGET_CONVERSATION_INDEX): (_LARGE_TARGET, _LARGE_TARGET_HASH),
        _conversation_id(null_only_index): (None, None),
    }
    rows = connection.execute(
        text(
            "SELECT conversation_id, target_identifier, target_identifier_hash "
            'FROM "Conversations" WHERE conversation_id IN '
            f"({', '.join(f':id_{index}' for index in range(len(samples)))})"
        ),
        {f"id_{index}": conversation_id for index, conversation_id in enumerate(samples)},
    ).fetchall()
    actual = {row[0]: (row[1], row[2]) for row in rows}
    assert actual == samples


@pytest.mark.run_only_if_all_tests
@pytest.mark.timeout(_MAX_UPGRADE_SECONDS)
def test_conversation_migration_upgrade_from_production_revision_at_scale(caplog: pytest.LogCaptureFixture) -> None:
    """Upgrade production-shaped SQLite data from the deployed revision to v1 head."""
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_engine(f"sqlite:///{os.path.join(temp_dir, 'conversation-scale.db')}")
        metrics = _InsertMetrics()
        listener = metrics.record
        try:
            with engine.begin() as connection:
                config = _config_for(connection)
                command.upgrade(config, _PRODUCTION_REVISION)
                _make_prompt_target_identifier_nullable(connection)
                _seed_prompt_rows(connection)
                _seed_attack_only_rows(connection)

                assert connection.execute(text('SELECT COUNT(*) FROM "PromptMemoryEntries"')).scalar_one() == (
                    _PROMPT_ROW_COUNT
                )
                assert (
                    connection.execute(
                        text(
                            'SELECT COUNT(DISTINCT conversation_id) FROM "PromptMemoryEntries" '
                            "WHERE conversation_id IS NOT NULL"
                        )
                    ).scalar_one()
                    == _PROMPT_CONVERSATION_COUNT
                )

                event.listen(engine, "before_cursor_execute", listener)
                started = perf_counter()
                command.upgrade(config, "head")
                elapsed_seconds = perf_counter() - started
                event.remove(engine, "before_cursor_execute", listener)

                version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()
                prompt_count = connection.execute(text('SELECT COUNT(*) FROM "PromptMemoryEntries"')).scalar_one()
                conversation_count = connection.execute(text('SELECT COUNT(*) FROM "Conversations"')).scalar_one()
                distinct_conversation_count = connection.execute(
                    text('SELECT COUNT(DISTINCT conversation_id) FROM "Conversations"')
                ).scalar_one()
                null_version_count = connection.execute(
                    text('SELECT COUNT(*) FROM "Conversations" WHERE pyrit_version IS NULL')
                ).scalar_one()
                converter_link_count = connection.execute(
                    text('SELECT COUNT(*) FROM "PromptConverterIdentifiers"')
                ).scalar_one()
                converter_identifier_count = connection.execute(
                    text('SELECT COUNT(*) FROM "ConverterIdentifiers"')
                ).scalar_one()
                prompt_columns = {column["name"] for column in inspect(connection).get_columns("PromptMemoryEntries")}
                conversation_columns = {column["name"] for column in inspect(connection).get_columns("Conversations")}
                _assert_sampled_conversations(connection)

            expected_statement_count = math.ceil(
                _EXPECTED_CONVERSATION_COUNT / conversations_migration._CONVERSATION_INSERT_BATCH_SIZE
            )
            null_prompt_conversations = (_PROMPT_CONVERSATION_COUNT - 1) // _NULL_TARGET_INTERVAL - (
                _SECOND_PROMPT_COUNT - 1
            ) // _NULL_TARGET_INTERVAL
            expected_target_links = _PROMPT_CONVERSATION_COUNT - null_prompt_conversations
            expected_target_link_statements = math.ceil(
                expected_target_links / conversations_migration._CONVERSATION_INSERT_BATCH_SIZE
            )
            expected_converter_link_statements = math.ceil(
                _CONVERTER_LINKED_PROMPT_COUNT / identifiers_migration._CONVERTER_LINK_INSERT_BATCH_SIZE
            )
            conflict_warnings = [
                record for record in caplog.records if "multiple distinct target identifiers" in record.message
            ]

            assert version == _HEAD_REVISION
            assert prompt_count == _PROMPT_ROW_COUNT
            assert conversation_count == _EXPECTED_CONVERSATION_COUNT
            assert distinct_conversation_count == _EXPECTED_CONVERSATION_COUNT
            assert null_version_count == _EXPECTED_CONVERSATION_COUNT
            assert "prompt_target_identifier" not in prompt_columns
            assert "attack_identifier" not in prompt_columns
            assert "target_identifier_hash" in conversation_columns
            assert len(metrics.statement_rows) == expected_statement_count == 204
            assert metrics.statement_rows == [400] * 203 + [232]
            assert metrics.parameter_counts == [800] * 203 + [464]
            assert len(metrics.target_link_statement_rows) == expected_target_link_statements == 204
            assert metrics.target_link_statement_rows == [400] * 203 + [68]
            assert metrics.target_link_parameter_counts == [800] * 203 + [136]
            assert metrics.target_link_update_statements == 1
            assert converter_link_count == _CONVERTER_LINKED_PROMPT_COUNT
            assert converter_identifier_count == 1
            assert len(metrics.converter_link_statement_rows) == expected_converter_link_statements == 3
            assert metrics.converter_link_statement_rows == [300, 300, 1]
            assert metrics.converter_link_parameter_counts == [900, 900, 3]
            assert metrics.total_statements < 2_000
            assert len(conflict_warnings) == len(_CONFLICT_INDICES)
            assert elapsed_seconds < _MAX_UPGRADE_SECONDS
            print(
                "conversation migration scale: "
                f"{_PROMPT_ROW_COUNT} prompt rows, {_EXPECTED_CONVERSATION_COUNT} conversations, "
                f"{len(metrics.statement_rows)} conversation inserts, "
                f"{len(metrics.target_link_statement_rows)} target-link inserts, "
                f"{len(metrics.converter_link_statement_rows)} converter-link inserts, "
                f"{metrics.total_statements} total statements, {elapsed_seconds:.3f}s"
            )
        finally:
            if event.contains(engine, "before_cursor_execute", listener):
                event.remove(engine, "before_cursor_execute", listener)
            engine.dispose()
