# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import uuid
from collections.abc import Sequence
from enum import Enum
from typing import Any
from urllib.parse import quote

from typing_extensions import override

from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import ChatMessageRole, Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)

_PINNED_COMMIT = "a298d9c742203b00eaeca58dfca7100c7e2ff360"
_RAW_BASE_URL = f"https://raw.githubusercontent.com/ErxinYu/CoSafe-Dataset/{_PINNED_COMMIT}/"
_DATASET_URL = "https://github.com/ErxinYu/CoSafe-Dataset"
_AUTHORS: list[str] = [
    "Erxin Yu",
    "Jing Li",
    "Ming Liao",
    "Siqi Wang",
    "Gao Zuchen",
    "Fei Mi",
    "Lanqing Hong",
]
_DESCRIPTION = (
    "CoSafe evaluates LLM safety in multi-turn dialogue coreference. Each conversation builds "
    "context over several turns and ends with a question that is only harmful once its "
    "reference to an earlier turn is resolved."
)
_CONVERSATION_ROLES: frozenset[str] = frozenset({"user", "assistant"})


class CoSafeCategory(Enum):
    """
    CoSafe harm categories, one per file in the dataset's ``CoSafe datasets/`` directory.

    Each value is the file name without its ``.json`` extension, exactly as published.
    """

    ANIMAL_ABUSE = "animal_abuse"
    CHILD_ABUSE = "child_abuse"
    CONTROVERSIAL_TOPICS_POLITICS = "controversial_topics,politics"
    DISCRIMINATION_STEREOTYPE_INJUSTICE = "discrimination,stereotype,injustice"
    DRUG_ABUSE_WEAPONS_BANNED_SUBSTANCE = "drug_abuse,weapons,banned_substance"
    FINANCIAL_CRIME_PROPERTY_CRIME_THEFT = "financial_crime,property_crime,theft"
    HATE_SPEECH_OFFENSIVE_LANGUAGE = "hate_speech,offensive_language"
    MISINFORMATION_REGARDING_ETHICS_LAWS_AND_SAFETY = "misinformation_regarding_ethics,laws_and_safety"
    NON_VIOLENT_UNETHICAL_BEHAVIOR = "non_violent_unethical_behavior"
    PRIVACY_VIOLATION = "privacy_violation"
    SELF_HARM = "self_harm"
    SEXUALLY_EXPLICIT_ADULT_CONTENT = "sexually_explicit,adult_content"
    TERRORISM_ORGANIZED_CRIME = "terrorism,organized_crime"
    VIOLENCE_AIDING_AND_ABETTING_INCITEMENT = "violence,aiding_and_abetting,incitement"


