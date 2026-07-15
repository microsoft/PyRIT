# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
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
    legitimate user requests and 150 prompt-injection attacks. It includes 150
    benign boundary cases paired with related attacks so defenses can be
    evaluated on both attack detection and over-refusal. Attack rows cover 10
    prompt-injection families across direct user, agent/tool,
    retrieved-document, and memory contexts.

    By default, the loader returns attack examples from all published splits.
    Use ``label=TurkishConversationPromptInjectionLabel.BENIGN`` to load
    legitimate requests or ``label=TurkishConversationPromptInjectionLabel.ALL``
    to load both classes. Split and attack-family filters can be combined with
    the label filter. Benign rows have ``attack_family="none"`` and are excluded
    whenever an attack-family filter is supplied. Attack families describe
    delivery techniques rather than resulting harms, so they are preserved in
    seed metadata instead of being mapped to PyRIT's harm-category taxonomy.
    Pair IDs are provenance metadata rather than prompt-group aliases because
    each side is an independent evaluation case. The loader pins version 1.0.1
    to an immutable Hugging Face revision and validates the published schema,
    stable IDs, split claims, and complete pair structure before filtering.

    References:
        - [@deniz2026turkishpromptinjection]
        - https://huggingface.co/datasets/3nesdeniz/turkish-conversation-prompt-injection
        - https://github.com/3nesdeniz/turkish-conversation-prompt-injection

    License: CC BY 4.0

    Warning: Attack rows contain adversarial instructions intended for
    authorized LLM security testing. Use them only in controlled environments.
    """

    HF_DATASET_NAME: ClassVar[str] = "3nesdeniz/turkish-conversation-prompt-injection"
    HF_DATASET_REVISION: ClassVar[str] = "f80a4bb767f9797c64cb539b04e2a0bb78c6a302"
    DATASET_VERSION: ClassVar[str] = "1.0.1"
    SOURCE_URL: ClassVar[str] = f"https://huggingface.co/datasets/{HF_DATASET_NAME}"

    _AUTHORS: ClassVar[list[str]] = ["Enes Deniz"]
    _GROUPS: ClassVar[list[str]] = ["AltaySec"]
    _DESCRIPTION: ClassVar[str] = (
        "A synthetic, curated Turkish dataset for studying the boundary between legitimate "
        "user requests and prompt-injection attacks, including paired benign "
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
    # Version 1.0.1 is a single-language Turkish dataset and does not publish a
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

    harm_categories: list[str] = []
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 750 curated examples across all published splits
    tags: frozenset[str] = frozenset(
        {"safety", "jailbreak", "multilingual", "cybersecurity", "agent_security", "prompt_injection"}
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
        missing = cls._REQUIRED_FIELDS - row.keys()
        if missing:
            raise ValueError(f"Missing keys in Turkish prompt-injection entry: {', '.join(sorted(missing))}")

        for key in ("id", "text", "source_context"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"Turkish prompt-injection entry has an invalid `{key}` value: {row[key]!r}")

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
            return

        if category not in cls._BENIGN_CATEGORIES:
            raise ValueError(f"Turkish benign entry has category={category!r}; expected a benign category")
        if attack_family != "none":
            raise ValueError(f"Turkish benign entry has attack_family={attack_family!r}; expected 'none'")
        if category == "benign_boundary" and pair_id is None:
            raise ValueError("Turkish benign-boundary entry must include a `pair_id`")
        if category != "benign_boundary" and pair_id is not None:
            raise ValueError(f"Unpaired Turkish benign entry unexpectedly has pair_id={pair_id!r}")

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
                    },
                )
            )

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info("Successfully loaded %d prompts from %s", len(seed_prompts), self.HF_DATASET_NAME)
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
