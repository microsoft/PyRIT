# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from collections.abc import Sequence
from unittest.mock import patch
from uuid import uuid4

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import MessagePiece, SeedDataset, SeedGroup, SeedObjective, SeedPrompt


def assert_original_value_in_list(original_value: str, message_pieces: Sequence[MessagePiece]):
    for piece in message_pieces:
        if piece.original_value == original_value:
            return True
    raise AssertionError(f"Original value {original_value} not found in list")


@pytest.mark.asyncio
async def test_get_seeds_with_audio(sqlite_instance: MemoryInterface):
    """Test adding and retrieving seed prompts with an audio file."""
    temp_files = []
    try:
        # Create a temporary audio file
        audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115
        audio_file.write(b"dummy audio content")
        audio_file.close()
        temp_files.append(audio_file.name)

        # Create a seed prompt for the audio file
        audio_prompt = SeedPrompt(value=audio_file.name, dataset_name="dataset_audio", data_type="audio_path")

        # Add seed prompt to memory
        await sqlite_instance.add_seeds_to_memory_async(seeds=[audio_prompt], added_by="test_audio")

        # Retrieve and verify the seed prompts
        result = sqlite_instance.get_seeds()
        assert len(result) == 1
        assert result[0].value.endswith(".wav")
        assert result[0].data_type == "audio_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


@pytest.mark.asyncio
async def test_get_seeds_with_video(sqlite_instance: MemoryInterface):
    """Test adding and retrieving seed prompts with a video file."""
    temp_files = []
    try:
        # Create a temporary video file
        video_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)  # noqa: SIM115
        video_file.write(b"dummy video content")
        video_file.close()
        temp_files.append(video_file.name)

        # Create a seed prompt for the video file
        video_prompt = SeedPrompt(value=video_file.name, dataset_name="dataset_video", data_type="video_path")

        # Add seed prompt to memory
        await sqlite_instance.add_seeds_to_memory_async(seeds=[video_prompt], added_by="test_video")

        # Retrieve and verify the seed prompts
        result = sqlite_instance.get_seeds()
        assert len(result) == 1
        assert result[0].value.endswith(".mp4")
        assert result[0].data_type == "video_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


@pytest.mark.asyncio
async def test_get_seeds_with_image(sqlite_instance: MemoryInterface):
    """Test adding and retrieving seed prompts with an image file."""
    temp_files = []
    try:
        # Create a temporary image file
        image_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)  # noqa: SIM115
        image_file.write(b"dummy image content")
        image_file.close()
        temp_files.append(image_file.name)

        # Create a seed prompt for the image file
        image_prompt = SeedPrompt(value=image_file.name, dataset_name="dataset_image", data_type="image_path")

        # Add seed prompt to memory
        await sqlite_instance.add_seeds_to_memory_async(seeds=[image_prompt], added_by="test_image")

        # Retrieve and verify the seed prompts
        result = sqlite_instance.get_seeds()
        print(result)
        assert len(result) == 1
        assert result[0].value.endswith(".png")
        assert result[0].data_type == "image_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


