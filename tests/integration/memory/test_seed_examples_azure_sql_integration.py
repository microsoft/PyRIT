# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Azure SQL execution of the seed example read queries."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from pyrit.memory import AzureSQLMemory
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import SeedObjective, SeedPrompt


@pytest.mark.run_only_if_all_tests
async def test_seed_examples_on_azure_sql(azuresql_instance: AzureSQLMemory):
    test_id = str(uuid4())
    dataset = f"2748-azure-{test_id}"
    group = UUID("ffffffff-ffff-ffff-ffff-000000000000")
    older = UUID("00000000-0000-0000-0000-000000000001")
    newer = uuid4()
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    seeds = [
        SeedPrompt(value="hello", dataset_name=dataset, prompt_group_id=group, date_added=base_time),
        SeedObjective(
            value="objective",
            dataset_name=dataset,
            prompt_group_id=group,
            harm_categories=["Violence"],
            date_added=base_time,
        ),
        SeedPrompt(
            value="literal 100% a_b [ab] \\",
            dataset_name=dataset,
            prompt_group_id=newer,
            date_added=base_time + timedelta(1),
        ),
        SeedPrompt(value="older", dataset_name=dataset, prompt_group_id=older, date_added=base_time),
    ]
    await azuresql_instance.add_seeds_to_memory_async(seeds=seeds, added_by=test_id)

    async def ids(**filters) -> list:
        examples, _, _ = await azuresql_instance.get_seed_examples_async(dataset_name=dataset, limit=100, **filters)
        return list(examples)

    try:
        assert await ids() == [newer, group, older]
        assert await ids(harm_categories=["missing", "VIOLENCE"], value_search="HELLO") == [group]
        assert await ids(seed_types=["objective"], data_types=["text"]) == [group]
        for search in ["100%", "a_b", "[ab]", "\\"]:
            assert await ids(value_search=search) == [newer]
        assert await ids(value_search="0_") == []

        first, _, after = await azuresql_instance.get_seed_examples_async(dataset_name=dataset, limit=2)
        rest, _, last = await azuresql_instance.get_seed_examples_async(dataset_name=dataset, limit=100, after=after)
        assert after is not None
        assert after.identifier == str(group)
        assert list(first) == [newer, group]
        assert list(rest) == [older]
        assert last is None
        assert [*first, *rest] == await ids()

        detail = await azuresql_instance.get_seed_example_async(dataset_name=dataset, example_id=group)
        assert [seed.id for seed in detail] == [seeds[1].id, seeds[0].id]
    finally:
        async with await azuresql_instance.get_session_async() as session:
            await session.execute(delete(SeedEntry).where(SeedEntry.added_by == test_id))
            await session.commit()
