# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import String, event, select
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import AnswerMatches, MessagePiece, SeedDataset, SeedGroup, SeedObjective, SeedOrigin, SeedPrompt


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"harm_categories": ["hate"]},
        {"harm_categories": ["HATE%"]},
        {"harm_categories": ["other", "ä"]},
        {"data_types": ["text"]},
        {"data_types": ["audio_path"]},
        {"harm_categories": ["hate"], "data_types": ["text"]},
        {"metadata": {"scope.key": "selected"}},
        {"authors": ["Jones"]},
        {"origin": SeedOrigin.USER},
        {"value": "candidate-2"},
        {"value_sha256": [sha256(b"candidate-2").hexdigest()]},
        {"added_by": "test"},
        {"value_sha256": [sha256(b"candidate-2").hexdigest()], "added_by": "test"},
    ],
)
async def test_candidate_filters_match_stored_queries_without_writes_async(
    *, sqlite_instance: MemoryInterface, filters: dict[str, object]
) -> None:
    seeds = [
        SeedObjective(
            value=f"candidate-{index}",
            dataset_name="candidate",
            harm_categories=["Hate_Speech" if index % 2 else "other", "Ä"],
            authors=["A. Jones"],
            metadata={"scope.key": "selected" if index % 3 else "other"},
            origin=SeedOrigin.USER if index % 2 else SeedOrigin.LOCAL,
        )
        for index in range(250)
    ]
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[seed.model_copy(deep=True) for seed in seeds], added_by="test"
    )
    expected = await sqlite_instance.get_seeds_async(dataset_name="candidate", **filters)
    await sqlite_instance.remove_seeds_from_memory_async(dataset_name="candidate")
    statements: list[ClauseElement] = []

    def capture(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: "ExecutionContext",
        executemany: bool,
    ) -> None:
        if context.compiled is not None:
            statements.append(context.compiled.statement)

    event.listen(sqlite_instance.engine, "before_cursor_execute", capture)
    try:
        prepared = await sqlite_instance.prepare_seed_batch_async(seeds=seeds, added_by="test")
        assert all(seed.value_sha256 is None and seed.added_by is None for seed in seeds)
        assert all(seed.value_sha256 is not None and seed.added_by == "test" for seed in prepared.seeds)
        actual = await sqlite_instance.filter_seed_candidates_async(seeds=prepared.seeds, filters=filters)
    finally:
        event.remove(sqlite_instance.engine, "before_cursor_execute", capture)
    assert [seed.id for seed in actual] == [seed.id for seed in seeds if seed.id in {item.id for item in expected}]
    assert await sqlite_instance.get_seeds_async(dataset_name="candidate") == []
    for statement in statements:
        assert not statement.is_dml
        compiled = str(statement.compile(dialect=mssql.dialect()))
        assert "FROM [SeedPromptEntries]" not in compiled


@pytest.mark.parametrize("replace", [False, True])
async def test_prepared_seed_snapshot_is_committed_without_reprocessing_async(
    *, sqlite_instance: MemoryInterface, replace: bool
) -> None:
    source = SeedObjective(
        value="Fresh apple", dataset_name="candidate", added_by="provider", metadata={"scope": "original"}
    )
    prepared = await sqlite_instance.prepare_seed_batch_async(seeds=[source], added_by="test")
    validated = prepared.seeds[0]
    assert validated.added_by == "test"
    assert validated.date_added is not None
    assert validated.value_sha256 == sha256(b"Fresh apple").hexdigest()
    expected = validated.model_dump(mode="json")
    source.value = "Changed by caller"
    validated.value = "Changed by validator"
    validated.metadata["scope"] = "changed"
    with patch.object(
        sqlite_instance, "_prepare_seed_for_storage_async", side_effect=AssertionError("Must prepare only once")
    ):
        if replace:
            await sqlite_instance.replace_prepared_seed_batch_async(dataset_name="candidate", batch=prepared)
        else:
            await sqlite_instance.add_prepared_seed_batch_async(prepared)
    stored = await sqlite_instance.get_seeds_async(dataset_name="candidate")
    assert len(stored) == 1
    assert stored[0].model_dump(mode="json") == expected
    assert source.value_sha256 is None and source.added_by == "provider"


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.usefixtures("patch_central_database")
async def test_prepared_media_keeps_the_validated_path_hash_and_metadata_async(
    *, sqlite_instance: MemoryInterface, tmp_path: Path, replace: bool
) -> None:
    import asyncio

    from PIL import Image

    path = tmp_path / "source.png"

    def save_image() -> bytes:
        Image.new("RGB", (2, 2), color="red").save(path)
        return path.read_bytes()

    content = await asyncio.to_thread(save_image)
    source = SeedPrompt(value=str(path), dataset_name="candidate", data_type="image_path")
    with patch.object(
        sqlite_instance, "_serialize_seed_value_async", wraps=sqlite_instance._serialize_seed_value_async
    ) as serialize:
        prepared = await sqlite_instance.prepare_seed_batch_async(seeds=[source], added_by="test")
        candidate = prepared.seeds[0]
        assert candidate.value != str(path)
        assert candidate.value_sha256 == sha256(content).hexdigest()
        assert candidate.metadata["format"] == "png"
        assert serialize.await_count == 1
        assert await sqlite_instance.get_seeds_async(dataset_name="candidate") == []
        selected = await sqlite_instance.filter_seed_candidates_async(
            seeds=prepared.seeds,
            filters={
                "value_sha256": [candidate.value_sha256],
                "metadata": {"format": "png"},
                "added_by": "test",
                "data_types": ["image_path"],
            },
        )
        assert len(selected) == 1 and selected[0].id == source.id
        if replace:
            await sqlite_instance.replace_prepared_seed_batch_async(dataset_name="candidate", batch=prepared)
        else:
            await sqlite_instance.add_prepared_seed_batch_async(prepared)
        assert serialize.await_count == 1
    stored = (await sqlite_instance.get_seeds_async(dataset_name="candidate"))[0]
    assert stored.model_dump(mode="json") == candidate.model_dump(mode="json")
    assert await asyncio.to_thread(Path(stored.value).read_bytes) == content
    assert source.value == str(path) and source.value_sha256 is None
    assert not source.metadata.get("format")


async def test_replacement_rejects_invalid_group_without_deleting_old_rows_async(
    sqlite_instance: MemoryInterface,
) -> None:
    before = SeedObjective(value="Keep me", dataset_name="candidate")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[before], added_by="user")
    shared_id = uuid4()
    candidates = [
        SeedObjective(value=str(index), dataset_name="candidate", prompt_group_id=shared_id) for index in range(2)
    ]
    with pytest.raises(ValueError, match="only have one objective"):
        await sqlite_instance.replace_seeds_for_dataset_async(
            dataset_name="candidate", seeds=candidates, added_by="test"
        )
    after = await sqlite_instance.get_seeds_async(dataset_name="candidate")
    assert [(seed.id, seed.value) for seed in after] == [(before.id, before.value)]


if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect
    from sqlalchemy.engine.interfaces import ExecutionContext
    from sqlalchemy.sql.elements import ClauseElement


