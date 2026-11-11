# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.local import _LocalDatasetLoader
from pyrit.datasets.seed_datasets.remote import (
    _HarmBenchDataset,
    _HiXSTestDataset,
    _PromptIntelDataset,
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset


async def test_builtin_catalog_discovery_does_not_load_seeds_async() -> None:
    with (
        patch.object(SeedDataset, "from_yaml_file", side_effect=AssertionError("Seeds were materialized")) as load,
        patch.object(_RemoteDatasetLoader, "fetch_dataset_async", new_callable=AsyncMock) as fetch,
        patch("requests.sessions.Session.request", side_effect=AssertionError("Network request")) as request,
    ):
        descriptions = await SeedDatasetProvider.get_all_dataset_descriptors_async()
    assert {"harmbench", "promptintel"} <= descriptions.keys()
    load.assert_not_called()
    fetch.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("provider_class", [_HarmBenchDataset, _PromptIntelDataset, _HiXSTestDataset])
async def test_default_descriptions_do_not_fetch_or_reveal_credentials_async(
    provider_class: type[_RemoteDatasetLoader],
) -> None:
    with (
        patch.dict(os.environ, {"HUGGINGFACE_TOKEN": "secret-hf-value", "PROMPTINTEL_API_KEY": "secret-api-value"}),
        patch.object(provider_class, "_fetch_dataset_async", new_callable=AsyncMock) as fetch,
    ):
        description = await provider_class().describe_async()
    assert description.name
    assert description.description
    assert description.metadata is not None
    assert description.source_url is not None
    assert description.source_url.startswith("https://")
    assert "secret-hf-value" not in repr(description)
    assert "secret-api-value" not in repr(description)
    fetch.assert_not_called()


async def test_local_description_does_not_materialize_seeds_async(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    await asyncio.to_thread(
        path.write_text,
        "dataset_name: local\ndescription: Local fixture\nsource: https://example.com/dataset\n"
        "modalities: text\nseeds:\n  - value: hello\n",
        encoding="utf-8",
    )
    with patch.object(SeedDataset, "from_yaml_file", side_effect=AssertionError("Seeds were materialized")):
        provider = await asyncio.to_thread(_LocalDatasetLoader, file_path=path)
        description = await provider.describe_async()
    assert description.name == "local"
    assert description.description == "Local fixture"
    assert description.source_url == "https://example.com/dataset"
    assert description.metadata is not None
    assert description.metadata.modalities == {"text"}


async def test_metadata_failure_keeps_provider_and_stored_browsing_available_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _HarmBenchDataset()
    with patch.object(provider, "_parse_metadata_async", side_effect=ValueError("secret exception details")):
        description = await provider.describe_async()
    assert description.name == "harmbench"
    assert description.metadata is None
    assert description.unavailable_reason == "Provider metadata is unavailable."
    assert "secret exception details" not in repr(description)
    assert "_HarmBenchDataset (ValueError)" in caplog.text
    assert "secret exception details" not in caplog.text


@pytest.mark.parametrize(
    "metadata",
    [
        {"modalities": [None]},
        {"modalities": ["text", 42]},
        {"harm_categories": [False]},
        {"size": [None]},
        {"load_time": "unsupported"},
    ],
)
async def test_malformed_local_metadata_is_unavailable_async(tmp_path: Path, metadata: dict[str, object]) -> None:
    path = tmp_path / "local.yaml"
    await asyncio.to_thread(
        path.write_text,
        json.dumps({"dataset_name": "local", "description": "Local fixture", **metadata}),
        encoding="utf-8",
    )
    provider = await asyncio.to_thread(_LocalDatasetLoader, file_path=path)
    description = await provider.describe_async()
    assert description.name == "local"
    assert description.description == "Local fixture"
    assert description.metadata is None
    assert description.unavailable_reason == "Provider metadata is unavailable."


async def test_unsupported_metadata_shape_is_unavailable_async() -> None:
    provider = _HarmBenchDataset()
    with patch.object(provider, "_parse_metadata_async", return_value={"modalities": ["text"]}):
        description = await provider.describe_async()
    assert description.metadata is None
    assert description.unavailable_reason == "Provider metadata is unavailable."


async def test_catalog_isolates_unsupported_default_provider_async(caplog: pytest.LogCaptureFixture) -> None:
    class Unsupported:
        def __init__(self) -> None:
            raise NotImplementedError("secret exception details")

    with patch.object(
        SeedDatasetProvider,
        "get_all_providers",
        return_value={
            "supported": _HarmBenchDataset,
            "unsupported": Unsupported,
        },
    ):
        descriptors = await SeedDatasetProvider.get_all_dataset_descriptors_async()
    assert list(descriptors) == ["harmbench"]
    assert "Unsupported (NotImplementedError)" in caplog.text
    assert "secret exception details" not in caplog.text


@pytest.mark.parametrize(
    "source",
    [
        "https://user:secret@example.com/data",
        "https://example.com?token=secret",
        "https://example.com/data#access_token=secret",
        "http://example.com/data",
        "https://[invalid",
        "/secret/file",
    ],
)
@pytest.mark.parametrize("field", ["source", "license_url", "access_url"])
async def test_descriptor_omits_private_source_configuration_async(source: str, field: str) -> None:
    provider = _HarmBenchDataset()
    with patch.object(provider, field, source):
        description = await provider.describe_async()
    result_field = "source_url" if field == "source" else field
    assert getattr(description, result_field) is None


async def test_descriptor_preserves_public_links_async() -> None:
    provider = _HarmBenchDataset(source="https://example.com/dataset")
    with (
        patch.object(provider, "license_url", "https://example.com/license"),
        patch.object(provider, "access_url", "https://example.com/access"),
    ):
        description = await provider.describe_async()
    assert description.source_url == "https://example.com/dataset"
    assert description.license_url == "https://example.com/license"
    assert description.access_url == "https://example.com/access"
