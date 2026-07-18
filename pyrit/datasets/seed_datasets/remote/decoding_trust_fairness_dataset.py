# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any, cast

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)

_DECODING_TRUST_COMMIT = "bfb1251"
_BASE_URL = (
    f"https://raw.githubusercontent.com/AI-secure/DecodingTrust/{_DECODING_TRUST_COMMIT}/data/fairness/fairness_data/"
)


class DecodingTrustFairnessSubset(Enum):
    """Which pre-generated Adult-income fairness prompt file to load."""

    ADULT_0_8 = "adult_0_8"
    ADULT_0_200_TEST_BR_0_0 = "adult_0_200_test_base_rate_0.0"
    ADULT_0_200_TEST_BR_0_5 = "adult_0_200_test_base_rate_0.5"
    ADULT_0_200_TEST_BR_1_0 = "adult_0_200_test_base_rate_1.0"
    ADULT_0_200_TRAIN_BR_0_0_TEST_BR_0_0 = "adult_0_200_train_br_0.0_test_br_0.0"
    ADULT_0_200_TRAIN_BR_0_0_TEST_BR_0_5 = "adult_0_200_train_br_0.0_test_br_0.5"
    ADULT_0_200_TRAIN_BR_0_0_TEST_BR_1_0 = "adult_0_200_train_br_0.0_test_br_1.0"
    ADULT_16_200_TRAIN_BR_0_0_TEST_BR_0_5 = "adult_16_200_train_br_0.0_test_br_0.5"
    ADULT_32_200_TRAIN_BASE_RATE_0_0 = "adult_32_200_train_base_rate_0.0"
    ADULT_32_200_TRAIN_BASE_RATE_0_5 = "adult_32_200_train_base_rate_0.5"
    ADULT_32_200_TRAIN_BASE_RATE_1_0 = "adult_32_200_train_base_rate_1.0"
    ADULT_32_200_TRAIN_BR_0_0_TEST_BR_0_0 = "adult_32_200_train_br_0.0_test_br_0.0"
    ADULT_32_200_TRAIN_BR_0_0_TEST_BR_0_5 = "adult_32_200_train_br_0.0_test_br_0.5"
    ADULT_32_200_TRAIN_BR_0_5_TEST_BR_0_0 = "adult_32_200_train_br_0.5_test_br_0.0"
    ADULT_32_200_TRAIN_BR_1_0_TEST_BR_0_0 = "adult_32_200_train_br_1.0_test_br_0.0"


class _DecodingTrustFairnessDataset(_RemoteDatasetLoader):
    """
    Loader for the Fairness perspective of the DecodingTrust benchmark.

    Each record is a binary income-classification prompt derived from the UCI
    Adult census dataset, phrased as a yes/no question about whether a described
    person earns more than $50k/year. Labels may be perturbed relative to ground
    truth via base-rate parity (encoded in the filename, e.g. ``base_rate_0.5``)
    to test whether a model's predictions rely on protected attributes such as
    sex rather than genuine signal.

    References:
        [@wang2023decodingtrust]
        https://github.com/AI-secure/DecodingTrust

    License:
        DecodingTrust is distributed under CC BY-SA 4.0. PyRIT fetches the
        prompts at runtime and does not redistribute them.

    Warning:
        Base-rate-parity variants deliberately alter label distributions
        relative to ground truth to probe fairness; labels in these files are
        not raw census outcomes and should not be treated as such.
    """

    harm_categories: list[str] = ["fairness"]
    modalities: list[str] = ["text"]
    size: str = "small"
    tags: set[str] = {"default", "safety", "bias"}

    _AUTHORS: tuple[str, ...] = (
        "Boxin Wang", "Weixin Chen", "Hengzhi Pei", "Chulin Xie", "Mintong Kang",
        "Chenhui Zhang", "Chejian Xu", "Zidi Xiong", "Ritik Dutta", "Rylan Schaeffer",
        "Sang T. Truong", "Simran Arora", "Mantas Mazeika", "Dan Hendrycks",
        "Zinan Lin", "Yu Cheng", "Sanmi Koyejo", "Dawn Song", "Bo Li",
    )

    _GROUPS: tuple[str, ...] = (
        "University of Illinois Urbana-Champaign",
        "Stanford University",
        "University of California, Berkeley",
        "Center for AI Safety",
        "Microsoft Research",
    )

    _DESCRIPTION = (
        "Fairness perspective of the DecodingTrust benchmark (Wang et al., 2023). "
        "Binary income-classification prompts derived from the UCI Adult dataset, "
        "with labels optionally perturbed by base-rate parity to test reliance on "
        "protected attributes (e.g. sex)."
    )

    def __init__(
        self,
        *,
        subset: DecodingTrustFairnessSubset = DecodingTrustFairnessSubset.ADULT_0_8,
    ) -> None:
        """
        Args:
            subset: Which fairness_data JSONL file to load. Defaults to the
                smallest file (8 zero-shot samples).

        Raises:
            ValueError: If subset is not a DecodingTrustFairnessSubset instance.
        """
        self._validate_enum(subset, DecodingTrustFairnessSubset, "subset")
        self.subset = subset

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "decoding_trust_fairness"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the DecodingTrust Fairness prompts and return them as a SeedDataset.

        Args:
            cache: Whether to cache the fetched JSONL file locally. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset whose seeds are the fairness prompts.

        Raises:
            ValueError: If the source JSONL contains items that are not dicts, or
                if the result would be empty.
        """
        url = f"{_BASE_URL}{self.subset.value}.jsonl"
        logger.info(f"Loading DecodingTrust Fairness subset={self.subset.value!r} from {url}")

        raw = self._fetch_from_url(source=url, source_type="public_url", cache=cache)
        records = cast("list[Any]", raw)

        seed_prompts = self._records_to_seed_prompts(source_url=url, records=records)
        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check the source file.")
        logger.info(f"Loaded {len(seed_prompts)} prompts from DecodingTrust Fairness")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _records_to_seed_prompts(self, *, source_url: str, records: list[Any]) -> list[SeedUnion]:
        """
        Convert raw JSONL records into SeedPrompt instances.

        Args:
            source_url: The URL the records were fetched from; becomes each
                SeedPrompt's ``source``.
            records: Raw parsed JSONL records.

        Returns:
            List of SeedPrompt objects, one per record with non-empty ``input``.

        Raises:
            ValueError: If any record is not a dict.
        """
        seed_prompts: list[SeedUnion] = []
        for item in records:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Expected DecodingTrust fairness records to be JSON objects, got {type(item).__name__}: {item!r}"
                )

            text = item.get("input")
            if not isinstance(text, str) or not text:
                logger.warning("Skipping record with missing or empty 'input'")
                continue

            option = item.get("option")
            seed_prompts.append(
                SeedPrompt(
                    value=text,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=self.harm_categories,
                    description=self._DESCRIPTION,
                    source=source_url,
                    authors=list(self._AUTHORS),
                    groups=list(self._GROUPS),
                    metadata={
                        "label": str(item.get("label", "")),
                        "option": ",".join(option) if isinstance(option, list) else "",
                    },
                )
            )
        return seed_prompts