def assert_original_value_in_list(original_value: str, message_pieces: Sequence[MessagePiece]):
    for piece in message_pieces:
        if piece.original_value == original_value:
            return True
    raise AssertionError(f"Original value {original_value} not found in list")


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
        result = await sqlite_instance.get_seeds_async()
        assert len(result) == 1
        assert result[0].value.endswith(".wav")
        assert result[0].data_type == "audio_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


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
        result = await sqlite_instance.get_seeds_async()
        assert len(result) == 1
        assert result[0].value.endswith(".mp4")
        assert result[0].data_type == "video_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


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
        result = await sqlite_instance.get_seeds_async()
        print(result)
        assert len(result) == 1
        assert result[0].value.endswith(".png")
        assert result[0].data_type == "image_path"

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)


async def test_get_seeds_with_value_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", data_type="text"),
        SeedPrompt(value="another prompt", dataset_name="dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(value="prompt1")
    assert len(result) == 1
    assert result[0].value == "prompt1"


async def test_get_seed_dataset_summaries(sqlite_instance: MemoryInterface):
    """Test aggregate dataset summaries without hydrating seed values."""
    group_id = uuid4()
    seeds = [
        SeedPrompt(
            value="prompt one",
            dataset_name="dataset1",
            data_type="text",
            harm_categories=["violence"],
            prompt_group_id=group_id,
        ),
        SeedPrompt(
            value="prompt two",
            dataset_name="dataset1",
            data_type="text",
            harm_categories=["hate"],
            prompt_group_id=group_id,
        ),
        SeedObjective(
            value="objective",
            dataset_name="dataset1",
            data_type="text",
            prompt_group_id=group_id,
        ),
        SeedPrompt(value="dataset2 prompt", dataset_name="dataset2", data_type="text"),
        SeedPrompt(value="unnamed prompt", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="test")

    summaries = {
        summary.dataset_name: summary for summary in (await sqlite_instance.get_seed_dataset_summaries_async())
    }

    dataset1 = summaries["dataset1"]
    assert dataset1.logical_examples == 1
    assert dataset1.seed_pieces == 3
    assert dataset1.objectives == 1
    assert dataset1.modalities == ("text",)
    assert dataset1.harm_categories == ("hate", "violence")
    assert dataset1.has_unlabeled_harm_categories is True

    dataset2 = summaries["dataset2"]
    assert dataset2.logical_examples == 1
    assert dataset2.seed_pieces == 1
    assert dataset2.objectives == 0

    unnamed = summaries[None]
    assert unnamed.logical_examples == 1
    assert unnamed.seed_pieces == 1


async def test_get_seed_dataset_summaries_merges_none_and_empty_dataset_groups(
    sqlite_instance: MemoryInterface,
):
    """Linked seeds with None and empty dataset names count as one unnamed logical example."""
    group = SeedGroup(
        seeds=[
            SeedObjective(value="objective", dataset_name=None, data_type="text"),
            SeedPrompt(value="prompt", dataset_name="", data_type="text"),
        ]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")

    summaries = await sqlite_instance.get_seed_dataset_summaries_async()

    assert len(summaries) == 1
    assert summaries[0].dataset_name is None
    assert summaries[0].logical_examples == 1
    assert summaries[0].seed_pieces == 2
    assert summaries[0].objectives == 1


async def test_get_seed_dataset_summaries_follows_a_case_insensitive_collation(
    sqlite_instance: MemoryInterface,
):
    """Summary grouping honors the column collation, so differently cased names fold together.

    Rebuilds the real column with COLLATE NOCASE exactly like the seed dedupe tests so the
    summary's GROUP BY and metadata joins apply case-insensitive equality in SQL. A
    Python-side raw-name merge would read these three spellings as separate datasets and
    report each variant's counts and metadata independently.
    """
    table = SeedEntry.__table__
    original_type = table.c.dataset_name.type
    table.drop(sqlite_instance.engine)
    table.c.dataset_name.type = String(collation="NOCASE")
    try:
        table.create(sqlite_instance.engine)

        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[
                SeedPrompt(value="prompt one", dataset_name="Dataset", data_type="text", harm_categories=["hate"]),
                SeedPrompt(
                    value="https://example.com/two",
                    dataset_name="dataset",
                    data_type="url",
                    harm_categories=["violence"],
                ),
                SeedPrompt(value="prompt three", dataset_name="DATASET", data_type="reasoning"),
            ],
            added_by="tester",
        )

        summaries = await sqlite_instance.get_seed_dataset_summaries_async()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.seed_pieces == 3
        assert summary.objectives == 0
        assert summary.modalities == ("reasoning", "text", "url")
        assert summary.harm_categories == ("hate", "violence")
        assert summary.has_unlabeled_harm_categories is True
    finally:
        table.c.dataset_name.type = original_type


async def test_get_seed_dataset_summaries_follows_a_trailing_blank_insensitive_collation(
    sqlite_instance: MemoryInterface,
):
    """T-SQL ignores trailing blanks too, so folding case alone would not have been enough."""
    table = SeedEntry.__table__
    original_type = table.c.dataset_name.type
    table.drop(sqlite_instance.engine)
    table.c.dataset_name.type = String(collation="RTRIM")
    try:
        table.create(sqlite_instance.engine)

        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[
                SeedPrompt(value="prompt one", dataset_name="alpha", data_type="text", harm_categories=["hate"]),
                SeedPrompt(
                    value="https://example.com/two",
                    dataset_name="alpha   ",
                    data_type="url",
                    harm_categories=["violence"],
                ),
                SeedPrompt(value="prompt three", dataset_name="alpha ", data_type="reasoning"),
            ],
            added_by="tester",
        )

        summaries = await sqlite_instance.get_seed_dataset_summaries_async()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.seed_pieces == 3
        assert summary.objectives == 0
        assert summary.modalities == ("reasoning", "text", "url")
        assert summary.harm_categories == ("hate", "violence")
        assert summary.has_unlabeled_harm_categories is True
    finally:
        table.c.dataset_name.type = original_type


async def test_get_seeds_with_dataset_name_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(dataset_name="dataset1")
    assert len(result) == 1
    assert result[0].dataset_name == "dataset1"


async def test_get_seeds_with_dataset_name_pattern_startswith(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="harm_category_1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="harm_category_2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="other_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(dataset_name_pattern="harm%")
    assert len(result) == 2
    assert all(seed.dataset_name.startswith("harm") for seed in result)


async def test_get_seeds_with_dataset_name_pattern_contains(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="test_harm_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="another_harm_set", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="unrelated_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(dataset_name_pattern="%harm%")
    assert len(result) == 2
    assert all("harm" in seed.dataset_name for seed in result)


async def test_get_seeds_with_dataset_name_pattern_endswith(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset_test", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="another_test", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_other", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(dataset_name_pattern="%test")
    assert len(result) == 2
    assert all(seed.dataset_name.endswith("test") for seed in result)


async def test_get_seeds_dataset_name_takes_precedence_over_pattern(sqlite_instance: MemoryInterface):
    """Test that dataset_name exact match takes precedence over pattern matching"""
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="harm_exact", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="harm_other", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    # When both are provided, exact match should be used
    result = await sqlite_instance.get_seeds_async(dataset_name="harm_exact", dataset_name_pattern="harm%")
    assert len(result) == 1
    assert result[0].dataset_name == "harm_exact"


async def test_get_seeds_with_added_by_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", added_by="user2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    result = await sqlite_instance.get_seeds_async(added_by="user1")
    assert len(result) == 1
    assert result[0].added_by == "user1"


async def test_get_seeds_with_source_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", source="source1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", source="source2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(source="source1")
    assert len(result) == 1
    assert result[0].source == "source1"


async def test_get_seeds_with_harm_categories_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(harm_categories=["category1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]


async def test_get_seeds_with_authors_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(authors=["author1"])
    assert len(result) == 1
    assert result[0].authors == ["author1"]


async def test_get_seeds_with_groups_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", groups=["group1"], data_type="text"),
        SeedPrompt(value="prompt2", groups=["group2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(groups=["group1"])
    assert len(result) == 1
    assert result[0].groups == ["group1"]


async def test_get_seeds_with_parameters_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", parameters=["param1"], data_type="text"),
        SeedPrompt(value="prompt2", parameters=["param2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(parameters=["param1"])
    assert len(result) == 1
    assert isinstance(result[0], SeedPrompt)
    assert result[0].parameters == ["param1"]


async def test_get_seeds_with_metadata_filter(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", data_type="text", metadata={"key1": "value1", "key2": "value2"}),
        SeedPrompt(value="prompt2", data_type="text", metadata={"key1": "value2"}),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(metadata={"key1": "value1"})
    assert len(result) == 1
    assert result[0].metadata == {"key1": "value1", "key2": "value2"}


async def test_get_seeds_with_multiple_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", dataset_name="dataset1", added_by="user1", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="dataset2", added_by="user2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)

    result = await sqlite_instance.get_seeds_async(dataset_name="dataset1", added_by="user1")
    assert len(result) == 1
    assert result[0].dataset_name == "dataset1"
    assert result[0].added_by == "user1"


async def test_get_seeds_with_empty_list_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["harm1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["harm2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(harm_categories=[], authors=[])
    assert len(result) == 2


async def test_get_seeds_with_single_element_list_filters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(harm_categories=["category1"], authors=["author1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]
    assert result[0].authors == ["author1"]


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

    result = await sqlite_instance.get_seeds_async(
        harm_categories=["category1", "category2"], authors=["author1", "author2"]
    )
    assert len(result) == 1
    assert result[0].harm_categories == ["category1", "category2"]
    assert result[0].authors == ["author1", "author2"]


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

    result = await sqlite_instance.get_seeds_async(
        harm_categories=["category1", "category3"], authors=["author1", "author3"]
    )
    assert len(result) == 1
    assert result[0].harm_categories == ["category1", "category3"]
    assert result[0].authors == ["author1", "author3"]


async def test_get_seeds_with_substring_filters_harm_categories(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", harm_categories=["category1"], authors=["author1"], data_type="text"),
        SeedPrompt(value="prompt2", harm_categories=["category2"], authors=["author2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(harm_categories=["ory1"])
    assert len(result) == 1
    assert result[0].harm_categories == ["category1"]

    result = await sqlite_instance.get_seeds_async(authors=["auth"])
    assert len(result) == 2
    assert result[0].authors == ["author1"]
    assert result[1].authors == ["author2"]


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"authors": ["Frédéric Dubut"]}, ["p1"]),
        ({"authors": ["Frédéric"]}, ["p1"]),
        ({"harm_categories": ["ÉTHIQUE"]}, ["p1"]),
        ({"harm_categories": ["Überwachung"]}, ["p2"]),
        ({"authors": ['"AJ"']}, ["p3"]),
        ({"authors": ["C:\\red"]}, ["p3"]),
    ],
)
async def test_get_seeds_with_list_filters_matches_non_ascii_values(
    sqlite_instance: MemoryInterface, filters: dict[str, list[str]], expected: list[str]
):
    seed_prompts = [
        SeedPrompt(value="p1", harm_categories=["ÉTHIQUE"], authors=["Frédéric Dubut"], data_type="text"),
        SeedPrompt(value="p2", harm_categories=["Überwachung"], authors=["Frederic Smith"], data_type="text"),
        SeedPrompt(value="p3", authors=['Ann "AJ" Jones', "C:\\red-team"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(**filters)
    assert [seed.value for seed in result] == expected


@pytest.mark.parametrize("field", ["authors", "harm_categories", "groups", "parameters"])
@pytest.mark.parametrize(
    "needle, other",
    [
        ("\u00e9", r"\u00e9"),
        ("\U0001f642", r"\ud83d\ude42"),
        ("u00e9", "\u00e9"),
    ],
)
async def test_get_seeds_with_list_filters_matches_decoded_elements(
    sqlite_instance: MemoryInterface, field: str, needle: str, other: str
) -> None:
    seeds = [
        SeedPrompt(value="match", data_type="text", **{field: [f"prefix {needle} suffix"]}),
        SeedPrompt(value="other", data_type="text", **{field: [other]}),
        SeedPrompt(value="empty", data_type="text", **{field: []}),
        SeedPrompt(value="null", data_type="text", **{field: None}),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="test")

    result = await sqlite_instance.get_seeds_async(**{field: [needle]})

    assert [seed.value for seed in result] == ["match"]


@pytest.mark.parametrize("field", ["authors", "harm_categories", "groups", "parameters"])
async def test_get_seed_groups_with_list_filters_matches_decoded_elements(
    sqlite_instance: MemoryInterface, field: str
) -> None:
    groups = [
        SeedGroup(
            seeds=[
                SeedPrompt(value=label, data_type="text", sequence=0, role="user", **{field: [value]}),
                SeedPrompt(value=f"{label}-companion", data_type="text", sequence=1, role="assistant"),
            ]
        )
        for label, value in [("match", "\u00e9"), ("literal", r"\u00e9")]
    ]
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups, added_by="test")

    result = await sqlite_instance.get_seed_groups_async(**{field: ["\u00e9"]})

    assert len(result) == 1
    assert {seed.value for seed in result[0].seeds} == {"match", "match-companion"}


@pytest.mark.parametrize("field", ["authors", "harm_categories", "groups", "parameters"])
async def test_seed_list_substring_filters_preserve_sqlite_like_semantics(
    sqlite_instance: MemoryInterface, field: str
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="match", data_type="text", **{field: ["ANN", "\u00c9THIQUE"]})],
        added_by="test",
    )

    for needle in ["ann", "a_n", "a%n", "\u00c9THIQUE"]:
        result = await sqlite_instance.get_seeds_async(**{field: [needle]})
        assert [seed.value for seed in result] == ["match"]
    assert await sqlite_instance.get_seeds_async(**{field: ["\u00e9thique"]}) == []


@pytest.mark.parametrize(
    "dialect, expansion", [(sqlite.dialect(), "json_each(json_extract("), (mssql.dialect(), "OPENJSON(JSON_QUERY(")]
)
def test_seed_list_substring_filters_decode_json_on_both_dialects(
    sqlite_instance: MemoryInterface, dialect: "Dialect", expansion: str
) -> None:
    needle = "Fr\u00e9d"
    conditions = sqlite_instance._build_seed_filter_conditions(authors=[needle, r"\u00e9"])
    statement = select(SeedEntry.id).where(*conditions)
    compiled = statement.compile(dialect=dialect)
    sql = str(compiled)

    assert sql.count(expansion) == 2
    assert sql.count("EXISTS") == 2
    assert " AND " in sql
    assert needle in compiled.params.values()
    assert r"\u00e9" in compiled.params.values()


async def test_get_seeds_with_substring_filters_groups(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", groups=["group1"], data_type="text"),
        SeedPrompt(value="prompt2", groups=["group2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(groups=["oup1"])
    assert len(result) == 1
    assert result[0].groups == ["group1"]

    result = await sqlite_instance.get_seeds_async(groups=["oup"])
    assert len(result) == 2
    assert result[0].groups == ["group1"]
    assert result[1].groups == ["group2"]


async def test_get_seeds_with_substring_filters_parameters(sqlite_instance: MemoryInterface):
    seed_prompts = [
        SeedPrompt(value="prompt1", parameters=["param1"], data_type="text"),
        SeedPrompt(value="prompt2", parameters=["param2"], data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts, added_by="test")

    result = await sqlite_instance.get_seeds_async(parameters=["ram1"])
    assert len(result) == 1
    assert isinstance(result[0], SeedPrompt)
    assert result[0].parameters == ["param1"]

    result = await sqlite_instance.get_seeds_async(parameters=["ram"])
    assert len(result) == 2
    assert isinstance(result[0], SeedPrompt)
    assert isinstance(result[1], SeedPrompt)
    assert result[0].parameters == ["param1"]
    assert result[1].parameters == ["param2"]


async def test_add_seed_prompts_to_memory_empty_list(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = []
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = await sqlite_instance.get_seeds_async(dataset_name="test_dataset")
    assert len(stored_prompts) == 0


async def test_add_seed_prompts_duplicate_entries_same_dataset(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = await sqlite_instance.get_seeds_async(dataset_name="test_dataset")
    assert len(stored_prompts) == 2

    # Try to add prompt list with one duplicate prompt and one new prompt
    duplicate_prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=duplicate_prompts, added_by="tester")

    # Validate that only new prompt is added and the total prompt count is 3
    stored_prompts = await sqlite_instance.get_seeds_async(dataset_name="test_dataset")
    assert len(stored_prompts) == 3


async def test_add_seed_prompts_duplicates_within_one_call_are_all_stored(sqlite_instance: MemoryInterface):
    """Existing behaviour: the dedupe check looks at storage, not at the batch being added."""
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    assert len(await sqlite_instance.get_seeds_async(dataset_name="test_dataset")) == 3


async def test_add_seed_prompts_without_dataset_name_matches_any_dataset(sqlite_instance: MemoryInterface):
    """Without a dataset name the lookup is unfiltered, so the hash matches in any dataset."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text")],
        added_by="tester",
    )

    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="prompt1", data_type="text")],
        added_by="tester",
    )

    assert len(await sqlite_instance.get_seeds_async()) == 1


async def test_add_seed_prompts_dedupes_across_chunk_boundaries(sqlite_instance: MemoryInterface):
    """The lookup is chunked, so duplicates have to be caught across chunk boundaries."""
    count = sqlite_instance._MAX_BIND_VARS * 2 + 25
    first: Sequence[SeedPrompt] = [
        SeedPrompt(value=f"prompt{index}", dataset_name="test_dataset", data_type="text") for index in range(count)
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=first, added_by="tester")
    assert len(await sqlite_instance.get_seeds_async(dataset_name="test_dataset")) == count

    # Re-adding the same seeds plus one new one must only store the new one.
    second: Sequence[SeedPrompt] = [
        SeedPrompt(value=f"prompt{index}", dataset_name="test_dataset", data_type="text") for index in range(count)
    ] + [SeedPrompt(value="brand_new", dataset_name="test_dataset", data_type="text")]
    await sqlite_instance.add_seeds_to_memory_async(seeds=second, added_by="tester")

    assert len(await sqlite_instance.get_seeds_async(dataset_name="test_dataset")) == count + 1


async def test_add_seed_prompts_queries_are_batched(sqlite_instance: MemoryInterface):
    """Regression guard: the dedupe lookup must not run one query per seed."""
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value=f"prompt{index}", dataset_name="test_dataset", data_type="text") for index in range(50)
    ]

    with patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as spied:
        await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    assert spied.call_count == 1


async def test_add_seed_prompts_dedupe_honors_backend_bind_var_limit(sqlite_instance: MemoryInterface):
    """The whole statement has to fit the backend's ceiling, and the dataset name takes a bind."""
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value=f"prompt{index}", dataset_name="test_dataset", data_type="text") for index in range(120)
    ]

    with patch.object(type(sqlite_instance), "_MAX_BIND_VARS", 50):
        with patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as spied:
            await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    assert spied.call_count == 3
    # 49 hashes plus the name is 50 binds; a full 50 hashes would exceed the ceiling by one.
    assert [len(call.kwargs["value_sha256"]) for call in spied.call_args_list] == [49, 49, 22]
    assert all(len(call.kwargs["value_sha256"]) + 1 <= 50 for call in spied.call_args_list)
    assert len(await sqlite_instance.get_seeds_async(dataset_name="test_dataset")) == 120


async def test_add_seed_prompts_dedupe_uses_the_full_budget_without_a_dataset_name(
    sqlite_instance: MemoryInterface,
):
    """With no name to bind there is nothing to reserve, so the whole ceiling goes to hashes."""
    prompts: Sequence[SeedPrompt] = [SeedPrompt(value=f"prompt{index}", data_type="text") for index in range(120)]

    with patch.object(type(sqlite_instance), "_MAX_BIND_VARS", 50):
        with patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as spied:
            await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    assert [len(call.kwargs["value_sha256"]) for call in spied.call_args_list] == [50, 50, 20]


async def test_add_seed_prompts_dedupe_delegates_dataset_name_to_the_database(sqlite_instance: MemoryInterface):
    """Case sensitivity belongs to the column's collation, so the name must be compared in SQL."""
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value=f"prompt{index}", dataset_name="test_dataset", data_type="text") for index in range(3)
    ]

    with patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as spied:
        await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    spied.assert_called_once()
    assert all(call.kwargs["dataset_name"] == "test_dataset" for call in spied.call_args_list)


async def test_add_seed_prompts_dedupe_follows_a_case_insensitive_collation(sqlite_instance: MemoryInterface):
    """Azure SQL's default collation is case-insensitive, so a differently cased name is a duplicate.

    Rebuilds the real column with COLLATE NOCASE so the comparison is made by SQL rather than a
    stubbed lookup; a Python-side comparison would pass a stub but still insert a duplicate here.
    """
    table = SeedEntry.__table__
    original_type = table.c.dataset_name.type
    table.drop(sqlite_instance.engine)
    table.c.dataset_name.type = String(collation="NOCASE")
    try:
        table.create(sqlite_instance.engine)

        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="prompt1", dataset_name="Dataset", data_type="text")], added_by="tester"
        )
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="prompt1", dataset_name="dataset", data_type="text")], added_by="tester"
        )

        assert len(await sqlite_instance.get_seeds_async()) == 1
    finally:
        table.c.dataset_name.type = original_type


async def test_add_seed_prompts_dedupe_follows_a_trailing_blank_insensitive_collation(
    sqlite_instance: MemoryInterface,
):
    """T-SQL ignores trailing blanks too, so normalizing case in Python would not have been enough."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="prompt1", dataset_name="alpha", data_type="text")], added_by="tester"
    )

    real_get_seeds = sqlite_instance._execute_get_seeds

    def blank_insensitive_get_seeds(*, dataset_name=None, **kwargs):
        rows = real_get_seeds(**kwargs)
        if not dataset_name:
            return rows
        return [row for row in rows if (row.dataset_name or "").rstrip() == dataset_name.rstrip()]

    with patch.object(sqlite_instance, "_execute_get_seeds", side_effect=blank_insensitive_get_seeds):
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="prompt1", dataset_name="alpha   ", data_type="text")], added_by="tester"
        )

    assert len(await sqlite_instance.get_seeds_async()) == 1


async def test_add_seed_prompts_dedupe_groups_each_dataset_name_separately(sqlite_instance: MemoryInterface):
    """Seeds arriving for several datasets at once still get one query per dataset name."""
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="shared", dataset_name="alpha", data_type="text"),
        SeedPrompt(value="shared", dataset_name="beta", data_type="text"),
        SeedPrompt(value="loose", data_type="text"),
    ]

    with patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as spied:
        await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")

    queried = [call.kwargs["dataset_name"] for call in spied.call_args_list]
    assert sorted(queried, key=lambda name: (name is None, name or "")) == ["alpha", "beta", None]
    assert len(await sqlite_instance.get_seeds_async()) == 3


async def test_add_seed_prompts_dedupe_treats_an_empty_dataset_name_as_unfiltered(
    sqlite_instance: MemoryInterface,
):
    """An empty name filters nothing in SQL, so it must match a hash stored under any dataset."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="prompt1", dataset_name="alpha", data_type="text")], added_by="tester"
    )

    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="prompt1", dataset_name="", data_type="text")], added_by="tester"
    )

    assert len(await sqlite_instance.get_seeds_async()) == 1


async def test_add_seed_prompts_duplicate_entries_different_datasets(sqlite_instance: MemoryInterface):
    prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset", data_type="text"),
        SeedPrompt(value="prompt2", dataset_name="test_dataset", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=prompts, added_by="tester")
    stored_prompts = await sqlite_instance.get_seeds_async(dataset_name="test_dataset")
    assert len(stored_prompts) == 2

    # Try to add prompt list with one duplicate prompt and one new prompt
    duplicate_prompts: Sequence[SeedPrompt] = [
        SeedPrompt(value="prompt1", dataset_name="test_dataset2", data_type="text"),
        SeedPrompt(value="prompt3", dataset_name="test_dataset2", data_type="text"),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=duplicate_prompts, added_by="tester")

    # Validate that only new prompt is added and the total prompt count is 3
    stored_prompts = await sqlite_instance.get_seeds_async()
    assert len(stored_prompts) == 4


def _conversation_seed_group(*, dataset_name: str | None = "grouped_seeds") -> SeedGroup:
    return SeedGroup(
        seeds=[
            SeedObjective(value="Promote the false claim.", dataset_name=dataset_name, metadata={"task_id": "row"}),
            SeedPrompt(
                value="Shared system context.",
                dataset_name=dataset_name,
                role="system",
                sequence=0,
                metadata={"task_id": "row"},
            ),
            SeedPrompt(
                value="Repeat the claim.",
                dataset_name=dataset_name,
                role="user",
                sequence=1,
                metadata={"task_id": "row"},
            ),
        ]
    )


def _copy_seed_group(group: SeedGroup) -> SeedGroup:
    group_id = uuid4()
    return SeedGroup(
        seeds=[seed.model_copy(deep=True, update={"id": uuid4(), "prompt_group_id": group_id}) for seed in group.seeds]
    )


@pytest.mark.parametrize(
    "changed_field",
    ["objective", "system", "user", "metadata", "role", "sequence", "member_count", "subset", "schema", "conditions"],
)
async def test_add_seed_groups_preserves_complete_distinct_groups_async(
    *, sqlite_instance: MemoryInterface, changed_field: str
) -> None:
    original = _conversation_seed_group()
    changed = _copy_seed_group(original)
    assert changed.objective is not None
    if changed_field == "objective":
        changed.objective.value = "Promote another false claim."
    elif changed_field == "system":
        changed.prompts[0].value = "Different system context."
    elif changed_field == "user":
        changed.prompts[1].value = "Repeat a different claim."
    elif changed_field == "metadata":
        for seed in changed.seeds:
            seed.metadata = {"task_id": "different_row"}
    elif changed_field == "role":
        changed.prompts[0].role = "user"
    elif changed_field == "sequence":
        changed.prompts[1].sequence = 2
    elif changed_field == "member_count":
        changed.seeds.append(changed.prompts[0].model_copy(update={"id": uuid4()}, deep=True))
    elif changed_field == "subset":
        changed.seeds.pop()
    elif changed_field == "schema":
        changed.prompts[1].response_json_schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    elif changed_field == "conditions":
        changed.objective.conditions = (AnswerMatches(correct_answer="false claim", correct_answer_label="A"),)
    changed = SeedGroup(seeds=changed.seeds)

    for group in (original, changed):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")

    for expected in (original, changed):
        [stored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[expected.seeds[0].prompt_group_id])
        fields = {
            "value",
            "data_type",
            "seed_type",
            "metadata",
            "conditions",
            "role",
            "sequence",
            "response_json_schema",
        }
        assert [seed.model_dump(mode="json", include=fields) for seed in stored.seeds] == [
            seed.model_dump(mode="json", include=fields) for seed in expected.seeds
        ]

    for group in (original, changed):
        await sqlite_instance.add_seed_groups_to_memory_async(
            prompt_groups=[_copy_seed_group(group)], added_by="another_tester"
        )
    assert len(await sqlite_instance.get_seeds_async()) == len(original.seeds) + len(changed.seeds)


async def test_add_seed_groups_does_not_assemble_a_match_from_separate_groups_async(
    sqlite_instance: MemoryInterface,
) -> None:
    expected = _conversation_seed_group()
    for seed in expected.seeds:
        standalone = seed.model_copy(update={"id": uuid4(), "prompt_group_id": uuid4()}, deep=True)
        await sqlite_instance.add_seeds_to_memory_async(seeds=[standalone], added_by="tester")

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[expected], added_by="tester")

    [stored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[expected.seeds[0].prompt_group_id])
    assert len(stored.seeds) == 3
    assert len(await sqlite_instance.get_seeds_async()) == 6


@pytest.mark.parametrize("copy_to_new_group", [False, True])
async def test_add_seed_groups_retains_stored_companion_without_conditions_async(
    *, sqlite_instance: MemoryInterface, copy_to_new_group: bool
) -> None:
    original = SeedGroup(seeds=[SeedPrompt(value="Shared message.", dataset_name="grouped_seeds")])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[original], added_by="tester")
    [prompt] = await sqlite_instance.get_seeds_async()
    original_id = prompt.id
    group_id = uuid4() if copy_to_new_group else prompt.prompt_group_id
    prompt.prompt_group_id = group_id
    group = SeedGroup(
        seeds=[SeedObjective(value="A new objective.", dataset_name="grouped_seeds", prompt_group_id=group_id), prompt]
    )

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")

    [stored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[group_id])
    assert len(stored.seeds) == 2
    assert (stored.prompts[0].id != original_id) is copy_to_new_group
    assert prompt.id == stored.prompts[0].id
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
    assert len(await sqlite_instance.get_seeds_async()) == (3 if copy_to_new_group else 2)


async def test_add_seed_groups_extension_does_not_duplicate_stored_members_async(
    sqlite_instance: MemoryInterface,
) -> None:
    original = _conversation_seed_group()
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[original], added_by="tester")
    group_id = original.seeds[0].prompt_group_id
    extended = SeedGroup(
        seeds=[
            *(seed.model_copy(update={"id": uuid4()}, deep=True) for seed in original.seeds),
            SeedPrompt(
                value="Follow-up.", role="user", sequence=2, dataset_name="grouped_seeds", prompt_group_id=group_id
            ),
        ]
    )

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[extended], added_by="tester")

    [stored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[group_id])
    assert len(stored.seeds) == 4
    assert len(await sqlite_instance.get_seeds_async()) == 4


