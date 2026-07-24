# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from enum import Enum
from typing import Any, ClassVar

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)

_HF_REPO_ID = "allenai/wildguardmix"

# Each WildGuardMix config has its own inner HuggingFace split name:
# wildguardtrain ships rows under "train", wildguardtest ships rows under "test".
_CONFIG_TO_HF_SPLIT: dict[str, str] = {
    "wildguardtrain": "train",
    "wildguardtest": "test",
}


class WildGuardMixSplit(Enum):
    """Which WildGuardMix config(s) to fetch."""

    TRAIN = "wildguardtrain"
    TEST = "wildguardtest"


class WildGuardMixPromptHarmLabel(Enum):
    """Labels assigned to the prompt by WildGuard's prompt-harm classifier."""

    HARMFUL = "harmful"
    UNHARMFUL = "unharmful"


class WildGuardMixAdversarial(Enum):
    """Whether the row carries an adversarial (jailbreak-style) prompt."""

    ADVERSARIAL = True
    VANILLA = False


class _WildGuardMixDataset(_RemoteDatasetLoader):
    """
    Loader for the WildGuardMix dataset (Allen Institute for AI).

    WildGuardMix bundles two configs:

    - ``wildguardtrain`` (~86,759 rows): a mix of synthetic and human-written prompts,
      both prompt-only and prompt+response pairs. Built primarily for training the
      WildGuard moderation classifier.
    - ``wildguardtest`` (~1,725 rows): human-annotated prompt+response pairs used for
      evaluation.

    By default this loader fetches BOTH configs and concatenates them. Pass ``splits``
    to narrow the result (e.g. ``splits=[WildGuardMixSplit.TEST]`` for the eval-only set).

    Each kept seed records its source split in ``metadata["split"]`` along with the
    classifier labels for downstream post-filtering.

    Gating: the dataset is gated under the AI2 Responsible Use Guidelines. To use this
    loader you must accept the terms on the HuggingFace dataset page and supply a HF
    access token (either via the ``token`` constructor argument or the
    ``HUGGINGFACE_TOKEN`` environment variable).

    Warning: the corpus contains adversarial prompts on sensitive topics (violence,
    self-harm, weapons, drugs, etc.). Reader / target discretion is recommended.

    License: Open Data Commons Attribution License (ODC-BY 1.0). Access is also
    gated behind the AI2 Responsible Use Guidelines click-through on HuggingFace
    (a separate access control, not the data license).
    Reference: [@han2024wildguard]
    HuggingFace: https://huggingface.co/datasets/allenai/wildguardmix
    """

    _AUTHORS: ClassVar[list[str]] = [
        "Seungju Han",
        "Kavel Rao",
        "Allyson Ettinger",
        "Liwei Jiang",
        "Bill Yuchen Lin",
        "Nathan Lambert",
        "Yejin Choi",
        "Nouha Dziri",
    ]
    _GROUPS: ClassVar[list[str]] = [
        "Allen Institute for AI",
        "University of Washington",
        "Seoul National University",
    ]
    HF_DATASET_NAME: str = _HF_REPO_ID
    # WildGuard stores 13 named risk subcategories plus an "others" bucket as
    # normalized slugs. "benign" is intentionally excluded from harm metadata.
    harm_categories: list[str] = [
        "causing_material_harm_by_disseminating_misinformation",
        "copyright_violations",
        "cyberattack",
        "defamation_encouraging_unethical_or_unsafe_actions",
        "disseminating_false_or_misleading_information_encouraging_disinformation_campaigns",
        "fraud_assisting_illegal_activities",
        "mental_health_over-reliance_crisis",
        "others",
        "private_information_individual",
        "sensitive_information_organization_government",
        "sexual_content",
        "social_stereotypes_and_unfair_discrimination",
        "toxic_language_hate_speech",
        "violence_and_physical_harm",
    ]
    modalities: list[str] = ["text"]
    size: str = "huge"
    tags: set[str] = {"safety", "jailbreak"}
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "causing_material_harm_by_disseminating_misinformation": [
            HarmCategory.INFO_INTEGRITY,
            HarmCategory.CURRENT_EVENTS_MISINFO,
        ],
        "copyright_violations": [HarmCategory.COPYRIGHT],
        "cyberattack": [HarmCategory.MALWARE, HarmCategory.COORDINATION_HARM],
        "defamation_encouraging_unethical_or_unsafe_actions": [
            HarmCategory.REPUTATIONAL_DAMAGE,
            HarmCategory.DANGEROUS_SITUATIONS,
        ],
        "disseminating_false_or_misleading_information_encouraging_disinformation_campaigns": [
            HarmCategory.INFO_INTEGRITY,
            HarmCategory.CAMPAIGNING,
        ],
        "fraud_assisting_illegal_activities": [
            HarmCategory.SCAMS,
            HarmCategory.DECEPTION,
            HarmCategory.COORDINATION_HARM,
        ],
        "mental_health_over-reliance_crisis": [
            HarmCategory.MENTAL_HEALTH,
            HarmCategory.EMOTIONAL,
        ],
        "others": [HarmCategory.OTHER],
        "private_information_individual": [HarmCategory.PPI],
        "sensitive_information_organization_government": [
            HarmCategory.PROPRIETARY_INFO,
            HarmCategory.HIGH_RISK_GOVERNMENT,
        ],
        "sexual_content": [HarmCategory.SEXUAL_CONTENT],
        "social_stereotypes_and_unfair_discrimination": [
            HarmCategory.REPRESENTATIONAL,
            HarmCategory.HATE_SPEECH,
        ],
        "toxic_language_hate_speech": [HarmCategory.HATE_SPEECH],
        "violence_and_physical_harm": [
            HarmCategory.VIOLENT_CONTENT,
            HarmCategory.VIOLENT_THREATS,
            HarmCategory.COORDINATION_HARM,
        ],
    }

    def __init__(
        self,
        *,
        splits: list[WildGuardMixSplit] | None = None,
        prompt_harm_labels: list[WildGuardMixPromptHarmLabel] | None = None,
        adversarial: list[WildGuardMixAdversarial] | None = None,
        prompt_only: bool = True,
        token: str | None = None,
    ) -> None:
        """
        Initialize the WildGuardMix dataset loader.

        Args:
            splits (list[WildGuardMixSplit] | None): Which configs to fetch. Defaults to
                both (``[WildGuardMixSplit.TRAIN, WildGuardMixSplit.TEST]``).
            prompt_harm_labels (list[WildGuardMixPromptHarmLabel] | None): Keep only
                rows whose ``prompt_harm_label`` is in this list. Defaults to
                ``[WildGuardMixPromptHarmLabel.HARMFUL]`` (red-team default). Pass both
                values to additionally include the benign companion prompts used for
                over-refusal sanity checks.
            adversarial (list[WildGuardMixAdversarial] | None): Keep only rows whose
                ``adversarial`` flag is in this list. Defaults to ``[ADVERSARIAL]``
                only — the `jailbreak` tag implies you want jailbreak-style prompts,
                and vanilla (direct, no-jailbreak) harmful prompts are largely
                covered by PyRIT's existing AdvBench loader. Pass both values to
                additionally include the vanilla harmful prompts.
            prompt_only (bool): When True, drop rows from the train split that include
                a response (i.e. keep only rows where ``response is None``). This makes
                the loader semantically match "jailbreak prompts" rather than
                "prompt+response classifier training data". The test split has no
                prompt-only rows, so this flag is a no-op there. Defaults to True.
            token (str | None): HuggingFace authentication token. If not provided,
                reads from ``HUGGINGFACE_TOKEN`` env var.

        Raises:
            ValueError: If any filter argument contains a non-enum value, or if
                ``splits`` is an empty list.
        """
        self.splits = self._resolve_splits(splits)
        self.prompt_harm_labels = self._resolve_prompt_harm_labels(prompt_harm_labels)
        self.adversarial = self._resolve_adversarial(adversarial)
        self.prompt_only = prompt_only
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")
        self.source = f"https://huggingface.co/datasets/{_HF_REPO_ID}"

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "wildguardmix"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch WildGuardMix and return as a SeedDataset.

        Iterates over the requested splits, fetches each from HuggingFace, applies the
        prompt-harm-label / adversarial / prompt-only filters, and concatenates the
        results into a single SeedDataset.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered WildGuardMix prompts.

        Raises:
            ValueError: If the filter combination produces zero seeds.
        """
        logger.info(
            f"Loading WildGuardMix dataset (splits={[s.value for s in self.splits]}, "
            f"prompt_harm_labels={[lbl.value for lbl in self.prompt_harm_labels]}, "
            f"adversarial={[a.value for a in self.adversarial]}, prompt_only={self.prompt_only})"
        )

        seed_prompts: list[SeedPrompt] = []
        for split in self.splits:
            rows = await self._fetch_from_huggingface_async(
                dataset_name=_HF_REPO_ID,
                config=split.value,
                split=_CONFIG_TO_HF_SPLIT[split.value],
                cache=cache,
                token=self.token,
            )
            seed_prompts.extend(self._rows_to_seeds(rows=rows, split=split))

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from WildGuardMix dataset")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _rows_to_seeds(self, *, rows: Any, split: WildGuardMixSplit) -> list[SeedPrompt]:
        """
        Convert raw HuggingFace rows from a single split into filtered SeedPrompts.

        Args:
            rows (Any): The HuggingFace dataset object for one config.
            split (WildGuardMixSplit): The split the rows came from.

        Returns:
            list[SeedPrompt]: SeedPrompts that survived the configured filters.
        """
        kept_labels = {lbl.value for lbl in self.prompt_harm_labels}
        kept_adversarial = {a.value for a in self.adversarial}

        seeds: list[SeedPrompt] = []
        for row in rows:
            prompt_text = row.get("prompt")
            if not prompt_text:
                continue

            prompt_harm_label = row.get("prompt_harm_label")
            if prompt_harm_label not in kept_labels:
                continue

            adversarial_flag = row.get("adversarial")
            if adversarial_flag not in kept_adversarial:
                continue

            response = row.get("response")
            if split is WildGuardMixSplit.TRAIN and self.prompt_only and response is not None:
                continue

            subcategory = row.get("subcategory")
            seeds.append(
                SeedPrompt(
                    value=prompt_text,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=(
                        self._standardize_harm_categories(
                            subcategory,
                            alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                        )
                        if prompt_harm_label == WildGuardMixPromptHarmLabel.HARMFUL.value
                        else []
                    ),
                    source=self.source,
                    authors=self._AUTHORS,
                    groups=self._GROUPS,
                    metadata={
                        "split": split.value,
                        "subcategory": subcategory,
                        "adversarial": adversarial_flag,
                        "prompt_harm_label": prompt_harm_label,
                        "response_harm_label": row.get("response_harm_label"),
                        "response_refusal_label": row.get("response_refusal_label"),
                        "has_response": response is not None,
                    },
                )
            )
        return seeds

    @staticmethod
    def _resolve_splits(splits: list[WildGuardMixSplit] | None) -> list[WildGuardMixSplit]:
        """
        Validate and normalize the requested list of splits.

        Args:
            splits (list[WildGuardMixSplit] | None): User-supplied list, or None for the default.

        Returns:
            list[WildGuardMixSplit]: A normalized list of splits.

        Raises:
            ValueError: If ``splits`` is an empty list or contains non-enum values.
        """
        if splits is None:
            return [WildGuardMixSplit.TRAIN, WildGuardMixSplit.TEST]
        if not splits:
            raise ValueError(
                "WildGuardMix splits must not be empty. Pass None to load both, or supply at least one "
                "WildGuardMixSplit value."
            )
        _RemoteDatasetLoader._validate_enums(splits, WildGuardMixSplit, "split")
        return list(splits)

    @staticmethod
    def _resolve_prompt_harm_labels(
        labels: list[WildGuardMixPromptHarmLabel] | None,
    ) -> list[WildGuardMixPromptHarmLabel]:
        """
        Validate and normalize the requested prompt-harm labels.

        Args:
            labels (list[WildGuardMixPromptHarmLabel] | None): User-supplied list, or None.

        Returns:
            list[WildGuardMixPromptHarmLabel]: Normalized list (defaults to ``[HARMFUL]``).

        Raises:
            ValueError: If ``labels`` is an empty list or contains non-enum values.
        """
        if labels is None:
            return [WildGuardMixPromptHarmLabel.HARMFUL]
        if not labels:
            raise ValueError(
                "WildGuardMix prompt_harm_labels must not be empty. Pass None to use the default [HARMFUL], "
                "or supply at least one WildGuardMixPromptHarmLabel value."
            )
        _RemoteDatasetLoader._validate_enums(labels, WildGuardMixPromptHarmLabel, "prompt_harm_label")
        return list(labels)

    @staticmethod
    def _resolve_adversarial(
        adversarial: list[WildGuardMixAdversarial] | None,
    ) -> list[WildGuardMixAdversarial]:
        """
        Validate and normalize the requested adversarial filter values.

        Args:
            adversarial (list[WildGuardMixAdversarial] | None): User-supplied list, or None.

        Returns:
            list[WildGuardMixAdversarial]: Normalized list (defaults to both values).

        Raises:
            ValueError: If ``adversarial`` is an empty list or contains non-enum values.
        """
        if adversarial is None:
            return [WildGuardMixAdversarial.ADVERSARIAL]
        if not adversarial:
            raise ValueError(
                "WildGuardMix adversarial must not be empty. Pass None to load the "
                "adversarial (jailbreak) subset, or supply at least one "
                "WildGuardMixAdversarial value."
            )
        _RemoteDatasetLoader._validate_enums(adversarial, WildGuardMixAdversarial, "adversarial")
        return list(adversarial)