@pytest.mark.asyncio
async def test_get_seeds_with_value_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", data_type="text"),
        SeedPrompt(value="another prompt", dataset_name="dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(value="prompt1")
    assert len(result) == 1
    assert result[0].value == "prompt1"


@pytest.mark.asyncio
async def test_get_seeds_with_dataset_name_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(dataset_name="dataset1")
    assert len(result) == 1
    assert result[0].dataset_name == "dataset1"


@pytest.mark.asyncio
async def test_get_seeds_with_dataset_name_pattern_startswith(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="harm_category_1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="harm_category_2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="other_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(dataset_name_pattern="harm%")
    assert len(result) == 2
    assert all(seed.dataset_name.startswith("harm") for seed in result)


@pytest.mark.asyncio
async def test_get_seeds_with_dataset_name_pattern_contains(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="test_harm_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="another_harm_set", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="unrelated_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(dataset_name_pattern="%harm%")
    assert len(result) == 2
    assert all("harm" in seed.dataset_name for seed in result)


@pytest.mark.asyncio
async def test_get_seeds_with_dataset_name_pattern_endswith(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset_test", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="another_test", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_other", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(dataset_name_pattern="%test")
    assert len(result) == 2
    assert all(seed.dataset_name.endswith("test") for seed in result)


@pytest.mark.asyncio
async def test_get_seeds_dataset_name_takes_precedence_over_pattern(sqlite_instance: MemoryInterface):
    """Test that dataset_name exact match takes precedence over pattern matching"""
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="harm_exact", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="harm_other", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    # When both are provided, exact match should be used
    result = sqlite_instance.get_seeds(dataset_name="harm_exact", dataset_name_pattern="harm%")
    assert len(result) == 1
    assert result[0].dataset_name == "harm_exact"


@pytest.mark.asyncio
async def test_get_seeds_with_added_by_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", added_by="user2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    result = sqlite_instance.get_seeds(added_by="user1")
    assert len(result) == 1
    assert result[0].added_by == "user1"


@pytest.mark.asyncio
async def test_get_seeds_with_source_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", source="source1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", source="source2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(source="source1")
    assert len(result) == 1
    assert result[0].source == "source1"


@pytest.mark.asyncio
async def test_get_seeds_with_harm_categories_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=["category1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]


@pytest.mark.asyncio
async def test_get_seeds_with_authors_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(authors=["author1"])
    assert len(result) == 1
    assert result[0].authors == ["author1"]


@pytest.mark.asyncio
async def test_get_seeds_with_groups_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", groups=["group1"], data_type="text"),
        SeedPrompt(value="prompt2", groups=["group2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(groups=["group1"])
    assert len(result) == 1
    assert result[0].groups == ["group1"]


@pytest.mark.asyncio
async def test_get_seeds_with_parameters_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", parameters=["param1"], data_type="text"),
        SeedPrompt(value="prompt2", parameters=["param2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(parameters=["param1"])
    assert len(result) == 1
    assert isinstance(result[0], SeedPrompt)
    assert result[0].parameters == ["param1"]


@pytest.mark.asyncio
async def test_get_seeds_with_metadata_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", data_type="text", metadata={"key1": "value1", "key2": "value2"}),
        SeedPrompt(value="prompt2", data_type="text", metadata={"key1": "value2"}),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(metadata={"key1": "value1"})
    assert len(result) == 1
    assert result[0].metadata == {"key1": "value1", "key2": "value2"}


@pytest.mark.asyncio
async def test_get_seeds_with_multiple_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", added_by="user2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    result = sqlite_instance.get_seeds(dataset_name="dataset1", added_by="user1")
    assert len(result) == 1
    assert result[0].dataset_name == "dataset1"
    assert result[0].added_by == "user1"


@pytest.mark.asyncio
async def test_get_seeds_with_empty_list_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["harm1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["harm2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=[], authors=[])
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_seeds_with_single_element_list_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=["category1"], authors=["author1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]
    assert result[0].authors == ["author1"]


@pytest.mark.asyncio
async def test_get_seeds_with_multiple_elements_list_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(
            value="prompt1",
            harm_categories=["category1", "category2"],
            authors=["author1", "author2"],
            data_type="text",
        ),
        SeedPrompt(value="prompt2", harm_categories=["category3"], authors=["author3"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=["category1", "category2"], authors=["author1", "author2"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1", "category2"]
    assert result[0].authors == ["author1", "author2"]


@pytest.mark.asyncio
async def test_get_seeds_with_multiple_elements_list_filters_additional(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(
            value="prompt1",
            harm_categories=["category1", "category2"],
            authors=["author1", "author2"],
            data_type="text",
        ),
        SeedPrompt(value="prompt2", harm_categories=["category3"], authors=["author3"], data_type="text"),
        SeedPrompt(
            value="prompt3",
            harm_categories=["category1", "category3"],
            authors=["author1", "author3"],
            data_type="text",
        ),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=["category1", "category3"], authors=["author1", "author3"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1", "category3"]
    assert result[0].authors == ["author1", "author3"]


@pytest.mark.asyncio
async def test_get_seeds_with_substring_filters_harm_categories(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(harm_categories=["ory1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]

    result = sqlite_instance.get_seeds(authors=["auth"])
    assert len(result) == 2
    assert result[0].authors == ["author1"]
    assert result[1].authors == ["author2"]


@pytest.mark.asyncio
async def test_get_seeds_with_substring_filters_groups(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", groups=["group1"], data_type="text"),
        SeedPrompt(value="prompt2", groups=["group2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(groups=["oup1"])
    assert len(result) == 1
    assert result[0].groups == ["group1"]

    result = sqlite_instance.get_seeds(groups=["oup"])
    assert len(result) == 2
    assert result[0].groups == ["group1"]
    assert result[1].groups == ["group2"]


@pytest.mark.asyncio
async def test_get_seeds_with_substring_filters_parameters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", parameters=["param1"], data_type="text"),
        SeedPrompt(value="prompt2", parameters=["param2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = sqlite_instance.get_seeds(parameters=["ram1"])
    assert len(result) == 1
    assert isinstance(result[0], SeedPrompt)
    assert result[0].parameters == ["param1"]

    result = sqlite_instance.get_seeds(parameters=["ram"])
    assert len(result) == 2
    assert isinstance(result[0], SeedPrompt)
    assert isinstance(result[1], SeedPrompt)
    assert result[0].parameters == ["param1"]
    assert result[1].parameters == ["param2"]


@pytest.mark.asyncio
async def test_add_seed_prompts_to_memory_empty_list(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = []
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = sqlite_instance.get_seeds(dataset_name="test_dataset")
    assert len(stored_prompts) == 0


@pytest.mark.asyncio
async def test_add_seed_prompts_duplicate_entries_same_dataset(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = sqlite_instance.get_seeds(dataset_name="test_dataset")
    assert len(stored_prompts) == 2

    # Try to add prompt list with one duplicate prompt and one new prompt
    duplicate_prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=duplicate_prompts, added_by="tester")

    # Validate that only new prompt is added and the total prompt count is 3
    stored_prompts = sqlite_instance.get_seeds(dataset_name="test_dataset")
    assert len(stored_prompts) == 3


@pytest.mark.asyncio
async def test_add_seed_prompts_duplicate_entries_different_datasets(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = sqlite_instance.get_seeds(dataset_name="test_dataset")
    assert len(stored_prompts) == 2

    # Try to add prompt list with one duplicate prompt and one new prompt
    duplicate_prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=duplicate_prompts, added_by="tester")

    # Validate that only new prompt is added and the total prompt count is 3
    stored_prompts = sqlite_instance.get_seeds()
    assert len(stored_prompts) == 4


def test_get_seed_dataset_names_empty(sqlite_instance: MemoryInterface):
    assert sqlite_instance.get_seed_dataset_names() == []


@pytest.mark.asyncio
async def test_get_seed_dataset_names_single(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    seed_prompt = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_prompt])
    assert sqlite_instance.get_seed_dataset_names() == [dataset_name]


@pytest.mark.asyncio
async def test_get_seed_dataset_names_single_dataset_multiple_entries(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    seed_prompt1 = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    seed_prompt2 = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_prompt1, seed_prompt2])
    assert sqlite_instance.get_seed_dataset_names() == [dataset_name]


@pytest.mark.asyncio
async def test_get_seed_dataset_names_multiple(sqlite_instance: MemoryInterface):
    dataset_names = [f"dataset_{i}" for i in range(5)]
    seed_prompts = [
        SeedPrompt(value=f"value_{i}", dataset_name=dataset_name, added_by="tester", data_type="text")
        for i, dataset_name in enumerate(dataset_names)
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)
    assert len(sqlite_instance.get_seed_dataset_names()) == 5
    assert sorted(sqlite_instance.get_seed_dataset_names()) == sorted(dataset_names)


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_empty_list(sqlite_instance: MemoryInterface):
    prompt_group = SeedGroup(seeds=[SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)])
    prompt_group.seeds = []
    with pytest.raises(ValueError, match="Seed group must have at least one seed."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_single_element(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="tester")
    assert len(sqlite_instance.get_seeds()) == 1


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_multiple_elements(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="tester")
    assert len(sqlite_instance.get_seeds()) == 2
    assert len(sqlite_instance.get_seed_groups()) == 1


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_no_elements(sqlite_instance: MemoryInterface):
    with pytest.raises(ValueError, match="SeedGroup cannot be empty."):
        prompt_group = SeedGroup(seeds=[])
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_single_element_no_added_by(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    with pytest.raises(ValueError, match="The 'added_by' attribute must be set for each prompt."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_multiple_elements_no_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    with pytest.raises(ValueError, match="The 'added_by' attribute must be set for each prompt."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_inconsistent_group_ids(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")

    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    prompt_group.seeds[0].prompt_group_id = uuid4()

    with pytest.raises(ValueError):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_single_element_with_added_by(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])
    assert len(sqlite_instance.get_seeds()) == 1


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_multiple_elements_with_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])
    assert len(sqlite_instance.get_seeds()) == 2


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_multiple_groups_with_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt3 = SeedPrompt(value="Test prompt 3", added_by="tester", data_type="text", sequence=0, role="user")
    prompt4 = SeedPrompt(value="Test prompt 4", added_by="tester", data_type="text", sequence=1, role="user")

    prompt_group1 = SeedGroup(seeds=[prompt1, prompt2])
    prompt_group2 = SeedGroup(seeds=[prompt3, prompt4])

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group1, prompt_group2])
    assert len(sqlite_instance.get_seeds()) == 4
    groups_from_memory = sqlite_instance.get_seed_groups()
    assert len(groups_from_memory) == 2
    assert groups_from_memory[0].prompts[0].id != groups_from_memory[1].prompts[1].id
    assert groups_from_memory[0].prompts[0].prompt_group_id == groups_from_memory[0].prompts[1].prompt_group_id
    assert groups_from_memory[1].prompts[0].prompt_group_id == groups_from_memory[1].prompts[1].prompt_group_id


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_with_all_modalities(sqlite_instance: MemoryInterface):
    """Test adding multiple prompt groups with different modalities using temporary files."""
    temp_files = []
    temp_dir = tempfile.TemporaryDirectory()
    sqlite_instance.results_path = temp_dir.name
    try:
        # Create a temporary image file
        image_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)  # noqa: SIM115
        image_file.write(b"dummy image content")
        image_file.close()
        temp_files.append(image_file.name)

        # Create a temporary audio file
        audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115
        audio_file.write(b"dummy audio content")
        audio_file.close()
        temp_files.append(audio_file.name)

        # Create a temporary video file
        video_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)  # noqa: SIM115
        video_file.write(b"dummy video content")
        video_file.close()
        temp_files.append(video_file.name)

        # Create prompts with the temporary file paths
        prompt1 = SeedPrompt(
            value=image_file.name, added_by="testmultimodal", data_type="image_path", sequence=0, role="user"
        )
        prompt2 = SeedPrompt(
            value=audio_file.name, added_by="testmultimodal", data_type="audio_path", sequence=1, role="system"
        )
        prompt3 = SeedPrompt(
            value=video_file.name, added_by="testmultimodal", data_type="video_path", sequence=2, role="system"
        )
        prompt4 = SeedPrompt(
            value="Test prompt 4", added_by="testmultimodal", data_type="text", sequence=3, role="user"
        )

        # Create SeedGroup
        seed_group1 = SeedGroup(seeds=[prompt1, prompt2, prompt3, prompt4])

        # Add prompt groups to memory
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[seed_group1])

        # Assert the total number of prompts in memory
        assert len(sqlite_instance.get_seeds(added_by="testmultimodal")) == 4

        # Retrieve and verify prompt groups from memory
        groups_from_memory = sqlite_instance.get_seed_groups(added_by="testmultimodal")
        assert len(groups_from_memory) == 1

        # Verify prompt group IDs are consistent within each group
        expected_prompt_group_id = groups_from_memory[0].prompts[0].prompt_group_id
        assert groups_from_memory[0].prompts[0].prompt_group_id == expected_prompt_group_id
        assert groups_from_memory[0].prompts[1].prompt_group_id == expected_prompt_group_id
        assert groups_from_memory[0].prompts[2].prompt_group_id == expected_prompt_group_id
        assert groups_from_memory[0].prompts[3].prompt_group_id == expected_prompt_group_id

        # Verify the specific data types and values
        assert groups_from_memory[0].prompts[0].data_type == "image_path"
        assert groups_from_memory[0].prompts[0].value.endswith(".png")
        assert groups_from_memory[0].prompts[1].data_type == "audio_path"
        assert groups_from_memory[0].prompts[1].value.endswith(".wav")
        assert groups_from_memory[0].prompts[2].data_type == "video_path"
        assert groups_from_memory[0].prompts[2].value.endswith(".mp4")
        assert groups_from_memory[0].prompts[3].data_type == "text"
        assert groups_from_memory[0].prompts[3].value == "Test prompt 4"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)
        temp_dir.cleanup()


@pytest.mark.asyncio
async def test_add_seed_groups_to_memory_with_textimage_modalities(sqlite_instance: MemoryInterface):
    """Test adding multiple prompt groups with text and image modalities using temporary files."""
    temp_files = []
    temp_dir = tempfile.TemporaryDirectory()
    sqlite_instance.results_path = temp_dir.name
    try:
        # Create a temporary image file
        image_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)  # noqa: SIM115
        image_file.write(b"dummy image content")
        image_file.close()
        temp_files.append(image_file.name)

        # Create prompts with the temporary file paths
        prompt1 = SeedPrompt(
            value=image_file.name, added_by="testtextimagemultimodal", data_type="image_path", sequence=0, role="user"
        )
        prompt2 = SeedPrompt(
            value="Test prompt 2", added_by="testtextimagemultimodal", data_type="text", sequence=3, role="user"
        )

        # Create SeedGroup
        seed_group1 = SeedGroup(seeds=[prompt1, prompt2])

        # Add prompt groups to memory
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[seed_group1])

        # Assert the total number of prompts in memory
        assert len(sqlite_instance.get_seeds(added_by="testtextimagemultimodal")) == 2

        # Retrieve and verify prompt groups from memory
        groups_from_memory = sqlite_instance.get_seed_groups(added_by="testtextimagemultimodal")
        assert len(groups_from_memory) == 1

        # Verify prompt group IDs are consistent within each group
        expected_prompt_group_id = groups_from_memory[0].prompts[0].prompt_group_id
        assert groups_from_memory[0].prompts[0].prompt_group_id == expected_prompt_group_id
        assert groups_from_memory[0].prompts[1].prompt_group_id == expected_prompt_group_id

        # Verify the specific data types and values
        assert groups_from_memory[0].prompts[0].data_type == "image_path"
        assert groups_from_memory[0].prompts[0].value.endswith(".png")
        assert groups_from_memory[0].prompts[1].data_type == "text"
        assert groups_from_memory[0].prompts[1].value == "Test prompt 2"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)
        temp_dir.cleanup()


@pytest.mark.asyncio
async def test_get_seeds_with_param_filters(sqlite_instance: MemoryInterface):
    template_value = "Test template {{ param1 }}"
    dataset_name = "dataset_1"
    harm_categories = ["category1"]
    added_by = "tester"
    parameters = ["param1"]
    template = SeedPrompt(
        value=template_value,
        dataset_name=dataset_name,
        parameters=parameters,
        harm_categories=harm_categories,
        added_by=added_by,
        data_type="text",
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=[template])

    templates = sqlite_instance.get_seeds(
        value=template_value,
        dataset_name=dataset_name,
        harm_categories=harm_categories,
        added_by=added_by,
        parameters=parameters,
    )
    assert len(templates) == 1
    assert templates[0].value == template_value


def test_get_seed_groups_empty(sqlite_instance: MemoryInterface):
    assert sqlite_instance.get_seed_groups() == []


@pytest.mark.asyncio
async def test_get_seed_groups_with_dataset_name(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    prompt_group = SeedGroup(
        seeds=[
            SeedPrompt(value="Test prompt", dataset_name=dataset_name, added_by="tester", data_type="text", sequence=0)
        ]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])

    groups = sqlite_instance.get_seed_groups(dataset_name=dataset_name)
    assert len(groups) == 1
    assert groups[0].prompts[0].dataset_name == dataset_name


@pytest.mark.asyncio
async def test_get_seed_groups_with_dataset_name_pattern_startswith(sqlite_instance: MemoryInterface):
    groups_to_add = [
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 1",
                    dataset_name="harm_category_1",
                    added_by="tester",
                    data_type="text",
                    sequence=0,
                )
            ]
        ),
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 2",
                    dataset_name="harm_category_2",
                    added_by="tester",
                    data_type="text",
                    sequence=0,
                )
            ]
        ),
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 3", dataset_name="other_dataset", added_by="tester", data_type="text", sequence=0
                )
            ]
        ),
    ]
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups_to_add)

    groups = sqlite_instance.get_seed_groups(dataset_name_pattern="harm%")
    assert len(groups) == 2
    assert all(group.prompts[0].dataset_name.startswith("harm") for group in groups)


@pytest.mark.asyncio
async def test_get_seed_groups_with_dataset_name_pattern_contains(sqlite_instance: MemoryInterface):
    groups_to_add = [
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 1",
                    dataset_name="test_harm_dataset",
                    added_by="tester",
                    data_type="text",
                    sequence=0,
                )
            ]
        ),
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 2",
                    dataset_name="another_harm_set",
                    added_by="tester",
                    data_type="text",
                    sequence=0,
                )
            ]
        ),
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 3",
                    dataset_name="unrelated_dataset",
                    added_by="tester",
                    data_type="text",
                    sequence=0,
                )
            ]
        ),
    ]
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups_to_add)

    groups = sqlite_instance.get_seed_groups(dataset_name_pattern="%harm%")
    assert len(groups) == 2
    assert all("harm" in group.prompts[0].dataset_name for group in groups)


