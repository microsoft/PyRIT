# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Any

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class AgenticPromptInjectionBoundaryLabel(Enum):
    """Filter records by the published security decision label."""

    BENIGN = 0
    ATTACK = 1
    ALL = "all"


class AgenticPromptInjectionBoundarySplit(Enum):
    """Select a published split or combine the complete dataset."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    ALL = "all"


class AgenticPromptInjectionBoundaryAttackFamily(Enum):
    """Prompt-injection families represented by the controlled pairs."""

    DIRECT_INSTRUCTION_OVERRIDE = "direct_instruction_override"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    ROLEPLAY_JAILBREAK = "roleplay_jailbreak"
    AUTHORITY_CLAIM_BYPASS = "authority_claim_bypass"
    SENSITIVE_DATA_EXFILTRATION = "sensitive_data_exfiltration"
    TOOL_ACTION_ABUSE = "tool_action_abuse"
    INDIRECT_CONTENT_INJECTION = "indirect_content_injection"
    RAG_CONTEXT_POISONING = "rag_context_poisoning"
    MEMORY_CONTEXT_POISONING = "memory_context_poisoning"
    OBFUSCATION_ENCODING = "obfuscation_encoding"
    MULTI_AGENT_TRUST_SPOOFING = "multi_agent_trust_spoofing"
    APPROVAL_WORKFLOW_BYPASS = "approval_workflow_bypass"


class AgenticPromptInjectionBoundarySourceContext(Enum):
    """Input surfaces through which a record reaches an LLM or agent."""

    AGENT_MESSAGE = "agent_message"
    AGENT_TOOL_REQUEST = "agent_tool_request"
    CALENDAR_EVENT = "calendar_event"
    CHAT_MESSAGE = "chat_message"
    CODE_COMMENT = "code_comment"
    CONVERSATION_MEMORY = "conversation_memory"
    DIRECT_USER = "direct_user"
    DOCUMENT = "document"
    EMAIL = "email"
    IMAGE_OCR = "image_ocr"
    ISSUE_DESCRIPTION = "issue_description"
    MCP_TOOL_RESPONSE = "mcp_tool_response"
    PDF_DOCUMENT = "pdf_document"
    RETRIEVED_DOCUMENT = "retrieved_document"
    RUNBOOK = "runbook"
    SPREADSHEET_CELL = "spreadsheet_cell"
    SUPPORT_TICKET = "support_ticket"
    TOOL_OUTPUT = "tool_output"
    WEB_PAGE = "web_page"


class _AgenticPromptInjectionBoundaryDataset(_RemoteDatasetLoader):
    """
    Loader for Agentic Prompt-Injection Boundary Pairs.

    The dataset contains 1,200 English records arranged as 600 controlled
    benign/attack pairs. Each pair holds the workflow, asset, role, tool, and
    topic constant while changing whether the request crosses an instruction,
    authorization, confidentiality, trust, tool-use, or approval boundary.
    The 50 scenarios are isolated across train, validation, and test splits.

    Attack records are returned by default for red-team use. Set ``label`` to
    ``ALL`` to reconstruct the controlled pairs or ``BENIGN`` to load the hard
    negative surface. Family filters use ``pair_family`` rather than
    ``attack_family`` so benign rows remain paired with their corresponding
    attacks.

    Reference: [@deniz2026agenticboundarypairs].
    License: CC BY 4.0.
    """

    HF_DATASET_NAME: str = "3nesdeniz/agentic-prompt-injection-boundary-pairs"
    harm_categories: list[str] = ["benign_boundary", "prompt_injection"]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 1,200 records
    tags: frozenset[str] = frozenset({"safety", "agent_security", "prompt_injection", "refusal", "synthetic"})

    def __init__(
        self,
        *,
        label: AgenticPromptInjectionBoundaryLabel = AgenticPromptInjectionBoundaryLabel.ATTACK,
        split: AgenticPromptInjectionBoundarySplit = AgenticPromptInjectionBoundarySplit.ALL,
        attack_families: list[AgenticPromptInjectionBoundaryAttackFamily] | None = None,
        source_contexts: list[AgenticPromptInjectionBoundarySourceContext] | None = None,
    ) -> None:
        """
        Initialize the Agentic Prompt-Injection Boundary Pairs loader.

        Args:
            label: Security decision label to load. Defaults to attack records.
            split: Published split to load, or all splits. Defaults to all.
            attack_families: Optional non-empty list of paired attack families.
            source_contexts: Optional non-empty list of input surfaces.

        Raises:
            ValueError: If an enum value is invalid or a filter list is empty.
        """
        self._validate_enum(label, AgenticPromptInjectionBoundaryLabel, "label")
        self._validate_enum(split, AgenticPromptInjectionBoundarySplit, "split")

        if attack_families is not None:
            if not attack_families:
                raise ValueError("`attack_families` must be a non-empty list (pass None to include all families)")
            self._validate_enums(
                attack_families,
                AgenticPromptInjectionBoundaryAttackFamily,
                "attack_family",
            )

        if source_contexts is not None:
            if not source_contexts:
                raise ValueError("`source_contexts` must be a non-empty list (pass None to include all contexts)")
            self._validate_enums(
                source_contexts,
                AgenticPromptInjectionBoundarySourceContext,
                "source_context",
            )

        self._label = label
        self._split = split
        self._attack_families = {family.value for family in attack_families} if attack_families else None
        self._source_contexts = {context.value for context in source_contexts} if source_contexts else None

    @property
    @override
    def dataset_name(self) -> str:
        """The registered dataset name."""
        return "agentic_prompt_injection_boundary_pairs"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the selected records from Hugging Face.

        Args:
            cache: Whether Hugging Face may reuse its local cache.

        Returns:
            SeedDataset: Literal prompts with pair and boundary metadata.

        Raises:
            ValueError: If a row is malformed, split metadata conflicts with
                the requested split, or filters produce no records.
        """
        split_names = (
            ["train", "validation", "test"]
            if self._split is AgenticPromptInjectionBoundarySplit.ALL
            else [self._split.value]
        )

        rows: list[dict[str, Any]] = []
        for split_name in split_names:
            split_rows = await self._fetch_from_huggingface_async(
                dataset_name=self.HF_DATASET_NAME,
                config="default",
                split=split_name,
                cache=cache,
            )
            rows.extend(dict(row) for row in split_rows)

        required_keys = {
            "id",
            "pair_id",
            "scenario_id",
            "text",
            "label",
            "category",
            "attack_family",
            "pair_family",
            "source_context",
            "risk_domain",
            "target_boundary",
            "expected_action",
            "source_type",
            "language",
            "split",
        }

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        authors = ["Enes Deniz"]
        groups = ["AltaySec"]
        seeds: list[SeedUnion] = []

        for row in rows:
            missing = required_keys - row.keys()
            if missing:
                raise ValueError(f"Missing keys in Agentic Boundary Pairs entry: {', '.join(sorted(missing))}")

            row_split = str(row["split"])
            if self._split is not AgenticPromptInjectionBoundarySplit.ALL and row_split != self._split.value:
                raise ValueError(
                    f"Dataset row split '{row_split}' does not match requested split '{self._split.value}'"
                )

            row_label = int(row["label"])
            if row_label not in {0, 1}:
                raise ValueError(f"Invalid label in Agentic Boundary Pairs entry: {row_label}")
            if self._label is not AgenticPromptInjectionBoundaryLabel.ALL and row_label != self._label.value:
                continue

            pair_family = str(row["pair_family"])
            if self._attack_families and pair_family not in self._attack_families:
                continue

            source_context = str(row["source_context"])
            if self._source_contexts and source_context not in self._source_contexts:
                continue

            category = str(row["category"])
            description_prefix = "Prompt-injection" if row_label == 1 else "Paired legitimate"
            description = (
                f"{description_prefix} boundary case for {pair_family.replace('_', ' ')} "
                f"delivered through {source_context.replace('_', ' ')}."
            )

            metadata = {key: row[key] for key in sorted(required_keys - {"text"})}
            seeds.append(
                SeedPrompt(
                    value=str(row["text"]),
                    name=str(row["id"]),
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=[category],
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata=metadata,
                    prompt_group_alias=str(row["pair_id"]),
                )
            )

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Loaded {len(seeds)} records from {self.dataset_name}")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
