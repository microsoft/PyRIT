# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
System-prompt collections from the garak ``garak-llm`` HuggingFace org.

garak's ``sysprompt_extraction`` probe uses libraries of real system prompts as
extraction targets. These loaders expose those libraries as ``SeedDataset``s
where each system prompt is a ``SeedPrompt`` with ``role="system"``. Per-row
metadata (agent name, ids, flags) is preserved in ``SeedPrompt.metadata``.

Reference: [@derczynski2024garak]
"""

from typing import ClassVar

from pyrit.datasets.seed_datasets.remote.garak_dataset import _GarakRemoteDataset
from pyrit.models import ChatMessageRole, Modality


class _GarakSystemPromptDataset(_GarakRemoteDataset):
    """Base for garak system-prompt libraries (seeds use ``role="system"``)."""

    ROLE: ClassVar[ChatMessageRole | None] = "system"

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    tags: frozenset[str] = frozenset({"system_prompt"})


class _GarakDrhSystemPromptDataset(_GarakSystemPromptDataset):
    """
    garak processed system-prompt library (credit: danielrosehill/System-Prompt-Library).

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/drh-System-Prompt-processed"
    _DATASET_NAME: ClassVar[str] = "garak_drh_system_prompts"
    TEXT_COLUMN: ClassVar[str] = "systemprompt"
    METADATA_COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {
        "agentname": ("agentname",),
        "creation_date": ("creation_date",),
        "is_agent": ("is-agent",),
        "is_single_turn": ("is-single-turn",),
    }
    size: str = "large"  # ~944 system prompts


class _GarakTmSystemPromptDataset(_GarakSystemPromptDataset):
    """
    garak system-prompt library (credit: teilomillet/system_prompt).

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/tm-system_prompt"
    _DATASET_NAME: ClassVar[str] = "garak_tm_system_prompts"
    TEXT_COLUMN: ClassVar[str] = "prompt"
    METADATA_COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {
        "source_id": ("id",),
    }
    size: str = "small"  # ~69 system prompts
