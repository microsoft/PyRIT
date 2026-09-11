# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import MemoryInterface
from pyrit.models import SeedGroup, SeedObjective, SeedPrompt

# =========================================================================
# remove_seeds_from_memory
# =========================================================================


async def test_remove_seeds_by_dataset_name(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="to_delete", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="to_keep", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(dataset_name="to_delete")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].dataset_name == "to_keep"


async def test_remove_seeds_by_dataset_name_pattern(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="harm_category_1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="harm_category_2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="safe_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(dataset_name_pattern="harm%")
    assert removed == 2

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].dataset_name == "safe_dataset"


async def test_remove_seeds_by_added_by(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="ds2", added_by="user2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    removed = sqlite_instance.remove_seeds_from_memory(added_by="user1")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].added_by == "user2"


async def test_remove_seeds_by_source(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", source="internal", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="ds2", source="external", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(source="internal")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].source == "external"


async def test_remove_seeds_by_harm_categories(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["violence"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["fraud"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(harm_categories=["violence"])
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].harm_categories == ["fraud"]


async def test_remove_seeds_by_authors(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(authors=["author1"])
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].authors == ["author2"]


async def test_remove_seeds_by_groups(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", groups=["group1"], data_type="text"),
        SeedPrompt(value="prompt2", groups=["group2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(groups=["group1"])
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].groups == ["group2"]


async def test_remove_seeds_by_parameters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", parameters=["param1"], data_type="text"),
        SeedPrompt(value="prompt2", parameters=["param2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(parameters=["param1"])
    assert removed == 1
    assert len(sqlite_instance.get_seeds(parameters=["param1"])) == 0
    assert len(sqlite_instance.get_seeds(parameters=["param2"])) == 1


async def test_remove_seeds_by_metadata(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", data_type="text", metadata={"key1": "value1"}),
        SeedPrompt(value="prompt2", data_type="text", metadata={"key1": "value2"}),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(metadata={"key1": "value1"})
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "prompt2"


async def test_remove_seeds_by_data_type(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="ds2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    assert sqlite_instance.remove_seeds_from_memory(data_types=["image_path"]) == 0
    assert sqlite_instance.remove_seeds_from_memory(data_types=["text"]) == 2
    assert len(sqlite_instance.get_seeds()) == 0


async def test_remove_seeds_by_seed_type(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="a prompt", dataset_name="ds", data_type="text")
    objective = SeedObjective(value="an objective")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[prompt, objective], added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(seed_type="objective")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].seed_type == "prompt"


async def test_remove_seeds_by_value_sha256(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="ds", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")
    target = next(seed for seed in sqlite_instance.get_seeds() if seed.value == "prompt1")

    removed = sqlite_instance.remove_seeds_from_memory(value_sha256=[target.value_sha256])
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "prompt2"


async def test_remove_seeds_by_prompt_group_ids(sqlite_instance: MemoryInterface):
    group = SeedGroup(seeds=[SeedPrompt(value="grouped", dataset_name="ds", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="test")
    group_id = sqlite_instance.get_seeds()[0].prompt_group_id

    removed = sqlite_instance.remove_seeds_from_memory(prompt_group_ids=[group_id])
    assert removed == 1
    assert len(sqlite_instance.get_seeds()) == 0


async def test_remove_seeds_by_value_substring_is_broad(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="the quick fox", dataset_name="ds", data_type="text"),
        SeedPrompt(value="the lazy dog", dataset_name="ds", data_type="text"),
        SeedPrompt(value="a cat", dataset_name="ds", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    # exact=False opts into substring matching, which is broad: "the" matches both values that contain it.
    removed = sqlite_instance.remove_seeds_from_memory(value="the", exact=False)
    assert removed == 2

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "a cat"


async def test_remove_seeds_by_value_defaults_to_exact(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="the quick fox", dataset_name="ds", data_type="text"),
        SeedPrompt(value="the lazy dog", dataset_name="ds", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    # The remove methods default to exact matching, so a substring like "the" removes nothing.
    assert sqlite_instance.remove_seeds_from_memory(value="the") == 0
    # The full value matches exactly one seed.
    assert sqlite_instance.remove_seeds_from_memory(value="the quick fox") == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "the lazy dog"


async def test_remove_seeds_by_value_exact_is_narrow(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="the quick fox", dataset_name="ds", data_type="text"),
        SeedPrompt(value="the lazy dog", dataset_name="ds", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    # exact=True only removes the fully-matching value, not substring siblings.
    removed = sqlite_instance.remove_seeds_from_memory(value="the quick fox", exact=True)
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "the lazy dog"


async def test_remove_seeds_multi_filter_narrowing(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="ds1", added_by="user2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="ds2", added_by="user1", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    removed = sqlite_instance.remove_seeds_from_memory(dataset_name="ds1", added_by="user1")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 2


async def test_remove_seeds_no_filter_raises_value_error(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    with pytest.raises(ValueError, match="At least one filter"):
        sqlite_instance.remove_seeds_from_memory()


async def test_remove_seeds_returns_zero_when_no_match(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seeds_from_memory(dataset_name="nonexistent")
    assert removed == 0

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1


async def test_remove_seeds_rolls_back_on_error(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    with patch("sqlalchemy.orm.session.Session.commit", side_effect=SQLAlchemyError("boom")):
        with pytest.raises(SQLAlchemyError):
            sqlite_instance.remove_seeds_from_memory(dataset_name="ds1")

    # The failed deletion is rolled back, so the seed is still present.
    assert len(sqlite_instance.get_seeds()) == 1


# =========================================================================
# remove_seed_groups_from_memory
# =========================================================================


async def test_remove_seed_groups_removes_entire_group(sqlite_instance: MemoryInterface):
    group = SeedGroup(
        seeds=[
            SeedPrompt(value="match_me", dataset_name="grouped", data_type="text", sequence=0, role="user"),
            SeedPrompt(value="sibling", dataset_name="grouped", data_type="text", sequence=1, role="user"),
        ]
    )
    standalone = SeedGroup(seeds=[SeedPrompt(value="keep", dataset_name="other", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group, standalone], added_by="test")

    # Filter matches only one seed in the group, but the whole group is removed.
    removed = sqlite_instance.remove_seed_groups_from_memory(value="match_me", exact=True)
    assert removed == 2

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "keep"


async def test_remove_seed_groups_spanning_multiple_datasets(sqlite_instance: MemoryInterface):
    group = SeedGroup(
        seeds=[
            SeedPrompt(value="seed_a", dataset_name="ds_a", data_type="text", sequence=0, role="user"),
            SeedPrompt(value="seed_b", dataset_name="ds_b", data_type="text", sequence=1, role="user"),
        ]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="test")

    # Matching a seed in one dataset removes the whole group, including the seed in the other dataset.
    removed = sqlite_instance.remove_seed_groups_from_memory(dataset_name="ds_a")
    assert removed == 2
    assert len(sqlite_instance.get_seeds()) == 0


async def test_remove_seed_groups_preserves_non_matching_groups(sqlite_instance: MemoryInterface):
    group_to_remove = SeedGroup(seeds=[SeedPrompt(value="g1", dataset_name="remove", data_type="text", sequence=0)])
    group_to_keep = SeedGroup(seeds=[SeedPrompt(value="g2", dataset_name="keep", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(
        prompt_groups=[group_to_remove, group_to_keep], added_by="test"
    )

    removed = sqlite_instance.remove_seed_groups_from_memory(dataset_name="remove")
    assert removed == 1

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1
    assert remaining[0].value == "g2"


async def test_remove_seed_groups_skips_ungrouped_seeds(sqlite_instance: MemoryInterface):
    # Seeds added individually keep prompt_group_id=None, so they belong to no group.
    seed_prompts = [
        SeedPrompt(value="ungrouped", dataset_name="ds1", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    removed = sqlite_instance.remove_seed_groups_from_memory(dataset_name="ds1")
    assert removed == 0
    assert len(sqlite_instance.get_seeds()) == 1


async def test_remove_seed_groups_no_filter_raises_value_error(sqlite_instance: MemoryInterface):
    group = SeedGroup(seeds=[SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="test")

    with pytest.raises(ValueError, match="At least one filter"):
        sqlite_instance.remove_seed_groups_from_memory()


async def test_remove_seed_groups_returns_zero_when_no_match(sqlite_instance: MemoryInterface):
    group = SeedGroup(seeds=[SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="test")

    removed = sqlite_instance.remove_seed_groups_from_memory(dataset_name="nonexistent")
    assert removed == 0

    remaining = sqlite_instance.get_seeds()
    assert len(remaining) == 1


async def test_remove_seed_groups_rolls_back_on_error(sqlite_instance: MemoryInterface):
    group = SeedGroup(seeds=[SeedPrompt(value="prompt1", dataset_name="ds1", data_type="text", sequence=0)])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="test")

    with patch("sqlalchemy.orm.session.Session.commit", side_effect=SQLAlchemyError("boom")):
        with pytest.raises(SQLAlchemyError):
            sqlite_instance.remove_seed_groups_from_memory(dataset_name="ds1")

    # The failed deletion is rolled back, so the group is still present.
    assert len(sqlite_instance.get_seeds()) == 1
