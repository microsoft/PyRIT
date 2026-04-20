# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.models.seeds.seed_attack_group import SeedAttackGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt


def _make_prompt(*, value="test prompt", sequence=0):
    return SeedPrompt(value=value, data_type="text", role="user", sequence=sequence)


def _make_objective(*, value="test objective"):
    return SeedObjective(value=value)


def test_seed_attack_group_valid_init():
    objective = _make_objective()
    prompt = _make_prompt()
    group = SeedAttackGroup(seeds=[objective, prompt])
    assert group.objective is objective
    assert len(group.seeds) == 2


def test_seed_attack_group_objective_property():
    objective = _make_objective(value="achieve goal")
    group = SeedAttackGroup(seeds=[objective, _make_prompt()])
    assert group.objective.value == "achieve goal"


def test_seed_attack_group_no_objective_raises():
    prompt = _make_prompt()
    with pytest.raises(ValueError, match="exactly one objective"):
        SeedAttackGroup(seeds=[prompt])


def test_seed_attack_group_two_objectives_raises():
    obj1 = _make_objective(value="obj1")
    obj2 = _make_objective(value="obj2")
    prompt = _make_prompt()
    with pytest.raises(ValueError, match="one objective"):
        SeedAttackGroup(seeds=[obj1, obj2, prompt])


def test_seed_attack_group_empty_seeds_raises():
    with pytest.raises(ValueError):
        SeedAttackGroup(seeds=[])


def test_seed_attack_group_consistent_group_id():
    objective = _make_objective()
    prompt = _make_prompt()
    group = SeedAttackGroup(seeds=[objective, prompt])
    group_ids = {s.prompt_group_id for s in group.seeds}
    assert len(group_ids) == 1
    assert None not in group_ids


def test_seed_attack_group_with_multiple_prompts():
    objective = _make_objective()
    p1 = _make_prompt(value="p1", sequence=0)
    p2 = _make_prompt(value="p2", sequence=1)
    group = SeedAttackGroup(seeds=[objective, p1, p2])
    assert len(group.prompts) == 2


def test_seed_attack_group_objective_raises_when_get_objective_returns_none():
    from unittest.mock import patch

    prompt = _make_prompt()
    objective = _make_objective()
    group = SeedAttackGroup(seeds=[objective, prompt])
    with patch.object(type(group), "_get_objective", return_value=None):
        with pytest.raises(ValueError, match="SeedAttackGroup should always have an objective"):
            _ = group.objective