@pytest.mark.asyncio
async def test_get_seed_groups_dataset_name_takes_precedence_over_pattern(sqlite_instance: MemoryInterface):
    """Test that dataset_name exact match takes precedence over pattern matching"""
    groups_to_add = [
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 1", dataset_name="harm_exact", added_by="tester", data_type="text", sequence=0
                )
            ]
        ),
        SeedGroup(
            seeds=[
                SeedPrompt(
                    value="Test prompt 2", dataset_name="harm_other", added_by="tester", data_type="text", sequence=0
                )
            ]
        ),
    ]
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups_to_add)

    # When both are provided, exact match should be used
    groups = sqlite_instance.get_seed_groups(dataset_name="harm_exact", dataset_name_pattern="harm%")
    assert len(groups) == 1
    assert groups[0].prompts[0].dataset_name == "harm_exact"


@pytest.mark.asyncio
async def test_get_seed_groups_with_multiple_filters(sqlite_instance: MemoryInterface):
    dataset_name = "dataset_1"
    data_types = ["text"]
    harm_categories = ["category1"]
    added_by = "tester"
    group = SeedGroup(
        seeds=[
            SeedPrompt(
                value="Test prompt",
                dataset_name=dataset_name,
                harm_categories=harm_categories,
                added_by=added_by,
                sequence=0,
                data_type="text",
            )
        ]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group])

    groups = sqlite_instance.get_seed_groups(
        dataset_name=dataset_name,
        data_types=data_types,
        harm_categories=harm_categories,
        added_by=added_by,
    )
    assert len(groups) == 1
    assert not groups[0].objective
    assert groups[0].prompts[0].dataset_name == dataset_name
    assert groups[0].prompts[0].added_by == added_by