@pytest.mark.parametrize("dataset_name", [None, ""])
async def test_add_seed_groups_unnamed_dataset_matches_complete_named_group_async(
    *, sqlite_instance: MemoryInterface, dataset_name: str | None
) -> None:
    original = _conversation_seed_group()
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[original], added_by="tester")
    duplicate = _copy_seed_group(original)
    for seed in duplicate.seeds:
        seed.dataset_name = dataset_name

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[duplicate], added_by="tester")

    assert len(await sqlite_instance.get_seeds_async()) == 3


@pytest.mark.parametrize("collation", ["NOCASE", "RTRIM"])
async def test_add_seed_groups_dedup_respects_database_collation_async(
    *, sqlite_instance: MemoryInterface, collation: str
) -> None:
    table = SeedEntry.__table__
    original_type = table.c.dataset_name.type
    table.drop(sqlite_instance.engine)
    table.c.dataset_name.type = String(collation=collation)
    try:
        table.create(sqlite_instance.engine)
        original = _conversation_seed_group(dataset_name="Dataset")
        duplicate = _copy_seed_group(original)
        for seed in duplicate.seeds:
            seed.dataset_name = "dataset" if collation == "NOCASE" else "Dataset   "
        for group in (original, duplicate):
            await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
        assert len(await sqlite_instance.get_seeds_async()) == 3
    finally:
        table.c.dataset_name.type = original_type


