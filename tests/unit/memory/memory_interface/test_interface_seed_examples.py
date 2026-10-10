# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.sql import Select

from pyrit.common.pagination import DecodedKeysetCursor
from pyrit.common.utils import to_sha256
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import Seed, SeedObjective, SeedPrompt, SeedRecord, SeedSimulatedConversation

DATASET = "browse"
T0 = datetime(2024, 1, 1, tzinfo=UTC)


async def _add(memory: MemoryInterface, *seeds: Seed) -> None:
    await memory.add_seeds_to_memory_async(seeds=list(seeds), added_by="test")


async def _store(memory: MemoryInterface, entry: SeedEntry) -> None:
    async with await memory.get_session_async() as session:
        session.add(entry)
        await session.commit()


async def _ids(memory: MemoryInterface, dataset_name: str | None = DATASET, **filters) -> list[UUID]:
    examples, _, _ = await memory.get_seed_examples_async(dataset_name=dataset_name, limit=100, **filters)
    return list(examples)


async def test_get_seed_examples_orders_by_first_date_then_id_and_seeks(sqlite_instance: MemoryInterface):
    tied_low = UUID("00000000-0000-0000-0000-000000000001")
    tied_high = UUID("ffffffff-ffff-ffff-ffff-000000000000")
    newest, oldest = uuid4(), uuid4()
    await _add(
        sqlite_instance,
        SeedPrompt(value="newest late", dataset_name=DATASET, prompt_group_id=newest, date_added=T0 + timedelta(3)),
        SeedPrompt(value="newest", dataset_name=DATASET, prompt_group_id=newest, date_added=T0 + timedelta(2)),
        SeedPrompt(value="tied low", dataset_name=DATASET, prompt_group_id=tied_low, date_added=T0 + timedelta(1)),
        SeedPrompt(value="tied high", dataset_name=DATASET, prompt_group_id=tied_high, date_added=T0 + timedelta(1)),
        SeedPrompt(value="oldest", dataset_name=DATASET, id=oldest, date_added=T0),
    )

    first, first_total, after = await sqlite_instance.get_seed_examples_async(dataset_name=DATASET, limit=2)
    second, second_total, last = await sqlite_instance.get_seed_examples_async(
        dataset_name=DATASET, limit=2, after=after
    )

    assert [*first, *second] == [newest, tied_high, tied_low, oldest]
    assert after is not None
    assert (after.timestamp, after.identifier) == (T0 + timedelta(1), str(tied_high))
    assert last is None
    assert first_total == second_total == 4


async def test_get_seed_examples_uses_textual_uuid_keys_on_both_dialects_async(
    sqlite_instance: MemoryInterface,
) -> None:
    seed = SeedPrompt(value="prompt", dataset_name=DATASET, date_added=T0)
    await _add(sqlite_instance, seed)
    statements: list[Select[Any]] = []

    def capture(
        _connection: Any,
        statement: Any,
        _multiparams: Any,
        _params: Any,
        _execution_options: Any,
    ) -> None:
        if isinstance(statement, Select):
            statements.append(statement)

    engine = sqlite_instance._get_async_engine().sync_engine
    event.listen(engine, "before_execute", capture)
    try:
        await sqlite_instance.get_seed_examples_async(
            dataset_name=DATASET,
            limit=1,
            after=DecodedKeysetCursor(timestamp=T0, identifier="ffffffff-ffff-ffff-ffff-ffffffffffff"),
        )
    finally:
        event.remove(engine, "before_execute", capture)

    assert len(statements) == 3
    for dialect in (mssql.dialect(), sqlite.dialect()):
        page = str(statements[1].compile(dialect=dialect, compile_kwargs={"literal_binds": True})).lower()
        members = str(statements[2].compile(dialect=dialect, compile_kwargs={"literal_binds": True})).lower()
        assert "lower(cast(coalesce(" in page
        assert "as varchar(36)" in page
        assert "example_id_key < 'ffffffff-ffff-ffff-ffff-ffffffffffff'" in page
        assert "example_id_key desc" in page
        assert "order by" in members and "lower(cast(" in members


async def test_get_seed_examples_orders_filtered_examples_by_first_date_of_all_members(
    sqlite_instance: MemoryInterface,
):
    group, single = uuid4(), uuid4()
    await _add(
        sqlite_instance,
        SeedPrompt(value="old", dataset_name=DATASET, prompt_group_id=group, date_added=T0),
        SeedPrompt(value="needle", dataset_name=DATASET, prompt_group_id=group, date_added=T0 + timedelta(3)),
        SeedPrompt(value="needle", dataset_name=DATASET, id=single, date_added=T0 + timedelta(1)),
    )

    assert await _ids(sqlite_instance, value_search="needle") == [single, group]