@pytest.mark.asyncio
async def test_get_seed_groups_multiple_groups(sqlite_instance: MemoryInterface):
    group1 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 1", dataset_name="dataset_1", added_by="user1", sequence=0, data_type="text")]
    )
    group2 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 2", dataset_name="dataset_2", added_by="user2", sequence=0, data_type="text")]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group1, group2])

    groups = sqlite_instance.get_seed_groups()
    assert len(groups) == 2


@pytest.mark.asyncio
async def test_get_seed_groups_multiple_groups_with_unique_ids(sqlite_instance: MemoryInterface):
    group1 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 1", dataset_name="dataset_1", added_by="user1", sequence=0, data_type="text")]
    )
    group2 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 2", dataset_name="dataset_2", added_by="user2", sequence=0, data_type="text")]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group1, group2])

    groups = sqlite_instance.get_seed_groups()
    assert len(groups) == 2
    # Check that each group has a unique prompt_group_id
    assert groups[0].prompts[0].prompt_group_id != groups[1].prompts[0].prompt_group_id


@pytest.mark.asyncio
async def test_get_seeds_by_hash(sqlite_instance: MemoryInterface):
    entries = [
        SeedPrompt(value="Hello 1", data_type="text"),
        SeedPrompt(value="Hello 2", data_type="text"),
    ]

    hello_1_hash = "724c531a3bc130eb46fbc4600064779552682ef4f351976fe75d876d94e8088c"

    await sqlite_instance.add_seeds_to_memory_async(seeds=entries, added_by="rlundeen")
    retrieved_entries = sqlite_instance.get_seeds(value_sha256=[hello_1_hash])

    assert len(retrieved_entries) == 1
    assert retrieved_entries[0].value == "Hello 1"
    assert retrieved_entries[0].value_sha256 == hello_1_hash


