# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class TurkishPromptInjectionLabel(Enum):
    """Filter records by the published security decision label."""

    BENIGN = 0
    ATTACK = 1
    ALL = "all"


class TurkishPromptInjectionSplit(Enum):
    """Select a published split or combine the complete dataset."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    ALL = "all"


class TurkishPromptInjectionFamily(Enum):
    """Turkish-native prompt-injection families represented in the dataset."""

    INSTRUCTION_OVERRIDE_EXTRACTION = "tr_instruction_override_extraction"
    JAILBREAK_PERSONA = "tr_jailbreak_persona"
    OBFUSCATION_EXFILTRATION = "tr_obfuscation_exfiltration"
    AGENTIC_TOOL_ABUSE = "tr_agentic_toolabuse"


class _TurkishPromptInjectionDataset(_RemoteDatasetLoader):
    """
    Loader for the Turkish Prompt-Injection dataset.

    A Turkish-native prompt-injection dataset with paired benign and attack
    examples for LLM guardrail and detector evaluation. Attack records cover
    instruction override and system-prompt extraction, jailbreak personas,
    obfuscation and data exfiltration, and agentic tool abuse, with Turkish
    morphological variation. Benign hard negatives share surface vocabulary
    with the attacks while being fully legitimate, so false-positive behavior
    can be measured directly. Templates are isolated across the train,
    validation, and test splits.

    Attack records are returned by default for red-team use. Set ``label`` to
    ``ALL`` to load the full benign/attack surface or ``BENIGN`` to load only
    the hard negatives.

    Reference: [@deniz2026turkishpromptinjection].
    License: CC BY 4.0.

    The dataset's labels describe prompt-injection decisions rather than content
    harms, so they are retained in seed metadata and ``harm_categories`` is
    intentionally empty.
    """

    HF_DATASET_NAME: str = "3nesdeniz/turkish-prompt-injection-1k"
    HF_DATASET_REVISION: str = "1cbd1152d9732f40148fbd5bb7cf0f58ddfe84c6"
    DATASET_VERSION: str = "1.0.0"
    harm_categories: list[str] = []
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 1,000 records
    tags: frozenset[str] = frozenset({"safety", "prompt_injection", "turkish", "multilingual", "synthetic"})

    def __init__(
        self,
        *,
        label: TurkishPromptInjectionLabel = TurkishPromptInjectionLabel.ATTACK,
        split: TurkishPromptInjectionSplit = TurkishPromptInjectionSplit.ALL,
        attack_families: list[TurkishPromptInjectionFamily] | None = None,
    ) -> None:
        """
        Initialize the Turkish Prompt-Injection loader.

        Args:
            label: Security decision label to load. Defaults to attack records.
            split: Published split to load, or all splits. Defaults to all.
            attack_families: Optional non-empty list of attack families. Applies
                to attack records only; benign records are not family-scoped.

        Raises:
            ValueError: If an enum value is invalid or a filter list is empty.
        """
        self._validate_enum(label, TurkishPromptInjectionLabel, "label")
        self._validate_enum(split, TurkishPromptInjectionSplit, "split")

        if attack_families is not None:
            if not attack_families:
                raise ValueError("`attack_families` must be a non-empty list (pass None to include all families)")
            self._validate_enums(attack_families, TurkishPromptInjectionFamily, "attack_family")

        self._label = label
        self._split = split
        self._attack_families = {family.value for family in attack_families} if attack_families else None

    @property
    @override
    def dataset_name(self) -> str:
        """The registered dataset name."""
        return "turkish_prompt_injection"

    @classmethod
    def _validate_row(cls, *, row: dict[str, Any]) -> None:
        """
        Validate the published schema and label-dependent invariants.

        The split is implied by the fetched parquet file rather than stored per
        row, so it is not part of the row schema.

        Raises:
            ValueError: If the row violates the published dataset schema.
        """
        required_keys = {
            "id",
            "text",
            "label",
            "class",
            "attack_family",
            "technique",
            "severity",
            "language",
        }
        missing = required_keys - row.keys()
        if missing:
            raise ValueError(f"Missing keys in Turkish Prompt-Injection entry: {', '.join(sorted(missing))}")

        if not isinstance(row["text"], str) or not row["text"].strip():
            raise ValueError(f"Turkish Prompt-Injection entry has an invalid `text` value: {row['text']!r}")

        row_label = row["label"]
        if type(row_label) is not int or row_label not in {0, 1}:
            raise ValueError(f"Invalid label in Turkish Prompt-Injection entry: {row_label!r}")

        if row["language"] != "tr":
            raise ValueError(f"Turkish Prompt-Injection entry has non-Turkish language: {row['language']!r}")

        valid_families = {family.value for family in TurkishPromptInjectionFamily}
        expected_class = "injection" if row_label == 1 else "benign"
        if row["class"] != expected_class:
            raise ValueError(
                f"Turkish Prompt-Injection entry has class={row['class']!r}; expected {expected_class!r} "
                f"for label {row_label}"
            )

        if row_label == 1:
            if row["attack_family"] not in valid_families:
                raise ValueError(f"Invalid attack family in Turkish Prompt-Injection entry: {row['attack_family']!r}")
        elif row["attack_family"] != "benign":
            raise ValueError(
                f"Benign Turkish Prompt-Injection entry must have attack_family 'benign', "
                f"got {row['attack_family']!r}"
            )

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the selected records from Hugging Face.

        Args:
            cache: Whether Hugging Face may reuse its local cache.

        Returns:
            SeedDataset: Literal prompts with label, family, and split metadata.

        Raises:
            ValueError: If a row is malformed or the requested filters produce
                no records.
        """
        split_names = (
            ["train", "validation", "test"] if self._split is TurkishPromptInjectionSplit.ALL else [self._split.value]
        )

        rows: list[dict[str, Any]] = []
        for split_name in split_names:
            split_rows = await self._fetch_from_huggingface_async(
                dataset_name=self.HF_DATASET_NAME,
                config="default",
                split=split_name,
                cache=cache,
                revision=self.HF_DATASET_REVISION,
            )
            for split_row in split_rows:
                row = dict(split_row)
                self._validate_row(row=row)
                row["split"] = split_name
                rows.append(row)

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        authors = ["Enes Deniz"]
        groups = ["AltaySec"]
        seeds: list[SeedUnion] = []

        for row in rows:
            row_label = row["label"]
            if self._label is not TurkishPromptInjectionLabel.ALL and row_label != self._label.value:
                continue

            attack_family = row["attack_family"]
            if self._attack_families and row_label == 1 and attack_family not in self._attack_families:
                continue

            if row_label == 1:
                description = f"Turkish prompt-injection case for {attack_family.replace('tr_', '').replace('_', ' ')}."
            else:
                description = "Paired legitimate Turkish request that shares surface vocabulary with an attack."

            metadata = {key: row[key] for key in sorted(row) if key != "text"}
            metadata["dataset_version"] = self.DATASET_VERSION
            metadata["hf_revision"] = self.HF_DATASET_REVISION
            seeds.append(
                SeedPrompt(
                    value=str(row["text"]),
                    name=str(row["id"]),
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=[],
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata=metadata,
                )
            )

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Loaded {len(seeds)} records from {self.dataset_name}")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
