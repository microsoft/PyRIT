# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end tests that verify every registered dataset provider can be fetched.

These tests download real data from HuggingFace and GitHub, are slow, and are
subject to transient network failures.  They are intended to run daily in e2e CI,
not on every PR.

Resiliency: each fetch is retried up to 3 times with exponential backoff to
handle transient HuggingFace / GitHub rate-limiting and network errors.

The local Garak task dataset contains three empty instruction-prefix variants.
Their identities and composed prompts are checked in
test_garak_latent_injection_dataset.py; all other datasets must have no empty seeds.
"""

import asyncio
import logging
import os
import pathlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

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
    _HarmEvalDataset,
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
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

if TYPE_CHECKING:
    from uuid import UUID

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
    _HarmEvalDataset,
    _HiXSTestDataset,
    _SGXSTestDataset,
    _SorryBenchDataset,
    _VLGuardDataset,
    _WildGuardMixDataset,
}


_EXPECTED_EMPTY_SEEDS: dict[str, int] = {"garak_latent_injection_tasks": 3}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Materialize the full registry only when collecting the default-provider sweep."""
    if "provider_cls" in metafunc.fixturenames:
        metafunc.parametrize("name,provider_cls", list(SeedDatasetProvider.get_all_providers().items()))


@dataclass(frozen=True, kw_only=True)
class _VariantCase:
    """A non-default provider configuration and its independent upstream contract."""

    factory: Callable[[], SeedDatasetProvider]
    provider_cls: type[SeedDatasetProvider]
    dataset_name: str
    seed_type: type[SeedPrompt] | type[SeedObjective] = SeedPrompt
    source_contains: str | None = None
    seed_metadata: Mapping[str, object] = field(default_factory=dict)
    expected_data_types: frozenset[str] = frozenset({"text"})
    text_pattern: str | None = None
    vlguard_instruction_key: str | None = None


# Each case names the exact non-default artifact. A case that starts returning the
# default artifact, or an empty one, is the upstream drift this matrix exists to catch.
_VARIANT_CASES: dict[str, _VariantCase] = {
    # Every Aya language is a separate JSONL; only English is reachable by default.
    **{
        f"aya-{language.lower()}": _VariantCase(
            factory=partial(_AyaRedteamingDataset, language=language),
            provider_cls=_AyaRedteamingDataset,
            dataset_name="aya_redteaming",
            source_contains=f"aya_{code}.jsonl",
        )
        for language, code in (
            ("Hindi", "hin"),
            ("French", "fra"),
            ("Spanish", "spa"),
            ("Arabic", "arb"),
            ("Russian", "rus"),
            ("Serbian", "srp"),
            ("Tagalog", "tgl"),
        )
    },
    # CatQA selects a Hugging Face split and records it on every seed; only "en" is default.
    **{
        f"categorical-harmful-qa-{language}": _VariantCase(
            factory=partial(_CategoricalHarmfulQADataset, language=language),
            provider_cls=_CategoricalHarmfulQADataset,
            dataset_name="categorical_harmful_qa",
            seed_type=SeedObjective,
            seed_metadata={"language": language},
            text_pattern=pattern,
        )
        # Han characters versus Vietnamese-specific letters, not just non-ASCII punctuation.
        # Each pattern matches all 550 rows of its own split and none of the other two (2026-09-28).
        for language, pattern in (
            ("zh", r"[\u3400-\u4dbf\u4e00-\u9fff]"),
            ("vi", r"[\u0102\u0103\u0110\u0111\u01a0\u01a1\u01af\u01b0\u1ea0-\u1ef9]"),
        )
    },
    # Each VLGuard subset reads a different `instr-resp` field and a different image
    # contract; only UNSAFES is default. Gated, so this runs only with a token whose
    # account has accepted the dataset terms.
    **{
        f"vlguard-{subset.value.replace('_', '-')}": _VariantCase(
            factory=partial(_VLGuardDataset, subset=subset),
            provider_cls=_VLGuardDataset,
            dataset_name="vlguard",
            seed_metadata={"subset": subset.value, "safe_image": True},
            expected_data_types=frozenset({"text", "image_path"}),
            vlguard_instruction_key=instruction_key,
        )
        for subset, instruction_key in (
            (VLGuardSubset.SAFE_UNSAFES, "unsafe_instruction"),
            (VLGuardSubset.SAFE_SAFES, "safe_instruction"),
        )
    },
}


