# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.models.seeds.seed_dataset import SeedDataset
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt


def test_seed_dataset_init_from_dicts():
    ds = SeedDataset(seeds=[{"value": "hello"}, {"value": "world"}])
    assert len(ds.seeds) == 2
    assert all(isinstance(s, SeedPrompt) for s in ds.seeds)


def test_seed_dataset_init_from_seed_objects():
    sp = SeedPrompt(value="hello", data_type="text", role="user")
    ds = SeedDataset(seeds=[sp])
    assert ds.seeds[0] is sp


def test_seed_dataset_empty_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        SeedDataset(seeds=[])


def test_seed_dataset_none_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        SeedDataset(seeds=None)


def test_seed_dataset_invalid_seed_type_raises():
    with pytest.raises(ValueError, match="dicts or Seed objects"):
        SeedDataset(seeds=[42])


def test_seed_dataset_defaults():
    ds = SeedDataset(seeds=[{"value": "hi"}])
    assert ds.data_type == "text"
    assert ds.name is None
    assert ds.dataset_name is None
    assert ds.description is None
    assert ds.source is None
    assert ds.date_added is not None


def test_seed_dataset_with_metadata():
    ds = SeedDataset(
        seeds=[{"value": "hi"}],
        name="test_ds",
        dataset_name="ds1",
        description="a dataset",
        authors=["author1"],
        groups=["group1"],
        source="test",
    )
    assert ds.name == "test_ds"
    assert ds.dataset_name == "ds1"
    assert ds.authors == ["author1"]


def test_seed_dataset_objective_seeds():
    ds = SeedDataset(seeds=[{"value": "objective text", "seed_type": "objective"}])
    assert len(ds.seeds) == 1
    assert isinstance(ds.seeds[0], SeedObjective)


def test_seed_dataset_get_values():
    ds = SeedDataset(seeds=[{"value": "a"}, {"value": "b"}, {"value": "c"}])
    values = ds.get_values()
    assert list(values) == ["a", "b", "c"]


def test_seed_dataset_get_values_first():
    ds = SeedDataset(seeds=[{"value": "a"}, {"value": "b"}, {"value": "c"}])
    assert list(ds.get_values(first=2)) == ["a", "b"]


def test_seed_dataset_get_values_last():
    ds = SeedDataset(seeds=[{"value": "a"}, {"value": "b"}, {"value": "c"}])
    assert list(ds.get_values(last=2)) == ["b", "c"]


def test_seed_dataset_get_values_first_and_last_overlap():
    ds = SeedDataset(seeds=[{"value": "a"}, {"value": "b"}])
    assert list(ds.get_values(first=2, last=2)) == ["a", "b"]


def test_seed_dataset_get_values_by_harm_category():
    ds = SeedDataset(
        seeds=[
            {"value": "a", "harm_categories": ["violence"]},
            {"value": "b", "harm_categories": ["hate"]},
            {"value": "c", "harm_categories": ["violence", "hate"]},
        ]
    )
    values = ds.get_values(harm_categories=["violence"])
    assert "a" in values
    assert "c" in values
    assert "b" not in values


def test_seed_dataset_get_random_values():
    ds = SeedDataset(seeds=[{"value": str(i)} for i in range(10)])
    result = ds.get_random_values(number=3)
    assert len(result) == 3


def test_seed_dataset_get_random_values_more_than_available():
    ds = SeedDataset(seeds=[{"value": "a"}, {"value": "b"}])
    result = ds.get_random_values(number=10)
    assert len(result) == 2


def test_seed_dataset_prompts_property():
    ds = SeedDataset(seeds=[{"value": "p1"}, {"value": "obj", "seed_type": "objective"}])
    assert len(ds.prompts) == 1
    assert isinstance(ds.prompts[0], SeedPrompt)


def test_seed_dataset_objectives_property():
    ds = SeedDataset(seeds=[{"value": "p1"}, {"value": "obj", "seed_type": "objective"}])
    assert len(ds.objectives) == 1
    assert isinstance(ds.objectives[0], SeedObjective)


def test_seed_dataset_repr():
    ds = SeedDataset(seeds=[{"value": "a"}])
    assert "1 seeds" in repr(ds)


def test_seed_dataset_from_dict():
    data = {
        "name": "test_ds",
        "description": "desc",
        "seeds": [{"value": "hello"}, {"value": "world"}],
    }
    ds = SeedDataset.from_dict(data)
    assert len(ds.seeds) == 2
    assert ds.description == "desc"


def test_seed_dataset_from_dict_rejects_preset_group_id():
    data = {
        "seeds": [{"value": "hello", "prompt_group_id": str(uuid.uuid4())}],
    }
    with pytest.raises(ValueError, match="prompt_group_id"):
        SeedDataset.from_dict(data)


def test_seed_dataset_group_seed_prompts_by_group_id():
    gid = uuid.uuid4()
    p1 = SeedPrompt(value="a", data_type="text", role="user", prompt_group_id=gid, sequence=0)
    obj = SeedObjective(value="objective", prompt_group_id=gid)
    groups = SeedDataset.group_seed_prompts_by_prompt_group_id([p1, obj])
    assert len(groups) == 1


def test_seed_dataset_group_without_group_id():
    p1 = SeedPrompt(value="a", data_type="text", role="user")
    p2 = SeedPrompt(value="b", data_type="text", role="user")
    p1.prompt_group_id = None
    p2.prompt_group_id = None
    groups = SeedDataset.group_seed_prompts_by_prompt_group_id([p1, p2])
    assert len(groups) == 2
