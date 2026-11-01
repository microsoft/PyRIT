# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression tests for seed deduplication identity (issue #3738)."""

import json
from typing import Any

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


async def test_add_standalone_seeds_skip_exact_duplicates(sqlite_instance: MemoryInterface) -> None:
    """Exact duplicate standalone seeds are still skipped."""
    await _add(sqlite_instance, SeedPrompt(value="plain value", dataset_name=DATASET, data_type="text"))
    await _add(sqlite_instance, SeedPrompt(value="plain value", dataset_name=DATASET, data_type="text"))

    assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 1
