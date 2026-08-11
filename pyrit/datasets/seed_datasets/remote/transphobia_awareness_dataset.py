# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import re
from typing import Any

import pandas as pd
from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _TransphobiaAwarenessDataset(_RemoteDatasetLoader):
    """
    Loader for the Transphobia-Awareness Dataset.

    This dataset contains user-generated queries related to transphobia with human
    annotations and model responses from Quora questions.

    Reference: [@scheuerman2025transphobia]
    Scheuerman, M., Weathington, K., Petterson, A., Doyle, D. T., Das, D., DeVito, M. A., & Brubaker, J.
    (2025). Quora Questions and LLM Responses on Transgender and Non-Binary Topics [Data set].
    Zenodo. https://doi.org/10.5281/zenodo.15482694

    Paper:
    Morgan Klaus Scheuerman, Katy Weathington, Adrian Petterson,
    Dylan Doyle, Dipto Das, Michael Ann DeVito, Jed Brubaker.
    "Transphobia is in the Eye of the Prompter:
    Trans-Centered Perspectives on Large Language Models."
    ACM Transactions on Computer-Human Interaction (2025).
    """

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 300 Quora questions on transgender / non-binary topics
    tags: frozenset[str] = frozenset({"default", "safety", "bias"})

    RATINGS_URL = "https://zenodo.org/records/15482694/files/Ratings.xlsx?download=1"
    QUESTION_RESPONSE_PAIRS_URL = "https://zenodo.org/records/15482694/files/Question_Response_Pairs.xlsx?download=1"
    QUORA_QUESTION_LIST_URL = "https://zenodo.org/records/15482694/files/Quora%20Question%20List.xlsx?download=1"

    def __init__(self, *, source: str = RATINGS_URL) -> None:
        """
        Initialize the Transphobia-Awareness dataset loader.

        Args:
            source: URL to the Ratings Excel file. Defaults to Zenodo URL.
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "transphobia_awareness"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Transphobia-Awareness dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.
                   Note: Excel files are always fetched fresh due to pandas requirements.

        Returns:
            SeedDataset: A SeedDataset containing prompts with transphobia awareness metadata.
        """
        logger.info(f"Loading Transphobia-Awareness dataset from {self.source}")

        # Fetch and parse the Excel file
        # Note: Excel files require pandas to read directly from URL
        ratings_df = pd.read_excel(self.source, header=1)

        # Map keywords for consistency
        ratings_df["keyword"] = ratings_df["keyword"].replace(
            {"Trans": "transgender", "Transgender": "transgender", "nonbinary": "non-binary"}
        )

        standardized_prompt_harm_categories = self._standardize_harm_categories(
            "transphobia",
            alias_overrides={"transphobia": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL]},
        )

        sentiment_col = (
            "Question sentiment: How transphobic is the question?\n\n"
            "Rate on a scale of 1-6, where 6 is the most transphobic"
        )

        seed_prompts: list[SeedUnion] = []

        for _, row in ratings_df.iterrows():
            metadata: dict[str, Any] = {
                "keyword": str(row["keyword"]),
            }

            # Extract the numeric question-sentiment rating (1-6) when present.
            question_sentiment: float | None = None
            if sentiment_col in row and pd.notna(row[sentiment_col]):
                # Extract numeric value from strings like "4 (Slightly Transphobic)"
                sentiment_value = str(row[sentiment_col])
                match = re.search(r"\d+\.?\d*", sentiment_value)
                if match:
                    question_sentiment = float(match.group())
                    metadata["question_sentiment"] = question_sentiment
                else:
                    # If no number found, store as string
                    metadata["question_sentiment"] = sentiment_value

            # Only questions that are themselves annotated as transphobic carry the
            # harm categories. On the 1-6 scale, 3 is "Neutral" and 4 is "Slightly
            # Transphobic", so a rating >= 4 marks a transphobic question. Neutral or
            # affirming questions (<= 3) and unrated questions are left with no harm
            # categories so benign trans-topic prompts are not mislabeled as hate.
            row_harm_categories = (
                standardized_prompt_harm_categories
                if question_sentiment is not None and question_sentiment >= 4
                else []
            )

            prompt = SeedPrompt(
                value=str(row["Quora Question"]),
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=row_harm_categories,
                description="Quora-style question for transphobia awareness and inclusivity evaluation.",
                metadata=metadata,
                source=self.source,
                authors=[
                    "Morgan Klaus Scheuerman",
                    "Katy Weathington",
                    "Adrian Petterson",
                    "Dylan Thomas Doyle",
                    "Dipto Das",
                    "Michael Ann DeVito",
                    "Jed R. Brubaker",
                ],
                groups=[
                    "Sony AI",
                    "University of Colorado Boulder",
                    "University of Toronto",
                    "Northeastern University",
                ],
            )
            seed_prompts.append(prompt)

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Transphobia-Awareness dataset")

        return SeedDataset(
            seeds=seed_prompts,
            dataset_name=self.dataset_name,
            harm_categories=standardized_prompt_harm_categories,
            description="Dataset for evaluating LLM responses for transphobia and inclusivity.",
            source=self.source,
        )