def _assert_variant_dataset(*, case_id: str, case: _VariantCase, dataset: SeedDataset) -> None:
    """Check seed identity, shape, categories, and language independently of loader defaults."""
    assert isinstance(dataset, SeedDataset), f"{case_id} did not return a SeedDataset"
    assert dataset.dataset_name == case.dataset_name, f"{case_id}: unexpected dataset name"
    assert dataset.seeds, f"{case_id} returned an empty dataset"

    for seed in dataset.seeds:
        assert isinstance(seed, case.seed_type), f"{case_id}: unexpected seed type {type(seed).__name__}"
        assert seed.value, f"Seed in {case_id} has no value"
        assert seed.dataset_name == case.dataset_name, f"{case_id}: seed dataset_name mismatch"
        metadata = seed.metadata or {}
        # VLGuard's safe-image records have no harm labels. Verify that absence against
        # the raw record below, rather than requiring the loader to invent categories.
        if case.vlguard_instruction_key is not None and metadata.get("harmful_subcategory") == "":
            assert not seed.harm_categories, f"{case_id}: unlabeled seed gained harm categories"
        else:
            assert seed.harm_categories, f"Seed in {case_id} lost its harm categories"
        if "OTHER" in (seed.harm_categories or []):
            subcategory = metadata.get("harmful_subcategory")
            assert (
                case.vlguard_instruction_key is not None
                and isinstance(subcategory, str)
                and subcategory.strip().lower() == "other"
            ), f"{case_id}: harm category unexpectedly fell back to OTHER"
        for key, expected in case.seed_metadata.items():
            assert metadata.get(key) == expected, f"{case_id}: unexpected seed metadata for {key!r}"

    data_types = {seed.data_type for seed in dataset.seeds}
    assert data_types == case.expected_data_types, f"{case_id}: unexpected data types {data_types}"

    if case.text_pattern is not None:
        matches = sum(bool(re.search(case.text_pattern, seed.value)) for seed in dataset.seeds)
        fraction = matches / len(dataset.seeds)
        assert fraction >= 0.9, (
            f"{case_id}: only {fraction:.3f} of seeds match the requested language; expected at least 0.900"
        )


def _expected_vlguard_pairs(
    *,
    records: Sequence[Mapping[str, object]],
    image_dir: pathlib.Path,
    instruction_key: str,
) -> Counter[tuple[pathlib.Path, str, str]]:
    """Validate every selected raw record, including records the loader would discard."""
    pairs: Counter[tuple[pathlib.Path, str, str]] = Counter()
    for index, record in enumerate(records):
        assert isinstance(record.get("safe"), bool), f"VLGuard row {index}: missing or invalid safe flag"
        if not record["safe"]:
            continue

        filename = record.get("image")
        assert isinstance(filename, str) and filename, f"VLGuard row {index}: missing image filename"
        image_path = image_dir / filename
        assert image_path.is_file(), f"VLGuard row {index}: image reference does not resolve: {image_path}"

        instr_resp = record.get("instr-resp")
        assert isinstance(instr_resp, list) and instr_resp, f"VLGuard row {index}: invalid instr-resp"
        instructions: list[object] = []
        for item in instr_resp:
            assert isinstance(item, dict), f"VLGuard row {index}: invalid instr-resp entry"
            if instruction_key in item:
                instructions.append(item[instruction_key])
        assert len(instructions) == 1, f"VLGuard row {index}: missing or ambiguous {instruction_key}"
        instruction = instructions[0]
        assert isinstance(instruction, str) and instruction.strip(), (
            f"VLGuard row {index}: empty or invalid {instruction_key}"
        )
        subcategory = record.get("harmful_subcategory", "")
        assert isinstance(subcategory, str), f"VLGuard row {index}: invalid harmful_subcategory"
        pairs[(image_path, instruction, subcategory)] += 1

    assert pairs, "VLGuard returned no safe-image records"
    return pairs