@pytest.mark.parametrize(
    ("incoming_datasets", "expected_seed_count"),
    [
        (["Dataset", "dataset", None], 6),
        (["dataset", None, None], 3),
        ([None, "dataset", None], 3),
    ],
)
async def test_add_seed_groups_matches_each_stored_member_once_async(
    *, sqlite_instance: MemoryInterface, incoming_datasets: list[str | None], expected_seed_count: int
) -> None:
    table = SeedEntry.__table__
    original_type = table.c.dataset_name.type
    table.drop(sqlite_instance.engine)
    table.c.dataset_name.type = String(collation="NOCASE")
    try:
        table.create(sqlite_instance.engine)
        original = SeedGroup(
            seeds=[
                SeedPrompt(value="Shared.", role="user", sequence=0, dataset_name=dataset)
                for dataset in ["Dataset", "Elsewhere", "Elsewhere"]
            ]
        )
        incoming = _copy_seed_group(original)
        for seed, dataset in zip(incoming.seeds, incoming_datasets, strict=True):
            seed.dataset_name = dataset
        for group in (original, incoming):
            await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
        assert len(await sqlite_instance.get_seeds_async()) == expected_seed_count
    finally:
        table.c.dataset_name.type = original_type


async def test_add_seed_groups_dedup_queries_remain_bounded_async(sqlite_instance: MemoryInterface) -> None:
    groups = [_conversation_seed_group() for _ in range(12)]
    for index, group in enumerate(groups):
        group.prompts[1].value = f"User message {index}."
    with (
        patch.object(type(sqlite_instance), "_MAX_BIND_VARS", 5),
        patch.object(
            sqlite_instance, "_dispatch_memory_operation", wraps=sqlite_instance._dispatch_memory_operation
        ) as dispatch,
    ):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups, added_by="tester")
        await sqlite_instance.add_seed_groups_to_memory_async(
            prompt_groups=[_copy_seed_group(group) for group in groups], added_by="tester"
        )
    lookups = [call for call in dispatch.call_args_list if call.args[0] == "get_seeds"]
    assert len(lookups) == 8
    assert all(len(call.kwargs["value_sha256"]) <= 4 for call in lookups)
    assert len(await sqlite_instance.get_seeds_async()) == 36