@pytest.mark.asyncio
async def test_add_seed_prompts_no_serialization_for_text(sqlite_instance: MemoryInterface):
    """Test that text prompts don't go through serialization"""
    text_prompt = SeedPrompt(value="Simple text prompt", dataset_name="test_dataset", data_type="text")
    original_value = text_prompt.value

    # Mock the _serialize_seed_value method
    with patch.object(sqlite_instance, "_serialize_seed_value") as mock_serialize:
        await sqlite_instance.add_seeds_to_memory_async(seeds=[text_prompt], added_by="test_user")

        # Verify that _serialize_seed_value was NOT called for text
        mock_serialize.assert_not_called()

        # Verify that the prompt value was not changed
        assert text_prompt.value == original_value


@pytest.mark.asyncio
async def test_add_seed_groups_with_objective_added_to_all_prompts(sqlite_instance: MemoryInterface):
    """Test objective is added to all_prompts list"""
    # Create prompts and objective
    prompt1 = SeedPrompt(value="Test prompt 1", data_type="text", sequence=0, added_by="test_user", role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", data_type="text", sequence=0, added_by="test_user")
    objective = SeedObjective(value="Test objective")

    # Create a prompt group with both prompts and objective
    prompt_group = SeedGroup(seeds=[prompt1, prompt2, objective])

    # Mock the add_seeds_to_memory_async method to capture what gets passed to it
    original_add_method = sqlite_instance.add_seeds_to_memory_async
    captured_prompts = []

    async def mock_add_seeds_to_memory_async(*, seeds, added_by=None):
        captured_prompts.extend(seeds)
        return await original_add_method(seeds=seeds, added_by=added_by)

    with patch.object(sqlite_instance, "add_seeds_to_memory_async", side_effect=mock_add_seeds_to_memory_async):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="test_user")

        # Verify that both prompts and the objective were added to all_prompts
        assert len(captured_prompts) == 3

        # Check that the two SeedPrompts are included
        seed_prompts = [p for p in captured_prompts if isinstance(p, SeedPrompt)]
        assert len(seed_prompts) == 2
        assert any(p.value == "Test prompt 1" for p in seed_prompts)
        assert any(p.value == "Test prompt 2" for p in seed_prompts)

        # Check that the objective is included
        objectives = [p for p in captured_prompts if isinstance(p, SeedObjective)]
        assert len(objectives) == 1
        assert objectives[0].value == "Test objective"


