# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression tests for seed deduplication identity (issue #3738)."""

import json
from typing import Any

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import Seed, SeedGroup, SeedObjective, SeedPrompt

DATASET = "dedup-identity"

SCHEMA_ALPHA = {"type": "object", "properties": {"alpha": {"type": "string"}}, "required": ["alpha"]}
SCHEMA_BETA = {"type": "object", "properties": {"beta": {"type": "string"}}, "required": ["beta"]}


async def _add(memory: MemoryInterface, *seeds: Seed[Any]) -> None:
    await memory.add_seeds_to_memory_async(seeds=list(seeds), added_by="tester")


async def test_add_seeds_keeps_same_value_with_distinct_seed_types(sqlite_instance: MemoryInterface) -> None:
    """A prompt and an objective with the same value are execution-distinct, so both must stay."""
    await _add(sqlite_instance, SeedPrompt(value="shared value", dataset_name=DATASET, data_type="text"))
    await _add(sqlite_instance, SeedObjective(value="shared value", dataset_name=DATASET))

    stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
    assert len(stored) == 2

    objectives = await sqlite_instance.get_seeds_async(dataset_name=DATASET, seed_type="objective")
    assert len(objectives) == 1


async def test_add_standalone_seeds_keep_distinct_data_types(sqlite_instance: MemoryInterface) -> None:
    """The data type is execution-relevant, so same-value seeds with different data types must stay."""
    value = "https://example.com/shared-value"
    await _add(sqlite_instance, SeedPrompt(value=value, dataset_name=DATASET, data_type="text"))
    await _add(sqlite_instance, SeedPrompt(value=value, dataset_name=DATASET, data_type="url"))

    stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
    assert len(stored) == 2
    assert sorted(seed.data_type for seed in stored) == ["text", "url"]


async def test_add_singleton_groups_keep_distinct_response_json_schemas(sqlite_instance: MemoryInterface) -> None:
    """Singleton groups that differ only by response schema are distinct and both must stay."""
    alpha_group = SeedGroup(
        seeds=[
            SeedPrompt(value="schema value", dataset_name=DATASET, data_type="text", response_json_schema=SCHEMA_ALPHA)
        ]
    )
    beta_group = SeedGroup(
        seeds=[
            SeedPrompt(value="schema value", dataset_name=DATASET, data_type="text", response_json_schema=SCHEMA_BETA)
        ]
    )

    await _add(sqlite_instance, *alpha_group.seeds)
    await _add(sqlite_instance, *beta_group.seeds)

    stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
    assert len(stored) == 2
    stored_schemas = sorted(
        json.dumps(seed.response_json_schema, sort_keys=True) for seed in stored if isinstance(seed, SeedPrompt)
    )
    assert stored_schemas == sorted([json.dumps(SCHEMA_ALPHA, sort_keys=True), json.dumps(SCHEMA_BETA, sort_keys=True)])


async def test_add_singleton_groups_skip_exact_duplicate_content(sqlite_instance: MemoryInterface) -> None:
    """Exact duplicate singleton group content is still skipped."""
    first = SeedGroup(
        seeds=[
            SeedPrompt(value="repeat value", dataset_name=DATASET, data_type="text", response_json_schema=SCHEMA_ALPHA)
        ]
    )
    second = SeedGroup(
        seeds=[
            SeedPrompt(value="repeat value", dataset_name=DATASET, data_type="text", response_json_schema=SCHEMA_ALPHA)
        ]
    )

    await _add(sqlite_instance, *first.seeds)
    await _add(sqlite_instance, *second.seeds)

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 1


async def test_metadata_only_changes_dedupe_across_shapes(sqlite_instance: MemoryInterface) -> None:
    """A metadata-only edit and grouped/ungrouped shapes dedupe on execution identity."""

    def prompt(harm: str) -> SeedPrompt:
        return SeedPrompt(value="v", dataset_name=DATASET, data_type="text", harm_categories=[harm])

    await _add(sqlite_instance, prompt("a"))
    await _add(sqlite_instance, *SeedGroup(seeds=[prompt("b")]).seeds)
    await _add(sqlite_instance, prompt("c"))

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 1


async def test_add_standalone_seeds_skip_exact_duplicates(sqlite_instance: MemoryInterface) -> None:
    """Exact duplicate standalone seeds are still skipped."""
    await _add(sqlite_instance, SeedPrompt(value="plain value", dataset_name=DATASET, data_type="text"))
    await _add(sqlite_instance, SeedPrompt(value="plain value", dataset_name=DATASET, data_type="text"))

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 1


async def test_add_standalone_seeds_keep_distinct_schemas_and_roles(sqlite_instance: MemoryInterface) -> None:
    """Standalone execution identity includes the response schema and the role."""
    for extra in ({}, {"response_json_schema": SCHEMA_ALPHA}, {"role": "system"}):
        await _add(sqlite_instance, SeedPrompt(value="v", dataset_name=DATASET, data_type="text", **extra))

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 3


async def test_copying_stored_seed_into_singleton_group_gets_new_id(sqlite_instance: MemoryInterface) -> None:
    """A stored seed copied into a new singleton group with a different schema must not reuse its ID."""
    await _add(sqlite_instance, SeedPrompt(value="copied value", dataset_name=DATASET, data_type="text"))
    stored = (await sqlite_instance.get_seeds_async(dataset_name=DATASET))[0]
    original_id = stored.id

    # The new schema is execution-distinct, so the copy must be inserted as a new row; reusing the
    # stored seed ID would violate the primary key and roll back the whole batch.
    stored.response_json_schema = SCHEMA_ALPHA
    await _add(sqlite_instance, *SeedGroup(seeds=[stored]).seeds)

    rows = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
    assert len(rows) == 2
    assert {seed.id for seed in rows} == {original_id, stored.id}
    assert stored.id != original_id


@pytest.mark.parametrize("standalone_first", [True, False])
async def test_dedup_is_order_independent_across_shapes(
    sqlite_instance: MemoryInterface, standalone_first: bool
) -> None:
    """A metadata-only edit dedupes the same whether the standalone or singleton seed arrives first."""

    def prompt(harm: str) -> SeedPrompt:
        return SeedPrompt(value="order value", dataset_name=DATASET, data_type="text", harm_categories=[harm])

    standalone = prompt("a")
    grouped = SeedGroup(seeds=[prompt("b")]).seeds
    if standalone_first:
        await _add(sqlite_instance, standalone)
        await _add(sqlite_instance, *grouped)
    else:
        await _add(sqlite_instance, *grouped)
        await _add(sqlite_instance, standalone)

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 1


@pytest.mark.parametrize(("field", "value"), [("role", "system"), ("response_json_schema", SCHEMA_ALPHA)])
async def test_readding_edited_grouped_seed_keeps_original_group_intact(
    sqlite_instance: MemoryInterface, field: str, value: Any
) -> None:
    """A copy of a stored grouped seed must land in a new group, leaving the original group intact."""
    await _add(sqlite_instance, *SeedGroup(seeds=[SeedPrompt(value="grouped", dataset_name=DATASET)]).seeds)
    stored = (await sqlite_instance.get_seeds_async(dataset_name=DATASET))[0]
    original_group_id = stored.prompt_group_id

    setattr(stored, field, value)
    await _add(sqlite_instance, *SeedGroup(seeds=[stored]).seeds)

    groups = await sqlite_instance.get_seed_groups_async(dataset_name=DATASET)
    assert sorted(len(group.seeds) for group in groups) == [1, 1]
    assert stored.prompt_group_id != original_group_id
