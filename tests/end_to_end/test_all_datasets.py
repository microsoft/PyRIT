# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end tests that verify every registered dataset provider can be fetched.

These tests download real data from HuggingFace and GitHub, are slow, and are
subject to transient network failures.  They are intended to run daily in e2e CI,
not on every PR.

Resiliency: each fetch is retried up to 3 times with exponential backoff to
handle transient HuggingFace / GitHub rate-limiting and network errors.
"""

import asyncio
import logging
import os

import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote import (
    _HarmBenchMultimodalDataset,
    _PromptIntelDataset,
    _VLSUMultimodalDataset,
)
from pyrit.models import SeedDataset
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

logger = logging.getLogger(__name__)

# Per-test timeout in seconds (5 minutes per dataset)
_TEST_TIMEOUT = 300

# Transient error types that warrant a retry
_RETRYABLE_ERRORS = (OSError, ConnectionError, TimeoutError)

# Providers that download many remote images; each image fetch may fail
# due to rate-limiting, so an empty result is expected in some environments.
_IMAGE_FETCHING_PROVIDERS: set[type] = {_HarmBenchMultimodalDataset, _VLSUMultimodalDataset}


def get_dataset_providers():
    """Helper to get all registered providers for parameterization."""
    providers = SeedDatasetProvider.get_all_providers()
    return [(name, cls) for name, cls in providers.items()]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=5, min=5, max=60),
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    reraise=True,
)
async def _fetch_with_retry(provider) -> SeedDataset:
    """Fetch a dataset with retry on transient network errors."""
    return await provider.fetch_dataset(cache=False)


@pytest.fixture(scope="module", autouse=True)
def _init_memory():
    """Multimodal providers need CentralMemory to save downloaded images."""
    asyncio.run(initialize_pyrit_async(memory_db_type=IN_MEMORY))


class TestAllDatasets:
    """Exhaustive test that every registered dataset provider can be fetched."""

    @pytest.mark.timeout(_TEST_TIMEOUT)
    @pytest.mark.parametrize("name,provider_cls", get_dataset_providers())
    async def test_fetch_dataset(self, name, provider_cls):
        """
        Verify that a specific registered dataset can be fetched.

        This test is parameterized to run for each registered provider.
        It verifies that:
        1. The dataset can be downloaded/loaded without error
        2. The result is a SeedDataset
        3. The dataset is not empty (has seeds)

        Retries up to 3 times on transient network errors.
        """
        # Skip providers that require credentials not available in CI
        if provider_cls == _PromptIntelDataset and not os.environ.get("PROMPTINTEL_API_KEY"):
            pytest.skip("PROMPTINTEL_API_KEY not set")

        logger.info(f"Testing provider: {name}")

        try:
            # Limit examples for slow multimodal providers that fetch many remote images
            provider = provider_cls(max_examples=6) if provider_cls == _VLSUMultimodalDataset else provider_cls()

            dataset = await _fetch_with_retry(provider)
        except Exception as e:
            # Multimodal providers silently skip failed image downloads. When ALL
            # images fail the resulting empty seed list triggers "SeedDataset cannot
            # be empty".  That is a transient environment issue, not a code bug.
            if provider_cls in _IMAGE_FETCHING_PROVIDERS and "cannot be empty" in str(e):
                pytest.skip(f"{name}: all image downloads failed ({e})")
            pytest.fail(f"Failed to fetch dataset from {name}: {e}")

        assert isinstance(dataset, SeedDataset), f"{name} did not return a SeedDataset"
        assert dataset.dataset_name, f"{name} has no dataset_name"
        assert len(dataset.seeds) > 0, f"{name} returned an empty dataset"

        for seed in dataset.seeds:
            assert seed.value, f"Seed in {name} has no value"
            assert seed.dataset_name == dataset.dataset_name, (
                f"Seed dataset_name mismatch in {name}: {seed.dataset_name} != {dataset.dataset_name}"
            )

        logger.info(f"Successfully verified {name} with {len(dataset.seeds)} seeds")