async def test_get_seed_dataset_names_empty(sqlite_instance: MemoryInterface):
    assert (await sqlite_instance.get_seed_dataset_names_async()) == []


async def test_get_seed_dataset_names_single(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    seed_prompt = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_prompt])
    assert (await sqlite_instance.get_seed_dataset_names_async()) == [dataset_name]


async def test_get_seed_dataset_names_single_dataset_multiple_entries(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    seed_prompt1 = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    seed_prompt2 = SeedPrompt(value="test_value", dataset_name=dataset_name, added_by="tester", data_type="text")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_prompt1, seed_prompt2])
    assert (await sqlite_instance.get_seed_dataset_names_async()) == [dataset_name]


async def test_get_seed_dataset_names_multiple(sqlite_instance: MemoryInterface):
    dataset_names = [f"dataset_{i}" for i in range(5)]
    seed_prompts = [
        SeedPrompt(value=f"value_{i}", dataset_name=dataset_name, added_by="tester", data_type="text")
        for i, dataset_name in enumerate(dataset_names)
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seed_prompts)
    assert len(await sqlite_instance.get_seed_dataset_names_async()) == 5
    assert sorted(await sqlite_instance.get_seed_dataset_names_async()) == sorted(dataset_names)


async def test_get_seed_dataset_names_without_loading_seed_rows_async(sqlite_instance: MemoryInterface) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(value=f"objective-{index}", dataset_name=name)
            for index, name in enumerate(["first", "first", "second", None, ""])
        ],
        added_by="test",
    )

    def reject_seed_load(*_: object) -> None:
        raise AssertionError("Listing dataset names must not load full seed rows")

    event.listen(SeedEntry, "load", reject_seed_load)
    try:
        assert set(await sqlite_instance.get_seed_dataset_names_async()) == {"first", "second"}
    finally:
        event.remove(SeedEntry, "load", reject_seed_load)


