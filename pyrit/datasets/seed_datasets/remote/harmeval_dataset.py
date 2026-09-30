# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from enum import Enum

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedObjective, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class HarmEvalTopic(Enum):
    """
    Filter for which HarmEval policy categories to load.

    Values are the verbatim strings stored in the upstream ``Topic`` column. The
    taxonomy is the list of prohibited scenarios from OpenAI's usage policies and
    Meta's acceptable use policy, the same 11-category framing used by
    CategoricalHarmfulQA (``_CategoricalHarmfulQADataset``).
    """

    ADULT_CONTENT = "Adult Content"
    CHILD_ABUSE_CONTENT = "Child Abuse Content"
    ECONOMIC_HARM = "Economic Harm"
    FRAUD_DECEPTION = "Fraud/Deception"
    HATE_HARASS_VIOLENCE = "Hate/Harass/Violence"
    ILLEGAL_ACTIVITY = "Illegal Activity"
    MALWARE = "Malware"
    PHYSICAL_HARM = "Physical Harm"
    POLITICAL_CAMPAIGNING = "Political Campaigning"
    PRIVACY_VIOLATION_ACTIVITY = "Privacy Violation Activity"
    TAILORED_FINANCIAL_ADVICE = "Tailored Financial Advice"


_AUTHORS: list[str] = [
    "Somnath Banerjee",
    "Sayan Layek",
    "Soham Tripathy",
    "Shanu Kumar",
    "Animesh Mukherjee",
    "Rima Hazra",
]

_GROUPS: list[str] = [
    "Indian Institute of Technology Kharagpur",
    "Microsoft IDC",
    "Singapore University of Technology and Design",
]

# The upstream release is a single CSV (Safe_Infer_Final_Dataset_550_Without_Answer.csv)
# with these two columns. Issue #2770 notes the column names were never verified against
# the gated data, so a drift here is reported explicitly rather than silently yielding
# an empty dataset.
_REQUIRED_COLUMNS: frozenset[str] = frozenset({"Question", "Topic"})

_DESCRIPTION: str = (
    "HarmEval contains ~550 crafted harmful queries spanning the 11 prohibited-use "
    "categories drawn from OpenAI's usage policies and Meta's acceptable use policy. "
    "Queries were filtered with a two-step verification process: GPT-4 harmfulness "
    "classification followed by a Perspective API toxicity score of at least 0.7. "
    "Introduced in 'SafeInfer: Context Adaptive Decoding Time Safety Alignment for "
    "Large Language Models' (Banerjee et al., AAAI-2025)."
)


