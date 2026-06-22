# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.garak_audio_dataset import _GarakAudioAchillesHeelDataset
from pyrit.models import SeedDataset, SeedPrompt


def _make_data(rows):
    """Wrap rows in a stand-in for an HF Dataset whose ``cast_column`` is a no-op."""
    data = MagicMock()
    data.cast_column.return_value = rows
    return data


@pytest.fixture
def mock_audio_rows():
    return [
        {"audio": {"bytes": b"RIFFmalware", "path": "Malware_Generation_12.wav"}},
        {"audio": {"bytes": b"RIFFprivacy", "path": "Privacy_Violation_3.wav"}},
    ]


async def test_fetch_maps_audio_rows(mock_audio_rows):
    loader = _GarakAudioAchillesHeelDataset()

    with (
        patch.object(
            loader,
            "_fetch_rows_async",
            new=AsyncMock(return_value=_make_data(mock_audio_rows)),
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote.garak_audio_dataset.cache_audio_bytes_async",
            new=AsyncMock(side_effect=lambda *, filename, audio_bytes, log_prefix: f"/cache/{filename}"),
        ),
    ):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    first = dataset.seeds[0]
    assert first.data_type == "audio_path"
    assert first.value == "/cache/garak_audio_achilles_heel_Malware_Generation_12.wav"
    assert first.dataset_name == "garak_audio_achilles_heel"
    assert first.harm_categories == ["Malware_Generation"]
    assert first.metadata["original_filename"] == "Malware_Generation_12.wav"
    assert first.metadata["category"] == "Malware_Generation"
    assert dataset.seeds[1].harm_categories == ["Privacy_Violation"]


async def test_rows_without_bytes_skipped():
    loader = _GarakAudioAchillesHeelDataset()
    rows = [
        {"audio": {"bytes": b"RIFF", "path": "Malware_Generation_1.wav"}},
        {"audio": {"bytes": None, "path": "x.wav"}},
        {"audio": {}},
    ]

    with (
        patch.object(loader, "_fetch_rows_async", new=AsyncMock(return_value=_make_data(rows))),
        patch(
            "pyrit.datasets.seed_datasets.remote.garak_audio_dataset.cache_audio_bytes_async",
            new=AsyncMock(side_effect=lambda *, filename, audio_bytes, log_prefix: f"/cache/{filename}"),
        ),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1


async def test_empty_after_fetch_raises():
    loader = _GarakAudioAchillesHeelDataset()

    with patch.object(loader, "_fetch_rows_async", new=AsyncMock(return_value=_make_data([]))):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


async def test_max_examples_caps_audio_seeds(mock_audio_rows):
    loader = _GarakAudioAchillesHeelDataset(max_examples=1)

    with (
        patch.object(loader, "_fetch_rows_async", new=AsyncMock(return_value=_make_data(mock_audio_rows))),
        patch(
            "pyrit.datasets.seed_datasets.remote.garak_audio_dataset.cache_audio_bytes_async",
            new=AsyncMock(side_effect=lambda *, filename, audio_bytes, log_prefix: f"/cache/{filename}"),
        ),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].harm_categories == ["Malware_Generation"]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("Malware_Generation_12.wav", "Malware_Generation"),
        ("Privacy_Violation_3.wav", "Privacy_Violation"),
        ("NoIndexHere.wav", "NoIndexHere"),
        (None, None),
        ("", None),
    ],
)
def test_harm_category_from_path(path, expected):
    assert _GarakAudioAchillesHeelDataset._harm_category_from_path(path) == expected


def test_dataset_name():
    assert _GarakAudioAchillesHeelDataset().dataset_name == "garak_audio_achilles_heel"