async def test_add_seed_groups_to_memory_empty_list(sqlite_instance: MemoryInterface):
    prompt_group = SeedGroup(seeds=[SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)])
    prompt_group.seeds = []
    with pytest.raises(ValueError, match="Seed group must have at least one seed."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


async def test_add_seed_groups_to_memory_single_element(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="tester")
    assert len(await sqlite_instance.get_seeds_async()) == 1


async def test_add_seed_groups_to_memory_multiple_elements(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group], added_by="tester")
    assert len(await sqlite_instance.get_seeds_async()) == 2
    assert len(await sqlite_instance.get_seed_groups_async()) == 1


async def test_add_seed_groups_to_memory_no_elements(sqlite_instance: MemoryInterface):
    with pytest.raises(ValueError, match="SeedGroup cannot be empty."):
        prompt_group = SeedGroup(seeds=[])
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


async def test_add_seed_groups_to_memory_single_element_no_added_by(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    with pytest.raises(ValueError, match="The 'added_by' attribute must be set for each prompt."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


async def test_add_seed_groups_to_memory_multiple_elements_no_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    with pytest.raises(ValueError, match="The 'added_by' attribute must be set for each prompt."):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


async def test_add_seed_groups_to_memory_inconsistent_group_ids(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")

    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    prompt_group.seeds[0].prompt_group_id = uuid4()

    with pytest.raises(ValueError):
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])


async def test_add_seed_groups_to_memory_single_element_with_added_by(sqlite_instance: MemoryInterface):
    prompt = SeedPrompt(value="Test prompt", added_by="tester", data_type="text", sequence=0)
    prompt_group = SeedGroup(seeds=[prompt])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])
    assert len(await sqlite_instance.get_seeds_async()) == 1


