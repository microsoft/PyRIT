# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import re
import unicodedata
from collections import Counter
from enum import Enum
from typing import Any, ClassVar

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class TurkishConversationPromptInjectionLabel(Enum):
    """Filter examples by their prompt-injection label."""

    ATTACK = "attack"
    BENIGN = "benign"
    ALL = "all"


class TurkishConversationPromptInjectionSplit(Enum):
    """Select a published dataset split or concatenate all splits."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    ALL = "train+validation+test"


class TurkishConversationPromptInjectionAttackFamily(Enum):
    """Prompt-injection attack families represented in the dataset."""

    DIRECT_INSTRUCTION_OVERRIDE = "direct_instruction_override"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    ROLEPLAY_JAILBREAK = "roleplay_jailbreak"
    AUTHORITY_CLAIM_BYPASS = "authority_claim_bypass"
    SENSITIVE_DATA_EXFILTRATION = "sensitive_data_exfiltration"
    TOOL_ACTION_ABUSE = "tool_action_abuse"
    INDIRECT_CONTENT_INJECTION = "indirect_content_injection"
    RAG_CONTEXT_POISONING = "rag_context_poisoning"
    MEMORY_CONTEXT_POISONING = "memory_context_poisoning"
    OBFUSCATION_CODE_SWITCHING = "obfuscation_code_switching"


class _TurkishConversationPromptInjectionDataset(_RemoteDatasetLoader):
    """
    Load the Turkish Conversation Prompt-Injection Dataset from Hugging Face.

    The dataset contains 750 synthetic, curated Turkish examples: 600
    legitimate user requests and 150 prompt-injection attempts. It includes
    150 benign boundary cases paired with related attacks so defenses can be
    evaluated on both attack detection and over-refusal. Attack rows cover 10
    prompt-injection families across direct user, agent/tool,
    retrieved-document, and memory contexts. Rows contain inputs rather than
    model responses, and attack labels do not claim a verified bypass against
    any model.

    By default, the loader returns attack examples from all published splits.
    Use ``label=TurkishConversationPromptInjectionLabel.BENIGN`` to load
    legitimate requests or ``label=TurkishConversationPromptInjectionLabel.ALL``
    to load both classes. Split and attack-family filters can be combined with
    the label filter. Benign rows have ``attack_family="none"`` and are excluded
    whenever an attack-family filter is supplied. Attack families describe
    delivery techniques rather than resulting harms, so they are preserved in
    seed metadata instead of being mapped to PyRIT's harm-category taxonomy.
    Pair IDs are provenance metadata rather than prompt-group aliases because
    each side is an independent evaluation case. The loader pins version 1.0.2
    to an immutable Hugging Face revision and validates the complete published
    release contract before filtering. The data is synthetic, was curated by
    one author, and does not claim independent annotation or inter-annotator
    agreement.

    References:
        - [@deniz2026turkishpromptinjection]
        - https://doi.org/10.5281/zenodo.21379389
        - https://huggingface.co/datasets/3nesdeniz/turkish-conversation-prompt-injection
        - https://github.com/3nesdeniz/turkish-conversation-prompt-injection

    License: CC BY 4.0

    Warning: Attack rows contain adversarial instructions intended for
    authorized LLM security testing. Use them only in controlled environments.
    """

    HF_DATASET_NAME: ClassVar[str] = "3nesdeniz/turkish-conversation-prompt-injection"
    HF_DATASET_REVISION: ClassVar[str] = "29d7593984f563c4ad56876aa800b9ffd948a2fb"
    DATASET_VERSION: ClassVar[str] = "1.0.2"
    DOI_URL: ClassVar[str] = "https://doi.org/10.5281/zenodo.21379389"
    SOURCE_URL: ClassVar[str] = f"https://huggingface.co/datasets/{HF_DATASET_NAME}/tree/{HF_DATASET_REVISION}"

    _AUTHORS: ClassVar[list[str]] = ["Enes Deniz"]
    _GROUPS: ClassVar[list[str]] = ["AltaySec"]
    _DESCRIPTION: ClassVar[str] = (
        "A synthetic, curated Turkish dataset for studying the boundary between legitimate "
        "user requests and prompt-injection attempts, including paired benign "
        "boundary cases and 10 attack families."
    )
    _LABEL_VALUES: ClassVar[dict[TurkishConversationPromptInjectionLabel, int]] = {
        TurkishConversationPromptInjectionLabel.ATTACK: 1,
        TurkishConversationPromptInjectionLabel.BENIGN: 0,
    }
    _REQUIRED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "text",
            "label",
            "category",
            "attack_family",
            "source_context",
            "pair_id",
            "source_type",
            "split",
        }
    )
    _BENIGN_CATEGORIES: ClassVar[frozenset[str]] = frozenset({"benign_boundary", "benign_daily", "benign_technical"})
    # Version 1.0.2 is a single-language Turkish dataset and does not publish a
    # row-level `language` field. These are its complete source-context values.
    _SOURCE_CONTEXT_VALUES: ClassVar[frozenset[str]] = frozenset(
        {
            "agent_tool_request",
            "calendar_event",
            "chat_message",
            "code_comment",
            "conversation_memory",
            "direct_user",
            "document",
            "email",
            "image_ocr",
            "issue_description",
            "pdf_document",
            "retrieved_document",
            "spreadsheet_cell",
            "support_ticket",
            "tool_output",
            "transcript",
            "web_page",
        }
    )
    _ATTACK_FAMILY_VALUES: ClassVar[frozenset[str]] = frozenset(
        family.value for family in TurkishConversationPromptInjectionAttackFamily
    )
    _ROW_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^tcpi_(?:p\d{4}_[ab]|b\d{4})$")
    _PAIR_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^pair_\d{4}$")
    _CONTROL_CHARACTER_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    _EXPECTED_SPLIT_COUNTS: ClassVar[dict[str, int]] = {
        "train": 530,
        "validation": 100,
        "test": 120,
    }
    _EXPECTED_LABEL_COUNTS: ClassVar[dict[str, dict[int, int]]] = {
        "train": {0: 430, 1: 100},
        "validation": {0: 80, 1: 20},
        "test": {0: 90, 1: 30},
    }
    _EXPECTED_CATEGORY_COUNTS: ClassVar[dict[str, dict[str, int]]] = {
        "train": {
            "benign_boundary": 100,
            "benign_daily": 303,
            "benign_technical": 27,
            "prompt_injection": 100,
        },
        "validation": {
            "benign_boundary": 20,
            "benign_daily": 52,
            "benign_technical": 8,
            "prompt_injection": 20,
        },
        "test": {
            "benign_boundary": 30,
            "benign_daily": 55,
            "benign_technical": 5,
            "prompt_injection": 30,
        },
    }
    _EXPECTED_ATTACK_FAMILY_COUNTS: ClassVar[dict[str, dict[str, int]]] = {
        "train": {family.value: 10 for family in TurkishConversationPromptInjectionAttackFamily},
        "validation": {family.value: 2 for family in TurkishConversationPromptInjectionAttackFamily},
        "test": {family.value: 3 for family in TurkishConversationPromptInjectionAttackFamily},
    }
    _EXPECTED_PAIR_COUNT: ClassVar[int] = 150
    _EXPECTED_UNPAIRED_COUNT: ClassVar[int] = 450

    harm_categories: list[str] = []
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 750 curated examples across all published splits
    tags: frozenset[str] = frozenset(
        {"safety", "jailbreak", "synthetic", "cybersecurity", "agent_security", "prompt_injection"}
    )

    def __init__(
        self,
        *,
        label: TurkishConversationPromptInjectionLabel = TurkishConversationPromptInjectionLabel.ATTACK,
        split: TurkishConversationPromptInjectionSplit = TurkishConversationPromptInjectionSplit.ALL,
        attack_families: list[TurkishConversationPromptInjectionAttackFamily] | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the Turkish Conversation Prompt-Injection dataset loader.

        Args:
            label: Select attack, benign, or all examples. Defaults to attack.
            split: Select train, validation, test, or all published splits.
                Defaults to all splits.
            attack_families: Optional non-empty list of attack families to keep.
                Benign rows are excluded when this filter is set because their
                published ``attack_family`` value is ``"none"``.
            token: Optional Hugging Face token. If omitted, reads
                ``HUGGINGFACE_TOKEN`` from the environment.

        Raises:
            ValueError: If an enum value is invalid or ``attack_families`` is empty.
        """
        self._validate_enum(
            value=label,
            enum_cls=TurkishConversationPromptInjectionLabel,
            label="label",
        )
        self._validate_enum(
            value=split,
            enum_cls=TurkishConversationPromptInjectionSplit,
            label="split",
        )
        if attack_families is not None:
            if not attack_families:
                raise ValueError("`attack_families` must be a non-empty list (pass None to include all families)")
            self._validate_enums(
                values=attack_families,
                enum_cls=TurkishConversationPromptInjectionAttackFamily,
                label="attack_family",
            )

        self.label = label
        self.split = split
        self.attack_families = list(attack_families) if attack_families is not None else None
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")
        self.source = self.SOURCE_URL

    @property
    @override
    def dataset_name(self) -> str:
        """The registered dataset name."""
        return "turkish_conversation_prompt_injection"

    @classmethod
    def _validate_row(cls, *, row: dict[str, Any], fetched_split: str) -> None:
        """
        Validate the published schema and label-dependent invariants.

        Args:
            row: One source row from the pinned Hugging Face release.
            fetched_split: The split requested from Hugging Face.

        Raises:
            ValueError: If the row violates the published dataset schema.
        """
        fields = row.keys()
        missing = cls._REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"Missing keys in Turkish prompt-injection entry: {', '.join(sorted(missing))}")
        unexpected = fields - cls._REQUIRED_FIELDS
        if unexpected:
            raise ValueError(f"Unexpected keys in Turkish prompt-injection entry: {', '.join(sorted(unexpected))}")

        for key in ("id", "text", "source_context"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"Turkish prompt-injection entry has an invalid `{key}` value: {row[key]!r}")

        identifier = row["id"]
        if not cls._ROW_ID_PATTERN.fullmatch(identifier):
            raise ValueError(f"Turkish prompt-injection entry has an invalid `id` format: {identifier!r}")

        text = row["text"]
        if cls._CONTROL_CHARACTER_PATTERN.search(text):
            raise ValueError(f"Turkish prompt-injection entry {identifier!r} contains control characters")

        source_context = row["source_context"]
        if source_context not in cls._SOURCE_CONTEXT_VALUES:
            raise ValueError(
                f"Turkish prompt-injection entry has an unknown `source_context` value: {source_context!r}"
            )

        row_split = row["split"]
        if row_split != fetched_split:
            raise ValueError(f"Dataset row split {row_split!r} does not match fetched split {fetched_split!r}")

        row_label = row["label"]
        if not isinstance(row_label, int) or isinstance(row_label, bool) or row_label not in {0, 1}:
            raise ValueError(f"Turkish prompt-injection entry has an invalid `label` value: {row_label!r}")

        category = row["category"]
        valid_categories = cls._BENIGN_CATEGORIES | {"prompt_injection"}
        if category not in valid_categories:
            raise ValueError(f"Turkish prompt-injection entry has an invalid `category` value: {category!r}")

        attack_family = row["attack_family"]
        if not isinstance(attack_family, str):
            raise ValueError(f"Turkish prompt-injection entry has an invalid `attack_family` value: {attack_family!r}")

        pair_id = row["pair_id"]
        if pair_id is not None and (not isinstance(pair_id, str) or not pair_id.strip()):
            raise ValueError(f"Turkish prompt-injection entry has an invalid `pair_id` value: {pair_id!r}")
        if pair_id is not None and not cls._PAIR_ID_PATTERN.fullmatch(pair_id):
            raise ValueError(f"Turkish prompt-injection entry has an invalid `pair_id` format: {pair_id!r}")

        if row["source_type"] != "synthetic_curated":
            raise ValueError(
                f"Turkish prompt-injection entry has an invalid `source_type` value: {row['source_type']!r}"
            )

        if row_label == 1:
            if category != "prompt_injection":
                raise ValueError(
                    f"Turkish prompt-injection attack entry has category={category!r}; expected 'prompt_injection'"
                )
            if attack_family not in cls._ATTACK_FAMILY_VALUES:
                raise ValueError(f"Turkish prompt-injection attack has an unknown family: {attack_family!r}")
            if pair_id is None:
                raise ValueError("Turkish prompt-injection attack entry must include a `pair_id`")
            expected_id = f"tcpi_p{pair_id.removeprefix('pair_')}_a"
            if identifier != expected_id:
                raise ValueError(f"Turkish prompt-injection attack ID {identifier!r} does not match pair {pair_id!r}")
            return

        if category not in cls._BENIGN_CATEGORIES:
            raise ValueError(f"Turkish benign entry has category={category!r}; expected a benign category")
        if attack_family != "none":
            raise ValueError(f"Turkish benign entry has attack_family={attack_family!r}; expected 'none'")
        if category == "benign_boundary" and pair_id is None:
            raise ValueError("Turkish benign-boundary entry must include a `pair_id`")
        if category != "benign_boundary" and pair_id is not None:
            raise ValueError(f"Unpaired Turkish benign entry unexpectedly has pair_id={pair_id!r}")
        if pair_id is not None:
            expected_id = f"tcpi_p{pair_id.removeprefix('pair_')}_b"
            if identifier != expected_id:
                raise ValueError(f"Turkish benign ID {identifier!r} does not match pair {pair_id!r}")
        elif not re.fullmatch(r"tcpi_b\d{4}", identifier):
            raise ValueError(f"Unpaired Turkish benign entry has an invalid `id` format: {identifier!r}")

    @staticmethod
    def _normalize_text(*, text: str) -> str:
        """
        Normalize text using the release validator's duplicate-detection policy.

        Args:
            text: Published prompt text.

        Returns:
            The NFKC-normalized, case-folded text without punctuation.
        """
        normalized = unicodedata.normalize("NFKC", text).casefold()
        without_punctuation = "".join(
            " " if character.isspace() or unicodedata.category(character).startswith("P") else character
            for character in normalized
        )
        return re.sub(r" +", " ", without_punctuation).strip()

    @staticmethod
    def _validate_pair_integrity(*, rows: list[dict[str, Any]]) -> None:
        """
        Ensure IDs are unique and every boundary pair is complete and consistent.

        Args:
            rows: All rows fetched for the selected split or splits.

        Raises:
            ValueError: If IDs repeat or a pair is incomplete or mismatched.
        """
        rows_by_pair: dict[str, list[dict[str, Any]]] = {}
        seen_ids: set[str] = set()
        for row in rows:
            identifier = row["id"]
            if identifier in seen_ids:
                raise ValueError(f"Duplicate Turkish prompt-injection entry ID: {identifier}")
            seen_ids.add(identifier)

            pair_id = row["pair_id"]
            if pair_id is not None:
                rows_by_pair.setdefault(pair_id, []).append(row)

        for pair_id, pair_rows in rows_by_pair.items():
            if len(pair_rows) != 2 or sorted(row["label"] for row in pair_rows) != [0, 1]:
                raise ValueError(f"Turkish prompt-injection pair {pair_id!r} must contain exactly labels 0 and 1")
            for field in ("split", "source_context", "source_type"):
                if len({row[field] for row in pair_rows}) != 1:
                    raise ValueError(f"Turkish prompt-injection pair {pair_id!r} has mismatched `{field}` values")

    @classmethod
    def _validate_release_contract(cls, *, rows: list[dict[str, Any]], split_names: list[str]) -> None:
        """
        Validate the fixed composition of the pinned dataset release.

        Args:
            rows: All rows fetched for the requested source splits.
            split_names: Source splits requested from Hugging Face.

        Raises:
            ValueError: If counts, identifiers, or normalized text uniqueness
                differ from the published release contract.
        """
        expected_total = sum(cls._EXPECTED_SPLIT_COUNTS[split_name] for split_name in split_names)
        if len(rows) != expected_total:
            raise ValueError(
                f"Turkish prompt-injection release count mismatch: expected {expected_total}, found {len(rows)}"
            )

        expected_labels: Counter[int] = Counter()
        expected_categories: Counter[str] = Counter()
        for split_name in split_names:
            expected_labels.update(cls._EXPECTED_LABEL_COUNTS[split_name])
            expected_categories.update(cls._EXPECTED_CATEGORY_COUNTS[split_name])

        label_counts = Counter(row["label"] for row in rows)
        if label_counts != expected_labels:
            raise ValueError(
                f"Turkish prompt-injection label counts mismatch: expected {dict(expected_labels)}, "
                f"found {dict(label_counts)}"
            )

        category_counts = Counter(row["category"] for row in rows)
        if category_counts != expected_categories:
            raise ValueError(
                f"Turkish prompt-injection category counts mismatch: expected {dict(expected_categories)}, "
                f"found {dict(category_counts)}"
            )

        expected_family_counts: Counter[str] = Counter()
        for split_name in split_names:
            expected_family_counts.update(cls._EXPECTED_ATTACK_FAMILY_COUNTS[split_name])
        family_counts = Counter(row["attack_family"] for row in rows if row["label"] == 1)
        if family_counts != expected_family_counts:
            raise ValueError(
                f"Turkish prompt-injection attack-family counts mismatch: "
                f"expected {dict(expected_family_counts)}, found {dict(family_counts)}"
            )

        expected_pair_count = expected_labels[1]
        pair_ids = {row["pair_id"] for row in rows if row["pair_id"] is not None}
        if len(pair_ids) != expected_pair_count:
            raise ValueError(
                f"Turkish prompt-injection pair count mismatch: expected {expected_pair_count}, found {len(pair_ids)}"
            )

        if set(split_names) == cls._EXPECTED_SPLIT_COUNTS.keys():
            expected_pair_ids = {f"pair_{index:04d}" for index in range(1, cls._EXPECTED_PAIR_COUNT + 1)}
            if pair_ids != expected_pair_ids:
                missing = sorted(expected_pair_ids - pair_ids)
                unexpected = sorted(pair_ids - expected_pair_ids)
                raise ValueError(
                    f"Turkish prompt-injection pair IDs mismatch: missing {missing[:5]}, unexpected {unexpected[:5]}"
                )

            expected_row_ids = {
                identifier
                for index in range(1, cls._EXPECTED_PAIR_COUNT + 1)
                for identifier in (f"tcpi_p{index:04d}_a", f"tcpi_p{index:04d}_b")
            }
            expected_row_ids.update(f"tcpi_b{index:04d}" for index in range(1, cls._EXPECTED_UNPAIRED_COUNT + 1))
            row_ids = {row["id"] for row in rows}
            if row_ids != expected_row_ids:
                missing = sorted(expected_row_ids - row_ids)
                unexpected = sorted(row_ids - expected_row_ids)
                raise ValueError(
                    f"Turkish prompt-injection row IDs mismatch: missing {missing[:5]}, unexpected {unexpected[:5]}"
                )

        normalized_texts: dict[str, str] = {}
        for row in rows:
            normalized_text = cls._normalize_text(text=row["text"])
            duplicate_id = normalized_texts.get(normalized_text)
            if duplicate_id is not None:
                raise ValueError(
                    f"Duplicate normalized Turkish prompt-injection text in IDs {duplicate_id!r} and {row['id']!r}"
                )
            normalized_texts[normalized_text] = row["id"]

    def _matches_filters(self, *, item: dict[str, Any]) -> bool:
        """Return whether a source row matches the configured filters."""
        expected_label = self._LABEL_VALUES.get(self.label)
        if expected_label is not None and item.get("label") != expected_label:
            return False

        if self.attack_families is not None:
            selected_families = {family.value for family in self.attack_families}
            if item.get("attack_family") not in selected_families:
                return False

        return True

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch and filter the dataset, preserving row-level provenance.

        Args:
            cache: Whether to reuse the Hugging Face dataset cache.

        Returns:
            A SeedDataset containing literal Turkish SeedPrompt objects.

        Raises:
            ValueError: If no rows remain after filtering.
        """
        family_values = [family.value for family in self.attack_families] if self.attack_families else None
        logger.info(
            "Loading %s (label=%s, split=%s, attack_families=%s)",
            self.HF_DATASET_NAME,
            self.label.value,
            self.split.name.lower(),
            family_values,
        )

        split_names = (
            ["train", "validation", "test"]
            if self.split is TurkishConversationPromptInjectionSplit.ALL
            else [self.split.value]
        )
        data: list[dict[str, Any]] = []
        for split_name in split_names:
            split_data = await self._fetch_from_huggingface_async(
                dataset_name=self.HF_DATASET_NAME,
                config="default",
                split=split_name,
                cache=cache,
                token=self.token,
                revision=self.HF_DATASET_REVISION,
            )
            for split_item in split_data:
                row = dict(split_item)
                self._validate_row(row=row, fetched_split=split_name)
                data.append(row)

        self._validate_pair_integrity(rows=data)
        self._validate_release_contract(rows=data, split_names=split_names)

        seed_prompts: list[SeedUnion] = []
        for item in data:
            if not self._matches_filters(item=item):
                continue

            attack_family = item["attack_family"]
            seed_prompts.append(
                SeedPrompt(
                    value=item["text"],
                    name=item["id"],
                    data_type="text",
                    dataset_name=self.dataset_name,
                    # Attack families (for example, roleplay or obfuscation) are
                    # techniques rather than harms. Keep them in metadata without
                    # conflating them with PyRIT's canonical harm taxonomy.
                    harm_categories=[],
                    description=self._DESCRIPTION,
                    source=self.SOURCE_URL,
                    authors=self._AUTHORS,
                    groups=self._GROUPS,
                    metadata={
                        "id": item["id"],
                        "label": item["label"],
                        "category": item["category"],
                        "attack_family": attack_family,
                        "source_context": item["source_context"],
                        "pair_id": item["pair_id"],
                        "source_type": item["source_type"],
                        "split": item["split"],
                        "dataset_version": self.DATASET_VERSION,
                        "hf_revision": self.HF_DATASET_REVISION,
                        "doi": self.DOI_URL,
                    },
                )
            )

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info("Successfully loaded %d prompts from %s", len(seed_prompts), self.HF_DATASET_NAME)
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