class _HarmEvalDataset(_RemoteDatasetLoader):
    """
    Loader for the HarmEval dataset from HuggingFace.

    HarmEval is the safety benchmark released with SafeInfer: ~550 harmful queries
    over 11 policy categories taken from OpenAI's usage policies and Meta's
    acceptable use policy. Each row is a single harmful question; the loader emits
    them as ``SeedObjective`` values, matching the sibling CategoricalHarmfulQA
    loader that uses the same taxonomy.

    This loader covers dataset integration only. SafeInfer's decoding-time
    alignment method is out of scope.

    References:
        - https://huggingface.co/datasets/SoftMINER-Group/HarmEval
        - https://github.com/NeuralSentinel/SafeInfer
        - [@banerjee2025safeinfer]

    License: The HuggingFace metadata tags the dataset Apache-2.0, but access is
    granted through a gate that additionally requires the requester to attest
    "I agree to use this dataset for non-commercial use ONLY" and to agree not to
    use the dataset for experiments that cause harm to human subjects. Which of
    the two governs is not for this docstring to decide; both are recorded here
    so a user sees the gate terms before accepting them. PyRIT does not
    redistribute the data; it is fetched at runtime by the user, under whatever
    they accepted at the gate.

    Note: This is a gated dataset on HuggingFace. You must accept the terms at
    https://huggingface.co/datasets/SoftMINER-Group/HarmEval before use, and provide
    a HuggingFace token (either via the ``token`` constructor argument or by setting
    the ``HUGGINGFACE_TOKEN`` environment variable).

    Warning: This dataset contains harmful questions designed to evaluate LLM
    safety, including child abuse, weapons, and other deeply harmful topics.
    """

    HF_DATASET_NAME: str = "SoftMINER-Group/HarmEval"

    # HarmEval and CategoricalHarmfulQA are built against the same OpenAI/Meta
    # prohibited-use taxonomy, so this map mirrors _CategoricalHarmfulQADataset's
    # for every label the two datasets share. Five of the eleven topics ("Child
    # Abuse Content", "Economic Harm", "Hate/Harass/Violence", "Privacy Violation
    # Activity", "Tailored Financial Advice") resolve to OTHER without an override.
    # HarmEval has no subcategory column, so "Child Abuse Content" is mapped at the
    # category level only.
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        HarmEvalTopic.ADULT_CONTENT.value: [HarmCategory.SEXUAL_CONTENT],
        HarmEvalTopic.CHILD_ABUSE_CONTENT.value: [
            HarmCategory.GROOMING,
            HarmCategory.SEXUAL_CONTENT,
            HarmCategory.CHILD_LEAKAGE,
        ],
        HarmEvalTopic.ECONOMIC_HARM.value: [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        HarmEvalTopic.FRAUD_DECEPTION.value: [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        HarmEvalTopic.HATE_HARASS_VIOLENCE.value: [
            HarmCategory.HATE_SPEECH,
            HarmCategory.HARASSMENT,
            HarmCategory.VIOLENT_CONTENT,
        ],
        HarmEvalTopic.ILLEGAL_ACTIVITY.value: [HarmCategory.COORDINATION_HARM],
        HarmEvalTopic.MALWARE.value: [HarmCategory.MALWARE],
        HarmEvalTopic.PHYSICAL_HARM.value: [HarmCategory.VIOLENT_CONTENT, HarmCategory.COORDINATION_HARM],
        HarmEvalTopic.POLITICAL_CAMPAIGNING.value: [HarmCategory.CAMPAIGNING],
        HarmEvalTopic.PRIVACY_VIOLATION_ACTIVITY.value: [HarmCategory.PPI],
        HarmEvalTopic.TAILORED_FINANCIAL_ADVICE.value: [HarmCategory.FINANCIAL_ADVICE],
    }

    # Metadata
    harm_categories: list[str] = [topic.value.lower() for topic in HarmEvalTopic]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # ~550 queries
    tags: set[str] = {"safety", "objectives"}

    def __init__(
        self,
        *,
        topics: list[HarmEvalTopic] | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the HarmEval dataset loader.

        Args:
            topics: List of HarmEvalTopic values to filter by. Defaults to None
                (all 11 policy categories).
            token: Hugging Face authentication token. If not provided, reads from
                the HUGGINGFACE_TOKEN env var.

        Raises:
            ValueError: If ``topics`` is an empty list, or contains a value that is
                not a ``HarmEvalTopic``.
        """
        if topics is not None:
            if not topics:
                raise ValueError("`topics` must be a non-empty list (pass None to include all topics)")
            self._validate_enums(values=topics, enum_cls=HarmEvalTopic, label="topics")

        # Copied, so a caller mutating its own list afterwards cannot change
        # what this loader filters on.
        self.topics = list(topics) if topics is not None else None
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "harmeval"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the HarmEval dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset of HarmEval objectives filtered by ``self.topics``.
            Each SeedObjective carries the standardized ``harm_categories`` and keeps the
            verbatim source label in ``metadata["topic"]``.

        Raises:
            ValueError: If a row is missing the expected columns, or if no objectives
                remain after filtering.
        """
        selected = {topic.value for topic in self.topics} if self.topics is not None else None
        logger.info(
            f"Loading HarmEval dataset from {self.HF_DATASET_NAME} "
            f"(topics={sorted(selected) if selected is not None else 'all'})"
        )

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
            token=self.token,
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"

        seed_objectives: list[SeedUnion] = []
        for item in data:
            missing_columns = _REQUIRED_COLUMNS - item.keys()
            if missing_columns:
                raise ValueError(
                    f"HarmEval row is missing expected column(s) {sorted(missing_columns)}; "
                    f"row has {sorted(item.keys())}. The upstream schema may have changed."
                )

            question = str(item["Question"] or "").strip()
            topic = str(item["Topic"] or "").strip()

            if not question:
                logger.warning("[HarmEval] Skipping row with an empty Question field")
                continue

            if selected is not None and topic not in selected:
                continue

            seed_objectives.append(
                SeedObjective(
                    value=question,
                    name="HarmEval",
                    dataset_name=self.dataset_name,
                    harm_categories=self._standardize_harm_categories(
                        topic,
                        alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                    ),
                    description=_DESCRIPTION,
                    source=source_url,
                    authors=_AUTHORS,
                    groups=_GROUPS,
                    metadata={"topic": topic},
                )
            )

        if not seed_objectives:
            raise ValueError(
                "SeedDataset cannot be empty. Check your filter criteria. "
                f"HarmEval filter: topics={sorted(selected) if selected is not None else '(any)'}."
            )

        logger.info(f"Successfully loaded {len(seed_objectives)} objectives from HarmEval dataset")

        return SeedDataset(seeds=seed_objectives, dataset_name=self.dataset_name)