async def test_add_seed_groups_to_memory_multiple_elements_with_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt_group = SeedGroup(seeds=[prompt1, prompt2])
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])
    assert len(await sqlite_instance.get_seeds_async()) == 2


async def test_add_seed_groups_to_memory_multiple_groups_with_added_by(sqlite_instance: MemoryInterface):
    prompt1 = SeedPrompt(value="Test prompt 1", added_by="tester", data_type="text", sequence=0, role="user")
    prompt2 = SeedPrompt(value="Test prompt 2", added_by="tester", data_type="text", sequence=1, role="user")
    prompt3 = SeedPrompt(value="Test prompt 3", added_by="tester", data_type="text", sequence=0, role="user")
    prompt4 = SeedPrompt(value="Test prompt 4", added_by="tester", data_type="text", sequence=1, role="user")

    prompt_group1 = SeedGroup(seeds=[prompt1, prompt2])
    prompt_group2 = SeedGroup(seeds=[prompt3, prompt4])

    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group1, prompt_group2])
    assert len(await sqlite_instance.get_seeds_async()) == 4
    groups_from_memory = await sqlite_instance.get_seed_groups_async()
    assert len(groups_from_memory) == 2
    assert groups_from_memory[0].prompts[0].id != groups_from_memory[1].prompts[1].id
    assert groups_from_memory[0].prompts[0].prompt_group_id == groups_from_memory[0].prompts[1].prompt_group_id
    assert groups_from_memory[1].prompts[0].prompt_group_id == groups_from_memory[1].prompts[1].prompt_group_id


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
        assert len(await sqlite_instance.get_seeds_async(added_by="testmultimodal")) == 4

        # Retrieve and verify prompt groups from memory
        groups_from_memory = await sqlite_instance.get_seed_groups_async(added_by="testmultimodal")
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
        assert len(await sqlite_instance.get_seeds_async(added_by="testtextimagemultimodal")) == 2

        # Retrieve and verify prompt groups from memory
        groups_from_memory = await sqlite_instance.get_seed_groups_async(added_by="testtextimagemultimodal")
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

    templates = await sqlite_instance.get_seeds_async(
        value=template_value,
        dataset_name=dataset_name,
        harm_categories=harm_categories,
        added_by=added_by,
        parameters=parameters,
    )
    assert len(templates) == 1
    assert templates[0].value == template_value


async def test_get_seed_groups_empty(sqlite_instance: MemoryInterface):
    assert (await sqlite_instance.get_seed_groups_async()) == []


async def test_get_seed_groups_with_dataset_name(sqlite_instance: MemoryInterface):
    dataset_name = "test_dataset"
    prompt_group = SeedGroup(
        seeds=[
            SeedPrompt(value="Test prompt", dataset_name=dataset_name, added_by="tester", data_type="text", sequence=0)
        ]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[prompt_group])

    groups = await sqlite_instance.get_seed_groups_async(dataset_name=dataset_name)
    assert len(groups) == 1
    assert groups[0].prompts[0].dataset_name == dataset_name


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

    groups = await sqlite_instance.get_seed_groups_async(dataset_name_pattern="harm%")
    assert len(groups) == 2
    assert all(group.prompts[0].dataset_name.startswith("harm") for group in groups)


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

    groups = await sqlite_instance.get_seed_groups_async(dataset_name_pattern="%harm%")
    assert len(groups) == 2
    assert all("harm" in group.prompts[0].dataset_name for group in groups)


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
    groups = await sqlite_instance.get_seed_groups_async(dataset_name="harm_exact", dataset_name_pattern="harm%")
    assert len(groups) == 1
    assert groups[0].prompts[0].dataset_name == "harm_exact"


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

    groups = await sqlite_instance.get_seed_groups_async(
        dataset_name=dataset_name,
        data_types=data_types,
        harm_categories=harm_categories,
        added_by=added_by,
    )
    assert len(groups) == 1
    assert not groups[0].objective
    assert groups[0].prompts[0].dataset_name == dataset_name
    assert groups[0].prompts[0].added_by == added_by


async def test_get_seed_groups_multiple_groups(sqlite_instance: MemoryInterface):
    group1 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 1", dataset_name="dataset_1", added_by="user1", sequence=0, data_type="text")]
    )
    group2 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 2", dataset_name="dataset_2", added_by="user2", sequence=0, data_type="text")]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group1, group2])

    groups = await sqlite_instance.get_seed_groups_async()
    assert len(groups) == 2


async def test_get_seed_groups_multiple_groups_with_unique_ids(sqlite_instance: MemoryInterface):
    group1 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 1", dataset_name="dataset_1", added_by="user1", sequence=0, data_type="text")]
    )
    group2 = SeedGroup(
        seeds=[SeedPrompt(value="Prompt 2", dataset_name="dataset_2", added_by="user2", sequence=0, data_type="text")]
    )
    await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group1, group2])

    groups = await sqlite_instance.get_seed_groups_async()
    assert len(groups) == 2
    # Check that each group has a unique prompt_group_id
    assert groups[0].prompts[0].prompt_group_id != groups[1].prompts[0].prompt_group_id


async def test_get_seeds_by_hash(sqlite_instance: MemoryInterface):
    entries = [
        SeedPrompt(value="Hello 1", data_type="text"),
        SeedPrompt(value="Hello 2", data_type="text"),
    ]

    hello_1_hash = "724c531a3bc130eb46fbc4600064779552682ef4f351976fe75d876d94e8088c"

    await sqlite_instance.add_seeds_to_memory_async(seeds=entries, added_by="rlundeen")
    retrieved_entries = await sqlite_instance.get_seeds_async(value_sha256=[hello_1_hash])

    assert len(retrieved_entries) == 1
    assert retrieved_entries[0].value == "Hello 1"
    assert retrieved_entries[0].value_sha256 == hello_1_hash


async def test_add_seed_prompts_no_serialization_for_text(sqlite_instance: MemoryInterface):
    """Test that text prompts don't go through serialization"""
    text_prompt = SeedPrompt(value="Simple text prompt", dataset_name="test_dataset", data_type="text")
    original_value = text_prompt.value

    # Mock the _serialize_seed_value_async method
    with patch.object(sqlite_instance, "_serialize_seed_value_async") as mock_serialize:
        await sqlite_instance.add_seeds_to_memory_async(seeds=[text_prompt], added_by="test_user")

        # Verify that _serialize_seed_value_async was NOT called for text
        mock_serialize.assert_not_called()

        # Verify that the prompt value was not changed
        assert text_prompt.value == original_value


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