@pytest.mark.asyncio
async def test_add_seed_groups_without_objective_only_prompts_added(sqlite_instance: MemoryInterface):
    """Test when no objective, only prompts are added to all_prompts"""
    # Create prompts without objective
    prompt1 = SeedPrompt(value="Test prompt 1", data_type="text", sequence=0, added_by="test_user", role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", data_type="text", sequence=1, added_by="test_user", role="user")

    # Create a prompt group with only prompts (no objective)
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])

    # Mock the add_seeds_to_memory_async method to capture what gets passed to it
    original_add_method = sqlite_instance.add_seeds_to_memory_async
    captured_prompts = []

    async def mock_add_seeds_to_memory_async(*, seeds, added_by=None):
        captured_prompts.extend(seeds)
        return await original_add_method(seeds=seeds, added_by=added_by)

    with patch.object(sqlite_instance, "add_seeds_to_memory_async", side_effect=mock_add_seeds_to_memory_async):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="test_user")

        # Verify that only the prompts were added to all_prompts
        assert len(captured_prompts) == 2

        # Check that all are SeedPrompts (no objectives should be present)
        assert all(isinstance(p, SeedPrompt) for p in captured_prompts)
        assert any(p.value == "Test prompt 1" for p in captured_prompts)
        assert any(p.value == "Test prompt 2" for p in captured_prompts)