async def test_get_seed_examples_returns_complete_groups_objective_first(sqlite_instance: MemoryInterface):
    group = uuid4()
    objective = SeedObjective(value="objective", dataset_name=DATASET, prompt_group_id=group)
    second = SeedPrompt(value="second", dataset_name=DATASET, prompt_group_id=group, sequence=1)
    first = SeedPrompt(value="first", dataset_name=DATASET, prompt_group_id=group, sequence=0)
    await _add(sqlite_instance, second, first, objective)

    examples, total, _ = await sqlite_instance.get_seed_examples_async(
        dataset_name=DATASET, limit=10, value_search="second"
    )

    assert total == 1
    assert [seed.id for seed in examples[group]] == [objective.id, first.id, second.id]
    assert isinstance(examples[group][0], SeedRecord)
    assert examples[group][0].seed_type == "objective"


async def test_get_seed_examples_filters_match_across_members(sqlite_instance: MemoryInterface):
    both, harm_only = uuid4(), uuid4()
    await _add(
        sqlite_instance,
        SeedObjective(value="objective", dataset_name=DATASET, prompt_group_id=both, harm_categories=["Violence"]),
        SeedPrompt(value="say hello", dataset_name=DATASET, prompt_group_id=both),
        SeedObjective(value="objective", dataset_name=DATASET, prompt_group_id=harm_only, harm_categories=["Violence"]),
        SeedPrompt(value="goodbye", dataset_name=DATASET, prompt_group_id=harm_only),
    )

    assert set(await _ids(sqlite_instance, harm_categories=["missing", "VIOLENCE"])) == {both, harm_only}
    assert await _ids(sqlite_instance, harm_categories=["violence"], value_search="HELLO") == [both]
    assert await _ids(sqlite_instance, seed_types=["objective"], data_types=["image_path"]) == []


async def test_get_seed_examples_keeps_named_and_unnamed_scopes_apart(sqlite_instance: MemoryInterface):
    group = uuid4()
    named = SeedPrompt(value="named", dataset_name=DATASET, prompt_group_id=group)
    null_name = SeedPrompt(value="null name", prompt_group_id=group)
    empty_name = SeedPrompt(value="empty name", dataset_name="", prompt_group_id=group)
    await _add(sqlite_instance, named, null_name, empty_name)

    named_examples, _, _ = await sqlite_instance.get_seed_examples_async(dataset_name=DATASET, limit=10)
    unnamed_examples, _, _ = await sqlite_instance.get_seed_examples_async(dataset_name=None, limit=10)

    assert [seed.id for seed in named_examples[group]] == [named.id]
    assert {seed.id for seed in unnamed_examples[group]} == {null_name.id, empty_name.id}
    assert await _ids(sqlite_instance, dataset_name="other") == []


@pytest.mark.parametrize(
    ("search", "expected"),
    [("%", ["50% off"]), ("_", ["snake_case"]), ("\\", ["a\\b"]), ("[x]", ["[x]"]), ("0_", []), ("x%", [])],
)
async def test_get_seed_examples_search_is_literal(sqlite_instance: MemoryInterface, search: str, expected: list[str]):
    await _add(
        sqlite_instance,
        *(SeedPrompt(value=value, dataset_name=DATASET) for value in ["50% off", "snake_case", "a\\b", "[x]"]),
    )

    examples, _, _ = await sqlite_instance.get_seed_examples_async(dataset_name=DATASET, limit=10, value_search=search)

    assert [seeds[0].value for seeds in examples.values()] == expected


async def test_get_seed_examples_search_ignores_media_values(sqlite_instance: MemoryInterface):
    media = SeedPrompt(value="/data/needle.png", data_type="image_path", dataset_name=DATASET, added_by="test")
    await _store(sqlite_instance, SeedEntry(entry=media))

    assert await _ids(sqlite_instance, value_search="needle") == []


async def test_get_seed_examples_search_ignores_simulated_conversation_json(sqlite_instance: MemoryInterface):
    group = uuid4()
    standalone = SeedSimulatedConversation(
        num_turns=2, adversarial_chat_system_prompt=SeedPrompt(value="needle"), dataset_name=DATASET
    )
    grouped = SeedSimulatedConversation(
        num_turns=2,
        adversarial_chat_system_prompt=SeedPrompt(value="needle"),
        dataset_name=DATASET,
        prompt_group_id=group,
    )
    objective = SeedObjective(value="objective", dataset_name=DATASET, prompt_group_id=group)
    await _add(sqlite_instance, standalone, grouped, objective)

    assert await _ids(sqlite_instance, value_search="needle") == []
    assert await _ids(sqlite_instance, value_search="num_turns") == []
    assert await _ids(sqlite_instance, value_search="objective") == [group]
    assert set(await _ids(sqlite_instance, seed_types=["simulated_conversation"])) == {standalone.id, group}


