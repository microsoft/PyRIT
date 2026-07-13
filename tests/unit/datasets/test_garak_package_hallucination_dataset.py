# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset import (
    _GarakCratesDataset,
    _GarakDartDataset,
    _GarakNpmDataset,
    _GarakPerlDataset,
    _GarakPypiDataset,
    _GarakRakuDataset,
    _GarakRubyGemsDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_pypi_rows():
    return [
        {"text": "numpy", "package first seen": "2010-01-01"},
        {"text": "requests", "package first seen": "2011-02-02"},
    ]


@pytest.fixture
def mock_npm_rows():
    return [
        {"text": "express", "package_first_seen": "2010-05-05"},
        {"text": "lodash", "package_first_seen": "2011-06-06"},
    ]


async def test_fetch_dataset_maps_rows(mock_pypi_rows):
    loader = _GarakPypiDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_pypi_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].value == "numpy"
    assert dataset.seeds[0].data_type == "text"
    assert dataset.seeds[0].dataset_name == "garak_pypi_packages"
    assert dataset.seeds[0].harm_categories == []


async def test_pypi_space_column_alias(mock_pypi_rows):
    """pypi uses the spaced ``package first seen`` column name."""
    loader = _GarakPypiDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_pypi_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert dataset.seeds[0].metadata["package_first_seen"] == "2010-01-01"
    assert dataset.seeds[0].metadata["language"] == "python"


async def test_npm_underscore_column_alias(mock_npm_rows):
    """npm uses the underscored ``package_first_seen`` column name."""
    loader = _GarakNpmDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_npm_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert dataset.seeds[0].metadata["package_first_seen"] == "2010-05-05"
    assert dataset.seeds[0].metadata["language"] == "javascript"


async def test_fetch_uses_train_split(mock_pypi_rows):
    loader = _GarakPypiDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_pypi_rows),
    ) as mock_fetch:
        await loader.fetch_dataset_async()

    call_kwargs = mock_fetch.call_args.kwargs
    assert call_kwargs["split"] == "train"
    assert call_kwargs["dataset_name"] == "garak-llm/pypi-20241031"


async def test_empty_rows_raises():
    loader = _GarakPypiDataset()

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


async def test_rows_missing_text_are_skipped():
    loader = _GarakDartDataset()
    rows = [{"text": "valid"}, {"text": None}, {"text": "  "}]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert [s.value for s in dataset.seeds] == ["valid"]


async def test_max_examples_caps_seed_count():
    loader = _GarakNpmDataset(max_examples=2)
    rows = [{"text": f"pkg{i}", "package_first_seen": "2010-01-01"} for i in range(10)]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert [s.value for s in dataset.seeds] == ["pkg0", "pkg1"]


async def test_max_examples_none_loads_all(mock_npm_rows):
    loader = _GarakNpmDataset(max_examples=None)

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_npm_rows)):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2


@pytest.mark.parametrize(
    "loader_cls, expected_name, expected_language",
    [
        (_GarakPypiDataset, "garak_pypi_packages", "python"),
        (_GarakNpmDataset, "garak_npm_packages", "javascript"),
        (_GarakCratesDataset, "garak_crates_packages", "rust"),
        (_GarakRubyGemsDataset, "garak_rubygems_packages", "ruby"),
        (_GarakDartDataset, "garak_dart_packages", "dart"),
        (_GarakPerlDataset, "garak_perl_packages", "perl"),
        (_GarakRakuDataset, "garak_raku_packages", "raku"),
    ],
)
def test_dataset_names_and_language(loader_cls, expected_name, expected_language):
    loader = loader_cls()
    assert loader.dataset_name == expected_name
    assert expected_language == loader._LANGUAGE