@pytest.mark.asyncio
async def test_add_seed_datasets_to_memory_async(sqlite_instance: MemoryInterface):
    """Test adding seed datasets to memory."""
    prompts = [SeedPrompt(value="test prompt", dataset_name="test_dataset", data_type="text")]
    dataset = SeedDataset(seeds=prompts, dataset_name="test_dataset", added_by="test_user")

    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="test_user")

    result = sqlite_instance.get_seeds(dataset_name="test_dataset")
    assert len(result) == 1
    assert result[0].value == "test prompt"
    assert result[0].added_by == "test_user"


@pytest.mark.asyncio
async def test_get_seed_groups_deduplication_and_filtering(sqlite_instance: MemoryInterface):
    """Test that get_seed_groups returns complete groups and deduplicates results."""
    temp_files = []
    temp_dir = tempfile.TemporaryDirectory()
    sqlite_instance.results_path = temp_dir.name
    try:
        # Create a temporary audio file
        audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115
        audio_file.write(b"dummy audio content")
        audio_file.close()
        temp_files.append(audio_file.name)

        # Create prompts
        prompt_text = SeedPrompt(
            value="Test text prompt", added_by="test_dedupe", data_type="text", sequence=0, role="user"
        )
        prompt_audio = SeedPrompt(
            value=audio_file.name, added_by="test_dedupe", data_type="audio_path", sequence=1, role="user"
        )

        # Create SeedGroup with both prompts
        seed_group = SeedGroup(seeds=[prompt_text, prompt_audio])

        # Add prompt group to memory
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[seed_group], added_by="test_dedupe")

        # Test 1: Filter by audio_path should return the whole group (including text)
        groups_audio = sqlite_instance.get_seed_groups(data_types=["audio_path"])
        assert len(groups_audio) == 1
        assert len(groups_audio[0].prompts) == 2

        # Verify both modalities are present
        data_types = {p.data_type for p in groups_audio[0].prompts}
        assert "text" in data_types
        assert "audio_path" in data_types

        # Test 2: Filter by both types should return the group only once (deduplication)
        groups_both = sqlite_instance.get_seed_groups(data_types=["text", "audio_path"])
        assert len(groups_both) == 1
        assert len(groups_both[0].prompts) == 2

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)
        temp_dir.cleanup()