def _legacy_conversation_entry(*, prompt_file: Path, prompt_group_id: UUID) -> SeedEntry:
    entry = SeedEntry(
        entry=SeedSimulatedConversation(
            num_turns=2,
            adversarial_chat_system_prompt=SeedPrompt(value="placeholder"),
            dataset_name=DATASET,
            prompt_group_id=prompt_group_id,
            added_by="test",
        )
    )
    entry.value = json.dumps(
        {"num_turns": 2, "sequence": 0, "adversarial_chat_system_prompt_path": str(prompt_file)},
        sort_keys=True,
        separators=(",", ":"),
    )
    entry.value_sha256 = to_sha256(entry.value)
    return entry


@pytest.mark.parametrize("file_exists", [True, False])
async def test_get_seed_examples_preserves_legacy_members_without_loading_files_async(
    sqlite_instance: MemoryInterface, tmp_path: Path, file_exists: bool
) -> None:
    mixed, standalone = uuid4(), uuid4()
    prompt_file = tmp_path / "legacy.yaml"
    if file_exists:
        prompt_file.write_text('value: "{{ 1 + 1 }}"\ndata_type: text', encoding="utf-8")
    prompt = SeedPrompt(value="readable", dataset_name=DATASET, prompt_group_id=mixed)
    await _add(sqlite_instance, prompt)
    mixed_entry = _legacy_conversation_entry(prompt_file=prompt_file, prompt_group_id=mixed)
    standalone_entry = _legacy_conversation_entry(prompt_file=prompt_file, prompt_group_id=standalone)
    mixed_id, standalone_id = mixed_entry.id, standalone_entry.id
    expected = {entry.id: (entry.value, entry.value_sha256) for entry in (mixed_entry, standalone_entry)}
    await _store(sqlite_instance, mixed_entry)
    await _store(sqlite_instance, standalone_entry)

    with (
        patch.object(SeedEntry, "get_seed", side_effect=AssertionError("seed reconstruction")),
        patch.object(Path, "read_text", side_effect=AssertionError("file read")),
        patch.object(SeedPrompt, "render_template_value_silent", side_effect=AssertionError("template rendering")),
    ):
        examples, total, _ = await sqlite_instance.get_seed_examples_async(
            dataset_name=DATASET, limit=10, seed_types=["simulated_conversation"]
        )
        detail = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=mixed)

    assert {seed.id for seed in examples[mixed]} == {prompt.id, mixed_id}
    assert [seed.id for seed in examples[standalone]] == [standalone_id]
    assert detail == examples[mixed]
    assert total == 2
    for members in examples.values():
        for record in members:
            if record.seed_type == "simulated_conversation":
                assert (record.value, record.value_sha256) == expected[record.id]


async def test_get_seed_examples_preserves_simulated_configuration(sqlite_instance: MemoryInterface):
    conversation = SeedSimulatedConversation(
        num_turns=2,
        adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
        simulated_target_system_prompt=SeedPrompt(value="target", parameters=["objective", "num_turns"]),
        dataset_name=DATASET,
    )
    await _add(sqlite_instance, conversation)

    seeds = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=conversation.id)

    assert len(seeds) == 1
    assert isinstance(seeds[0], SeedRecord)
    assert seeds[0].seed_type == "simulated_conversation"
    assert seeds[0].value == conversation.value
    assert seeds[0].value_sha256 == conversation.value_sha256


async def test_get_seed_examples_keeps_unparseable_configuration_inspectable_async(
    sqlite_instance: MemoryInterface, tmp_path: Path
) -> None:
    group = uuid4()
    entry = _legacy_conversation_entry(prompt_file=tmp_path / "missing.yaml", prompt_group_id=group)
    entry.value = "unparseable stored configuration"
    expected_value, expected_hash = entry.value, entry.value_sha256
    await _store(sqlite_instance, entry)

    examples, total, _ = await sqlite_instance.get_seed_examples_async(dataset_name=DATASET, limit=10)
    detail = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=group)

    assert total == 1
    assert detail == examples[group]
    assert detail[0].value == expected_value
    assert detail[0].value_sha256 == expected_hash


async def test_get_seed_example_returns_empty_outside_its_dataset(sqlite_instance: MemoryInterface):
    group = uuid4()
    objective = SeedObjective(value="objective", dataset_name=DATASET, prompt_group_id=group, date_added=T0)
    await _add(sqlite_instance, objective, SeedPrompt(value="prompt", dataset_name=DATASET, prompt_group_id=group))

    seeds = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=group)

    assert seeds[0].id == objective.id
    assert len(seeds) == 2
    assert await sqlite_instance.get_seed_example_async(dataset_name="other", example_id=group) == []
    assert await sqlite_instance.get_seed_example_async(dataset_name=None, example_id=group) == []
