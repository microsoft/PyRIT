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
import pathlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote import (
    VLGuardSubset,
    _AyaRedteamingDataset,
    _CategoricalHarmfulQADataset,
    _ComicJailbreakDataset,
    _GarakNpmDataset,
    _GarakPypiDataset,
    _HarmBenchMultimodalDataset,
    _HiXSTestDataset,
    _JailbreakV28KDataset,
    _PromptIntelDataset,
    _SGXSTestDataset,
    _SIUODataset,
    _SorryBenchDataset,
    _VLGuardDataset,
    _VLSUMultimodalDataset,
    _WildGuardMixDataset,
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
_IMAGE_FETCHING_PROVIDERS: set[type] = {_HarmBenchMultimodalDataset, _SIUODataset, _VLSUMultimodalDataset}

# Providers that produce many seeds and would otherwise exceed _TEST_TIMEOUT.
# Constructed with max_examples to keep CI fast; full coverage runs are out of scope here.
# The garak package registries are multi-million-row lists (npm ~3.3M, pypi ~555k); building
# every row as a SeedPrompt alone can exceed the timeout even with the data already cached.
_LIMITED_EXAMPLES_PROVIDERS: set[type] = {
    _ComicJailbreakDataset,
    _GarakNpmDataset,
    _GarakPypiDataset,
    _VLSUMultimodalDataset,
}

# Providers backed by HuggingFace-gated datasets. They require both a HUGGINGFACE_TOKEN
# and that the token's account has accepted each dataset's terms; skipped when no token
# is present (e.g. when running E2E locally without secrets).
_HF_GATED_PROVIDERS: set[type] = {
    _HiXSTestDataset,
    _SGXSTestDataset,
    _SorryBenchDataset,
    _VLGuardDataset,
    _WildGuardMixDataset,
}


def get_dataset_providers():
    """Helper to get all registered providers for parameterization."""
    providers = SeedDatasetProvider.get_all_providers()
    return [(name, cls) for name, cls in providers.items()]


@dataclass(frozen=True)
class _VariantCase:
    """One non-default provider configuration to validate against real upstream.

    ``test_fetch_dataset`` above builds every provider with no arguments, so it only
    ever sees each loader's default URL, split or subset. These cases carry the
    constructor argument that selects a different upstream artifact, plus what that
    choice should be observable as once the fetch returns.
    """

    factory: Callable[[], Any]
    provider_cls: type
    # Substring the provider's resolved upstream URL must contain, for loaders that
    # pin the variant in ``source`` before fetching. None when the loader selects the
    # variant by split or subset instead.
    source_contains: str | None = None
    # Metadata every seed must carry, for loaders that record which variant produced it.
    seed_metadata: Mapping[str, Any] = field(default_factory=dict)
    # Seed ``data_type`` values the variant must produce, and nothing else.
    expected_data_types: frozenset[str] = frozenset({"text"})
    # Minimum fraction of seeds that must contain a non-ASCII character. This is the
    # only binding to the artifact the server actually returned for loaders that select
    # a variant by split: they record the *requested* variant in seed metadata, so a
    # split that silently serves the default still reports the requested one. Measured
    # 2026-09-16: CatQA is 1.000 on zh and vi against 0.000 on the default en.
    min_non_ascii_fraction: float | None = None
    # Whether a seed resolving to the OTHER harm category counts as drift. True where
    # the variant currently maps every upstream category to a specific one, so an
    # upstream rename is the only way OTHER appears. False where the upstream
    # taxonomy has a real "other" bucket of its own.
    forbid_other_harm_category: bool = True


# Each case names the exact non-default artifact. A case that starts returning the
# default artifact, or an empty one, is the upstream drift this matrix exists to catch.
_VARIANT_CASES: dict[str, _VariantCase] = {
    # Every Aya language is a separate JSONL; only English is reachable by default.
    **{
        f"aya-{language.lower()}": _VariantCase(
            factory=partial(_AyaRedteamingDataset, language=language),
            provider_cls=_AyaRedteamingDataset,
            source_contains=f"aya_{_AyaRedteamingDataset.LANGUAGE_CODES[language]}.jsonl",
        )
        for language in ("Hindi", "French", "Spanish", "Arabic", "Russian", "Serbian", "Tagalog")
    },
    # CatQA selects a Hugging Face split and records it on every seed; only "en" is default.
    **{
        f"categorical-harmful-qa-{language}": _VariantCase(
            factory=partial(_CategoricalHarmfulQADataset, language=language),
            provider_cls=_CategoricalHarmfulQADataset,
            seed_metadata={"language": language},
            min_non_ascii_fraction=0.9,
        )
        for language in ("zh", "vi")
    },
    # Each VLGuard subset reads a different `instr-resp` field and a different image
    # contract; only UNSAFES is default. Gated, so this runs only with a token whose
    # account has accepted the dataset terms.
    **{
        f"vlguard-{subset.value.replace('_', '-')}": _VariantCase(
            factory=partial(_VLGuardDataset, subset=subset),
            provider_cls=_VLGuardDataset,
            seed_metadata={"subset": subset.value, "safe_image": True},
            expected_data_types=frozenset({"text", "image_path"}),
            # VLGuard's own subcategory list ends in "other", so OTHER here is the
            # upstream value rather than a failed lookup.
            forbid_other_harm_category=False,
        )
        for subset in (VLGuardSubset.SAFE_UNSAFES, VLGuardSubset.SAFE_SAFES)
    },
}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=5, min=5, max=60),
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    reraise=True,
)
async def _fetch_with_retry(provider) -> SeedDataset:
    """Fetch a dataset with retry on transient network errors."""
    return await provider.fetch_dataset_async(cache=False)


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
        if provider_cls in _HF_GATED_PROVIDERS and not os.environ.get("HUGGINGFACE_TOKEN"):
            pytest.skip(f"HUGGINGFACE_TOKEN not set (required for gated dataset used by {name})")

        # The JailBreakV-28K image set is distributed via a gated Google Drive
        # form (see the loader docstring), so it can't be auto-fetched in CI.
        # Skip when the user-supplied zip is not present in the home directory.
        if provider_cls == _JailbreakV28KDataset and not (pathlib.Path.home() / "JailBreakV_28K.zip").exists():
            pytest.skip("JailBreakV_28K.zip not present in home directory (manual download required)")

        logger.info(f"Testing provider: {name}")

        try:
            # Limit examples for slow providers that would otherwise exceed _TEST_TIMEOUT
            provider = provider_cls(max_examples=6) if provider_cls in _LIMITED_EXAMPLES_PROVIDERS else provider_cls()

            dataset = await _fetch_with_retry(provider)
        except Exception as e:
            # Multimodal providers silently skip failed image downloads. When ALL
            # images fail the resulting empty seed list triggers "SeedDataset cannot
            # be empty".  That is a transient environment issue, not a code bug.
            if provider_cls in _IMAGE_FETCHING_PROVIDERS and "cannot be empty" in str(e):
                pytest.skip(f"{name}: all image downloads failed ({e})")
            # HuggingFace-gated datasets fail loudly when the token in use hasn't
            # accepted the dataset's terms. Skip rather than fail so CI tokens that
            # haven't gone through each per-dataset terms flow don't block the suite.
            if provider_cls in _HF_GATED_PROVIDERS and "gated dataset" in str(e):
                pytest.skip(f"{name}: HF account has not accepted dataset terms ({e})")
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

    @pytest.mark.timeout(_TEST_TIMEOUT)
    @pytest.mark.parametrize("case_id", sorted(_VARIANT_CASES), ids=sorted(_VARIANT_CASES))
    async def test_fetch_non_default_variant(self, case_id):
        """
        Verify a non-default provider configuration against its real upstream artifact.

        ``test_fetch_dataset`` validates each provider's default configuration. A
        non-default URL, split or subset can disappear or change schema while the
        default stays green, and unit tests run against fixtures so they cannot see
        it. This checks that the requested variant is the one exercised, that it
        still returns usable seeds, and that its harm categories did not quietly
        fall back when an upstream category was renamed.

        Retries up to 3 times on transient network errors, and skips only for
        missing gated credentials or unaccepted terms, never for a contract failure.
        """
        case = _VARIANT_CASES[case_id]

        if case.provider_cls in _HF_GATED_PROVIDERS and not os.environ.get("HUGGINGFACE_TOKEN"):
            pytest.skip(f"HUGGINGFACE_TOKEN not set (required for gated dataset used by {case_id})")

        provider = case.factory()

        # Assert before fetching: for loaders that pin the artifact in `source`, this is
        # what proves the requested variant is the one about to be downloaded.
        if case.source_contains is not None:
            source = getattr(provider, "source", None)
            assert source is not None, f"{case_id}: provider exposes no source to verify"
            assert case.source_contains in source, (
                f"{case_id}: expected upstream source to contain {case.source_contains!r}, got {source!r}"
            )

        try:
            dataset = await _fetch_with_retry(provider)
        except Exception as e:
            if case.provider_cls in _HF_GATED_PROVIDERS and "gated dataset" in str(e):
                pytest.skip(f"{case_id}: HF account has not accepted dataset terms ({e})")
            pytest.fail(f"Failed to fetch non-default variant {case_id}: {e}")

        assert isinstance(dataset, SeedDataset), f"{case_id} did not return a SeedDataset"
        assert dataset.dataset_name, f"{case_id} has no dataset_name"
        assert len(dataset.seeds) > 0, f"{case_id} returned an empty dataset"

        data_types = set()
        for seed in dataset.seeds:
            assert seed.value, f"Seed in {case_id} has no value"
            assert seed.dataset_name == dataset.dataset_name, (
                f"Seed dataset_name mismatch in {case_id}: {seed.dataset_name} != {dataset.dataset_name}"
            )
            # A category the loader no longer recognises is not dropped, it is mapped
            # to OTHER with a log line nobody reads, so the seed still looks normal.
            # Measured on 2026-09-16, none of these variants produce OTHER at all
            # (6982 seeds across the nine ungated cases), which is what makes its
            # appearance a usable drift signal rather than noise.
            assert seed.harm_categories, f"Seed in {case_id} lost its harm categories"
            if case.forbid_other_harm_category:
                assert "OTHER" not in seed.harm_categories, (
                    f"{case_id}: harm category fell back to OTHER for {seed.value[:60]!r}, "
                    "which means an upstream category no longer maps"
                )
            for key, expected in case.seed_metadata.items():
                actual = (seed.metadata or {}).get(key)
                assert actual == expected, f"{case_id}: seed metadata {key!r} is {actual!r}, expected {expected!r}"
            data_types.add(seed.data_type)

        if case.min_non_ascii_fraction is not None:
            non_ascii = sum(1 for seed in dataset.seeds if any(ord(ch) > 127 for ch in seed.value))
            fraction = non_ascii / len(dataset.seeds)
            assert fraction >= case.min_non_ascii_fraction, (
                f"{case_id}: only {fraction:.3f} of seeds contain non-ASCII text, expected at least "
                f"{case.min_non_ascii_fraction}. The requested variant was most likely not the one served."
            )

        assert data_types == set(case.expected_data_types), (
            f"{case_id}: produced data types {sorted(data_types)}, expected {sorted(case.expected_data_types)}"
        )

        # An image whose file never landed is dropped silently by the loader, so the
        # ones that survived are the only evidence the image half of the contract holds.
        image_seeds = [seed for seed in dataset.seeds if seed.data_type == "image_path"]
        for seed in image_seeds:
            assert pathlib.Path(seed.value).exists(), f"{case_id}: image reference does not resolve: {seed.value}"

        if "image_path" in case.expected_data_types:
            text_seeds = [seed for seed in dataset.seeds if seed.data_type == "text"]
            assert image_seeds, f"{case_id}: expected image seeds, got none"
            assert text_seeds, f"{case_id}: expected text seeds, got none"
            # The instruction and its image are one prompt; a subset that reads the wrong
            # `instr-resp` field loses the pairing rather than failing outright.
            assert {seed.prompt_group_id for seed in text_seeds} == {seed.prompt_group_id for seed in image_seeds}, (
                f"{case_id}: text and image seeds are not paired by prompt_group_id"
            )

        logger.info(f"Successfully verified variant {case_id} with {len(dataset.seeds)} seeds")