@pytest.mark.asyncio
async def test_get_seed_groups_filter_by_count(sqlite_instance: MemoryInterface):
    # Create seed prompts
    prompt1 = SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text")
    prompt2 = SeedPrompt(value="prompt2", dataset_name="test_dataset", data_type="text")
    prompt3 = SeedPrompt(value="prompt3", dataset_name="test_dataset", data_type="text")

    # Create groups
    # Group 1: 1 prompt
    group1 = SeedGroup(seeds=[prompt1])

    # Group 2: 2 prompts
    group2 = SeedGroup(seeds=[prompt2, prompt3])

    # Add groups to memory
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group1, group2], added_by="test_user")

    # Test filtering by count = 1
    groups_count_1 = sqlite_instance.get_seed_groups(group_length=[1])
    assert len(groups_count_1) == 1
    assert len(groups_count_1[0].seeds) == 1
    assert groups_count_1[0].seeds[0].value == "prompt1"

    # Test filtering by count = 2
    groups_count_2 = sqlite_instance.get_seed_groups(group_length=[2])
    assert len(groups_count_2) == 1
    assert len(groups_count_2[0].seeds) == 2

    # Test filtering by count = 3 (should be empty)
    groups_count_3 = sqlite_instance.get_seed_groups(group_length=[3])
    assert len(groups_count_3) == 0

    # Test filtering by count = [1, 2]
    groups_count_1_2 = sqlite_instance.get_seed_groups(group_length=[1, 2])
    assert len(groups_count_1_2) == 2

    # Test without filtering (should return all)
    all_groups = sqlite_instance.get_seed_groups()
    assert len(all_groups) == 2