class _CoSafeDataset(_RemoteDatasetLoader):
    """
    Loader for CoSafe, 1,400 multi-turn conversations whose final harmful question refers
    back to earlier turns instead of naming the harmful thing directly.

    The dataset has 14 harm categories with 100 conversations each. Every conversation
    becomes one ``SeedGroup``: one ``SeedPrompt`` per message, with its ``role`` and an
    increasing ``sequence``, all sharing a ``prompt_group_id``. Each conversation ends on
    the ``user`` question that carries the coreference attack; a trailing ``assistant``
    message after that question (one conversation in the published data has one) is
    dropped so the group ends on the question to send.

    The single-turn files in the dataset's ``Single Prompt/`` directory are not loaded.

    Reference: [@yu2024cosafe]

    Source: https://github.com/ErxinYu/CoSafe-Dataset, pinned to a known commit. The
    repository does not state a licence. The files are fetched at run time and no data is
    stored in PyRIT.

    Note: The multi-turn files use a ``.json`` extension but contain JSON Lines (one
    conversation per line), so this loader reads them as JSON Lines.

    Warning: These conversations are written to elicit harmful responses and may contain
    offensive content.
    """

    source_url = _DATASET_URL

    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    harm_categories: list[str] = [category.value for category in CoSafeCategory]
    size: str = "large"
    tags: frozenset[str] = frozenset({"safety", "jailbreak", "multiturn"})

    def __init__(
        self,
        *,
        categories: Sequence[CoSafeCategory] | None = None,
    ) -> None:
        """
        Initialize the CoSafe dataset loader.

        Args:
            categories: Harm categories to load. ``None`` loads all 14 categories.

        Raises:
            ValueError: If ``categories`` is empty or contains a non-enum value.
        """
        if categories is not None:
            self._validate_enums(categories, CoSafeCategory, "category")
            if len(categories) == 0:
                raise ValueError("categories must contain at least one CoSafeCategory.")
            categories = tuple(dict.fromkeys(categories))
        self._categories: tuple[CoSafeCategory, ...] = tuple(categories) if categories else tuple(CoSafeCategory)

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "cosafe"

    @override
    def _get_file_type(self, *, source: str) -> str:
        """
        Read the multi-turn ``.json`` files as JSON Lines, which is what they contain.

        Args:
            source: The URL or local file path.

        Returns:
            str: ``jsonl`` for ``.json`` sources, otherwise the inferred file type.
        """
        file_type = super()._get_file_type(source=source)
        return "jsonl" if file_type == "json" else file_type

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the selected CoSafe categories and return them as a SeedDataset.

        Args:
            cache: Whether to cache each downloaded file. Defaults to True.

        Returns:
            SeedDataset: One group of role-bearing prompts per conversation.

        Raises:
            ValueError: If a conversation is malformed or the dataset is empty.
        """
        file_conversations = await gather_with_cleanup_async(
            [self._fetch_category_async(category=category, cache=cache) for category in self._categories]
        )

        seeds: list[SeedUnion] = []
        for category, conversations in zip(self._categories, file_conversations, strict=True):
            for index, conversation in enumerate(conversations):
                seeds.extend(self._conversation_to_seeds(category=category, index=index, conversation=conversation))

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info("Loaded %s prompts from CoSafe", len(seeds))
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    async def _fetch_category_async(self, *, category: CoSafeCategory, cache: bool) -> list[Any]:
        """
        Download one category file.

        Args:
            category: The category whose file to download.
            cache: Whether to cache the download.

        Returns:
            list[Any]: One list of messages per conversation.
        """
        source = _RAW_BASE_URL + quote(f"CoSafe datasets/{category.value}.json", safe="/,")
        return await asyncio.to_thread(self._fetch_from_url, source=source, source_type="public_url", cache=cache)

    def _conversation_to_seeds(self, *, category: CoSafeCategory, index: int, conversation: Any) -> list[SeedPrompt]:
        """
        Convert one conversation into role-bearing prompts that share a group.

        Args:
            category: The category file the conversation came from.
            index: The conversation's position in that file.
            conversation: The list of ``{"role": ..., "content": ...}`` messages.

        Returns:
            list[SeedPrompt]: The conversation's prompts in order.

        Raises:
            ValueError: If the conversation is not a list of user and assistant messages or does
                not contain a user message.
        """
        if not isinstance(conversation, list):
            raise ValueError(f"CoSafe {category.value} conversation {index} is not a list of messages.")
        messages = list(conversation)
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in _CONVERSATION_ROLES
                or not isinstance(message.get("content"), str)
                or not message["content"]
            ):
                raise ValueError(
                    f"CoSafe {category.value} conversation {index} has a message without a user or assistant "
                    "role and non-empty content."
                )
        while messages and messages[-1]["role"] == "assistant":
            messages.pop()
        if not messages:
            raise ValueError(f"CoSafe {category.value} conversation {index} has no user message.")

        group_id = uuid.uuid4()
        metadata = {"category": category.value, "conversation_index": index}
        return [
            SeedPrompt(
                value=message["content"],
                data_type="text",
                role=self._role(message["role"]),
                sequence=sequence,
                dataset_name=self.dataset_name,
                harm_categories=[category.value],
                description=_DESCRIPTION,
                authors=_AUTHORS,
                source=_DATASET_URL,
                prompt_group_id=group_id,
                metadata=dict(metadata),
            )
            for sequence, message in enumerate(messages)
        ]

    @staticmethod
    def _role(role: str) -> ChatMessageRole:
        """
        Narrow a validated message role to a chat role.

        Args:
            role: ``user`` or ``assistant``.

        Returns:
            ChatMessageRole: The same role.
        """
        return "user" if role == "user" else "assistant"