def _assert_vlguard_contract(
    *,
    dataset: SeedDataset,
    records: Sequence[Mapping[str, object]],
    image_dir: pathlib.Path,
    instruction_key: str,
) -> None:
    """Require one simultaneous text/image pair for each selected upstream record."""
    expected_pairs = _expected_vlguard_pairs(records=records, image_dir=image_dir, instruction_key=instruction_key)
    groups: dict[UUID, list[SeedPrompt]] = defaultdict(list)
    for seed in dataset.seeds:
        assert isinstance(seed, SeedPrompt), "VLGuard must return SeedPrompt instances"
        assert seed.prompt_group_id is not None, "VLGuard seed has no prompt_group_id"
        assert seed.sequence == 0, "VLGuard text and image must share sequence 0"
        groups[seed.prompt_group_id].append(seed)

    actual_pairs: Counter[tuple[pathlib.Path, str, str]] = Counter()
    for seeds in groups.values():
        text_seeds = [seed for seed in seeds if seed.data_type == "text"]
        image_seeds = [seed for seed in seeds if seed.data_type == "image_path"]
        assert len(seeds) == 2 and len(text_seeds) == len(image_seeds) == 1, (
            "VLGuard group must contain exactly one text/image pair"
        )
        text_seed, image_seed = text_seeds[0], image_seeds[0]
        subcategory = (text_seed.metadata or {}).get("harmful_subcategory")
        assert isinstance(subcategory, str), "VLGuard text seed lost its source subcategory"
        assert (image_seed.metadata or {}).get("harmful_subcategory") == subcategory, (
            "VLGuard text and image subcategories differ"
        )
        actual_pairs[(pathlib.Path(image_seed.value), text_seed.value, subcategory)] += 1

    assert actual_pairs == expected_pairs, (
        "VLGuard returned pairs do not match the raw records: "
        f"{sum((expected_pairs - actual_pairs).values())} missing, "
        f"{sum((actual_pairs - expected_pairs).values())} unexpected"
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=5, min=5, max=60),
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    reraise=True,
)
async def _fetch_with_retry_async(provider: SeedDatasetProvider) -> SeedDataset:
    """Fetch a dataset with retry on transient network errors."""
    return await provider.fetch_dataset_async(cache=False)


@pytest.fixture(scope="module", autouse=True)
def _init_memory():
    """Multimodal providers need CentralMemory to save downloaded images."""
    asyncio.run(initialize_pyrit_async(memory_db_type=IN_MEMORY))


class TestAllDatasets:
    """Exhaustive test that every registered dataset provider can be fetched."""

    @pytest.mark.timeout(_TEST_TIMEOUT)
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

            dataset = await _fetch_with_retry_async(provider)
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

        empty = [seed for seed in dataset.seeds if not seed.value]
        expected = _EXPECTED_EMPTY_SEEDS.get(dataset.dataset_name, 0)
        assert len(empty) == expected, f"{name}: expected {expected} empty, got {len(empty)}"

        for seed in dataset.seeds:
            assert seed.dataset_name == dataset.dataset_name, (
                f"Seed dataset_name mismatch in {name}: {seed.dataset_name} != {dataset.dataset_name}"
            )

        logger.info(f"Successfully verified {name} with {len(dataset.seeds)} seeds")

    @pytest.mark.timeout(_TEST_TIMEOUT)
    @pytest.mark.parametrize("case_id", sorted(_VARIANT_CASES), ids=sorted(_VARIANT_CASES))
    async def test_fetch_non_default_variant_async(self, *, case_id: str) -> None:
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
            dataset = await _fetch_with_retry_async(provider)
        except Exception as e:
            if case.provider_cls in _HF_GATED_PROVIDERS and "gated dataset" in str(e):
                pytest.skip(f"{case_id}: HF account has not accepted dataset terms ({e})")
            pytest.fail(f"Failed to fetch non-default variant {case_id}: {e}")

        _assert_variant_dataset(case_id=case_id, case=case, dataset=dataset)
        if case.vlguard_instruction_key is not None:
            assert isinstance(provider, _VLGuardDataset)
            # Inspect the raw artifact just downloaded, without fetching another revision.
            records, image_dir = await provider._download_dataset_files_async(cache=True)
            await asyncio.to_thread(
                _assert_vlguard_contract,
                dataset=dataset,
                records=records,
                image_dir=image_dir,
                instruction_key=case.vlguard_instruction_key,
            )

        logger.info(f"Successfully verified variant {case_id} with {len(dataset.seeds)} seeds")
