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

    The dataset contains 750 curated Turkish examples: 600 legitimate user
    requests and 150 prompt-injection attacks. It includes 150 benign boundary
    cases paired with related attacks so defenses can be evaluated on both
    attack detection and over-refusal. Attack rows cover 10 prompt-injection
    families across direct user, agent/tool, retrieved-document, and memory
    contexts.

    By default, the loader returns attack examples from all published splits.
    Use ``label=TurkishConversationPromptInjectionLabel.BENIGN`` to load
    legitimate requests or ``label=TurkishConversationPromptInjectionLabel.ALL``
    to load both classes. Split and attack-family filters can be combined with
    the label filter. Benign rows have ``attack_family="none"`` and are excluded
    whenever an attack-family filter is supplied.

    References:
        - [@deniz2026turkishpromptinjection]
        - https://huggingface.co/datasets/3nesdeniz/turkish-conversation-prompt-injection
        - https://github.com/3nesdeniz/turkish-conversation-prompt-injection

    License: CC BY 4.0

    Warning: Attack rows contain adversarial instructions intended for
    authorized LLM security testing. Use them only in controlled environments.
    """

    HF_DATASET_NAME: ClassVar[str] = "3nesdeniz/turkish-conversation-prompt-injection"
    SOURCE_URL: ClassVar[str] = f"https://huggingface.co/datasets/{HF_DATASET_NAME}"

    _AUTHORS: ClassVar[list[str]] = ["Enes Deniz"]
    _GROUPS: ClassVar[list[str]] = ["AltaySec"]
    _DESCRIPTION: ClassVar[str] = (
        "A curated Turkish dataset for studying the boundary between legitimate "
        "user requests and prompt-injection attacks, including paired benign "
        "boundary cases and 10 attack families."
    )
    _LABEL_VALUES: ClassVar[dict[TurkishConversationPromptInjectionLabel, int]] = {
        TurkishConversationPromptInjectionLabel.ATTACK: 1,
        TurkishConversationPromptInjectionLabel.BENIGN: 0,
    }

    harm_categories: list[str] = [family.value for family in TurkishConversationPromptInjectionAttackFamily]
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

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split.value,
            cache=cache,
            token=self.token,
        )

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
                    harm_categories=[] if attack_family == "none" else [attack_family],
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
                    },
                )
            )

        if not seed_prompts:
            raise ValueError(
                "SeedDataset is empty after filtering "
                f"(label={self.label.value!r}, split={self.split.name.lower()!r}, "
                f"attack_families={family_values!r})."
            )

        logger.info("Successfully loaded %d prompts from %s", len(seed_prompts), self.HF_DATASET_NAME)
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
