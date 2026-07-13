# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for group_seeds_into_attack_groups."""

import uuid

import pytest

from pyrit.models import (
    AttackSeedGroup,
    SeedObjective,
    SeedPrompt,
    group_seeds_into_attack_groups,
)


def test_empty_returns_empty():
    assert group_seeds_into_attack_groups([]) == []


def test_groups_seeds_sharing_prompt_group_id():
    group_id = uuid.uuid4()
    seeds = [
        SeedObjective(value="objective", prompt_group_id=group_id),
        SeedPrompt(value="prompt", prompt_group_id=group_id),
    ]

    result = group_seeds_into_attack_groups(seeds)

    assert len(result) == 1
    assert isinstance(result[0], AttackSeedGroup)
    assert len(result[0].seeds) == 2
    assert result[0].objective.value == "objective"


def test_distinct_group_ids_become_distinct_groups():
    seeds = [
        SeedObjective(value="obj-a", prompt_group_id=uuid.uuid4()),
        SeedObjective(value="obj-b", prompt_group_id=uuid.uuid4()),
    ]

    result = group_seeds_into_attack_groups(seeds)

    assert len(result) == 2
    assert {g.objective.value for g in result} == {"obj-a", "obj-b"}


def test_ungrouped_objective_seeds_each_form_own_group():
    seeds = [
        SeedObjective(value="obj-1"),
        SeedObjective(value="obj-2"),
    ]

    result = group_seeds_into_attack_groups(seeds)

    assert len(result) == 2
    assert all(len(g.seeds) == 1 for g in result)


def test_orders_group_members_by_sequence():
    group_id = uuid.uuid4()
    seeds = [
        SeedPrompt(value="second", prompt_group_id=group_id, sequence=2, role="user"),
        SeedObjective(value="objective", prompt_group_id=group_id),
        SeedPrompt(value="first", prompt_group_id=group_id, sequence=1, role="user"),
    ]

    result = group_seeds_into_attack_groups(seeds)

    assert len(result) == 1
    prompt_values = [s.value for s in result[0].prompts]
    assert prompt_values == ["first", "second"]


def test_raises_when_group_has_no_objective():
    seeds = [SeedPrompt(value="prompt-only")]

    with pytest.raises(ValueError):
        group_seeds_into_attack_groups(seeds)


def test_raises_when_group_has_multiple_objectives():
    group_id = uuid.uuid4()
    seeds = [
        SeedObjective(value="obj-1", prompt_group_id=group_id),
        SeedObjective(value="obj-2", prompt_group_id=group_id),
    ]

    with pytest.raises(ValueError):
        group_seeds_into_attack_groups(seeds)
