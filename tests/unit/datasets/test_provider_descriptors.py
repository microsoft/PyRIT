# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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


async def test_builtin_catalog_discovery_does_not_load_seeds_async():
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
async def test_default_descriptions_do_not_fetch_or_reveal_credentials_async(provider_class, monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "secret-hf-value")
    monkeypatch.setenv("PROMPTINTEL_API_KEY", "secret-api-value")
    with patch.object(provider_class, "_fetch_dataset_async", new_callable=AsyncMock) as fetch:
        description = await provider_class().describe_async()
    assert description.name
    assert description.description
    assert description.metadata is not None
    assert description.source_url.startswith("https://")
    assert "secret-hf-value" not in repr(description)
    assert "secret-api-value" not in repr(description)
    fetch.assert_not_called()


async def test_local_description_does_not_materialize_seeds_async(tmp_path):
    path = tmp_path / "local.yaml"
    path.write_text("dataset_name: local\ndescription: Local fixture\nmodalities: text\nseeds:\n  - value: hello\n")
    with patch.object(SeedDataset, "from_yaml_file", side_effect=AssertionError("Seeds were materialized")):
        provider = _LocalDatasetLoader(file_path=path)
        description = await provider.describe_async()
    assert description.name == "local"
    assert description.description == "Local fixture"
    assert description.metadata.modalities == {"text"}


async def test_metadata_failure_keeps_provider_and_stored_browsing_available_async():
    provider = _HarmBenchDataset()
    provider._parse_metadata_async = AsyncMock(side_effect=ValueError("secret exception details"))
    description = await provider.describe_async()
    assert description.name == "harmbench"
    assert description.metadata is None
    assert description.unavailable_reason == "Provider metadata is unavailable."
    assert "secret exception details" not in repr(description)


async def test_catalog_isolates_unsupported_default_provider_async():
    class Unsupported:
        def __init__(self):
            raise NotImplementedError("No default configuration")

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


@pytest.mark.parametrize(
    "source", ["https://user:secret@example.com/data", "https://example.com?token=secret", "/secret/file"]
)
async def test_descriptor_omits_private_source_configuration_async(source):
    description = await _HarmBenchDataset(source=source).describe_async()
    assert description.source_url is None