async def test_add_seed_datasets_to_memory_async(sqlite_instance: MemoryInterface):
    """Test adding seed datasets to memory."""
    prompts = [SeedPrompt(value="test prompt", dataset_name="test_dataset", data_type="text")]
    dataset = SeedDataset(seeds=prompts, dataset_name="test_dataset", added_by="test_user")

    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="test_user")

    result = await sqlite_instance.get_seeds_async(dataset_name="test_dataset")
    assert len(result) == 1
    assert result[0].value == "test prompt"
    assert result[0].added_by == "test_user"


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
        groups_audio = await sqlite_instance.get_seed_groups_async(data_types=["audio_path"])
        assert len(groups_audio) == 1
        assert len(groups_audio[0].prompts) == 2

        # Verify both modalities are present
        data_types = {p.data_type for p in groups_audio[0].prompts}
        assert "text" in data_types
        assert "audio_path" in data_types

        # Test 2: Filter by both types should return the group only once (deduplication)
        groups_both = await sqlite_instance.get_seed_groups_async(data_types=["text", "audio_path"])
        assert len(groups_both) == 1
        assert len(groups_both[0].prompts) == 2

    finally:
        for file_path in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)
        temp_dir.cleanup()


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
    groups_count_1 = await sqlite_instance.get_seed_groups_async(group_length=[1])
    assert len(groups_count_1) == 1
    assert len(groups_count_1[0].seeds) == 1
    assert groups_count_1[0].seeds[0].value == "prompt1"

    # Test filtering by count = 2
    groups_count_2 = await sqlite_instance.get_seed_groups_async(group_length=[2])
    assert len(groups_count_2) == 1
    assert len(groups_count_2[0].seeds) == 2

    # Test filtering by count = 3 (should be empty)
    groups_count_3 = await sqlite_instance.get_seed_groups_async(group_length=[3])
    assert len(groups_count_3) == 0

    # Test filtering by count = [1, 2]
    groups_count_1_2 = await sqlite_instance.get_seed_groups_async(group_length=[1, 2])
    assert len(groups_count_1_2) == 2

    # Test without filtering (should return all)
    all_groups = await sqlite_instance.get_seed_groups_async()
    assert len(all_groups) == 2


async def test_replace_seeds_for_dataset_async_replaces_all(sqlite_instance: MemoryInterface):
    """replace_seeds_for_dataset_async swaps the target dataset's seeds and leaves others intact."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedPrompt(value="a1", dataset_name="alpha", data_type="text"),
            SeedPrompt(value="a2", dataset_name="alpha", data_type="text"),
            SeedPrompt(value="b1", dataset_name="beta", data_type="text"),
        ],
        added_by="seeding",
    )

    deleted = await sqlite_instance.replace_seeds_for_dataset_async(
        dataset_name="alpha",
        seeds=[SeedPrompt(value="a3", dataset_name="alpha", data_type="text")],
        added_by="refresh",
    )

    assert deleted == 2
    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="alpha"))} == {"a3"}
    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="beta"))} == {"b1"}


async def test_replace_seeds_for_dataset_async_new_dataset_inserts(sqlite_instance: MemoryInterface):
    """Replacing a dataset with no existing rows simply inserts the new seeds and returns 0."""
    deleted = await sqlite_instance.replace_seeds_for_dataset_async(
        dataset_name="fresh",
        seeds=[SeedPrompt(value="v1", dataset_name="fresh", data_type="text")],
        added_by="refresh",
    )

    assert deleted == 0
    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="fresh"))} == {"v1"}


async def test_replace_seeds_for_dataset_async_empty_name_raises(sqlite_instance: MemoryInterface):
    """An empty dataset_name is rejected to avoid an accidental mass delete."""
    with pytest.raises(ValueError, match="dataset_name"):
        await sqlite_instance.replace_seeds_for_dataset_async(
            dataset_name="",
            seeds=[SeedPrompt(value="v1", dataset_name="x", data_type="text")],
            added_by="refresh",
        )


async def test_replace_seeds_for_dataset_async_empty_seeds_raises(sqlite_instance: MemoryInterface):
    """Refusing empty seeds prevents replacing a dataset with nothing (i.e. wiping it)."""
    with pytest.raises(ValueError, match="non-empty"):
        await sqlite_instance.replace_seeds_for_dataset_async(dataset_name="alpha", seeds=[], added_by="refresh")


async def test_replace_seeds_for_dataset_async_mismatched_name_raises(sqlite_instance: MemoryInterface):
    """Seeds tagged for a different dataset are rejected before any delete, avoiding a cross-wipe."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="keep", dataset_name="alpha", data_type="text")],
        added_by="seeding",
    )

    with pytest.raises(ValueError, match="mismatched"):
        await sqlite_instance.replace_seeds_for_dataset_async(
            dataset_name="alpha",
            seeds=[SeedPrompt(value="foreign", dataset_name="beta", data_type="text")],
            added_by="refresh",
        )

    # The guard fires before the delete, so alpha is untouched and beta was never created.
    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="alpha"))} == {"keep"}
    assert (await sqlite_instance.get_seeds_async(dataset_name="beta")) == []


async def test_replace_seeds_for_dataset_async_mixed_none_and_foreign_name_raises(sqlite_instance: MemoryInterface):
    """A mix of a None dataset_name and a foreign one still raises ValueError (not TypeError)."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="keep", dataset_name="alpha", data_type="text")],
        added_by="seeding",
    )

    seed_unnamed = SeedPrompt(value="unnamed", data_type="text")  # dataset_name defaults to None
    seed_foreign = SeedPrompt(value="foreign", dataset_name="beta", data_type="text")

    with pytest.raises(ValueError, match="mismatched"):
        await sqlite_instance.replace_seeds_for_dataset_async(
            dataset_name="alpha",
            seeds=[seed_unnamed, seed_foreign],
            added_by="refresh",
        )

    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="alpha"))} == {"keep"}


async def test_replace_seeds_for_dataset_async_rolls_back_on_error(sqlite_instance: MemoryInterface):
    """A failure during the replace rolls back the delete too, so existing seeds are preserved."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="old", dataset_name="d", data_type="text")],
        added_by="seeding",
    )

    async_session = await sqlite_instance.get_session_async()
    real_session = async_session.sync_session
    real_session.commit = MagicMock(side_effect=SQLAlchemyError("commit failed"))
    real_session.rollback = MagicMock(side_effect=real_session.rollback)

    with patch.object(sqlite_instance, "get_session_async", return_value=async_session):
        with pytest.raises(SQLAlchemyError, match="commit failed"):
            await sqlite_instance.replace_seeds_for_dataset_async(
                dataset_name="d",
                seeds=[SeedPrompt(value="new", dataset_name="d", data_type="text")],
                added_by="refresh",
            )

    real_session.rollback.assert_called_once()
    # The delete was rolled back with the failed insert -> the original seed survives.
    assert {seed.value for seed in (await sqlite_instance.get_seeds_async(dataset_name="d"))} == {"old"}
