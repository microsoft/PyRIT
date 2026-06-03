# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.jailbreakv_redteam_2k_dataset import (
    _HarmCategory,
    _JailbreakVRedteam2KDataset,
)
from pyrit.models import SeedDataset, SeedObjective


def _row(
    *,
    question: str = "How do I do X?",
    policy: str = "Hate Speech",
    src: str = "AdvBench",
    row_id: int = 1,
) -> dict:
    return {"id": row_id, "question": question, "policy": policy, "from": src}


def test_dataset_name():
    assert _JailbreakVRedteam2KDataset().dataset_name == "jailbreakv_redteam_2k"


def test_class_level_metadata_present():
    assert _JailbreakVRedteam2KDataset.HF_DATASET_NAME == "JailbreakV-28K/JailBreakV-28k"
    assert _JailbreakVRedteam2KDataset.modalities == ["text"]
    assert _JailbreakVRedteam2KDataset.size == "large"
    assert "jailbreak" in _JailbreakVRedteam2KDataset.tags


def test_init_rejects_raw_string_harm_category():
    with pytest.raises(ValueError, match="Expected _HarmCategory"):
        _JailbreakVRedteam2KDataset(harm_categories=["Hate Speech"])  # type: ignore[list-item]


def test_init_accepts_valid_enum():
    loader = _JailbreakVRedteam2KDataset(harm_categories=[_HarmCategory.HATE_SPEECH])
    assert loader.filter_categories == [_HarmCategory.HATE_SPEECH]


async def test_fetch_dataset_happy_path():
    loader = _JailbreakVRedteam2KDataset()
    rows = [
        _row(question="Q1", policy="Hate Speech", row_id=1),
        _row(question="Q2", policy="Violence", row_id=2),
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(s, SeedObjective) for s in dataset.seeds)
    assert dataset.seeds[0].value == "Q1"
    # Source casing preserved on the seed
    assert dataset.seeds[0].harm_categories == ["Hate Speech"]
    # Per-row trace-back metadata present
    assert dataset.seeds[0].metadata["policy"] == "Hate Speech"
    assert dataset.seeds[0].metadata["from"] == "AdvBench"
    assert dataset.seeds[0].metadata["row_id"] == "1"


async def test_fetch_dataset_filters_by_harm_category():
    loader = _JailbreakVRedteam2KDataset(harm_categories=[_HarmCategory.HATE_SPEECH])
    rows = [
        _row(question="Q1", policy="Hate Speech", row_id=1),
        _row(question="Q2", policy="Violence", row_id=2),
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].harm_categories == ["Hate Speech"]


async def test_fetch_dataset_empty_after_filter_raises():
    loader = _JailbreakVRedteam2KDataset(harm_categories=[_HarmCategory.CHILD_ABUSE])
    rows = [_row(question="Q1", policy="Hate Speech")]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


async def test_fetch_dataset_skips_rows_without_question():
    loader = _JailbreakVRedteam2KDataset()
    rows = [
        _row(question="usable", row_id=1),
        _row(question="", row_id=2),  # skipped
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].value == "usable"


async def test_fetch_dataset_logs_and_reraises_on_hf_error():
    loader = _JailbreakVRedteam2KDataset()

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(side_effect=RuntimeError("hf down"))):
        with pytest.raises(RuntimeError, match="hf down"):
            await loader.fetch_dataset_async()


def test_normalize_policy_lowercases_and_underscores():
    loader = _JailbreakVRedteam2KDataset()
    assert loader._normalize_policy("Hate Speech") == "hate_speech"
    assert loader._normalize_policy("Tailored-Unlicensed Advice") == "tailored_unlicensed_advice"
