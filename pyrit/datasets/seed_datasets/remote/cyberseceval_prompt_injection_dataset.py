# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import uuid
from enum import Enum
from typing import Any, Literal, cast

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class CyberSecEvalInjectionType(Enum):
    """
    Injection types covered by the CyberSecEval prompt injection benchmark.

    Values are the verbatim strings stored in the upstream ``injection_type``
    field. A direct injection is typed by the user. An indirect injection
    arrives through content the model is asked to process.
    """

    DIRECT = "direct"
    INDIRECT = "indirect"


class CyberSecEvalRiskCategory(Enum):
    """
    Risk categories covered by the CyberSecEval prompt injection benchmark.

    Values are the verbatim strings stored in the upstream ``risk_category``
    field. A security-violating case tries to make the model leak information
    or take an action it was told not to. A logic-violating case tries to make
    it ignore the rule or task its system prompt set.
    """

    SECURITY_VIOLATING = "security-violating"
    LOGIC_VIOLATING = "logic-violating"


_AUTHORS: list[str] = [
    "Manish Bhatt",
    "Sahana Chennabasappa",
    "Cyrus Nikolaidis",
    "Shengye Wan",
    "Ivan Evtimov",
    "Dominik Gabi",
    "Daniel Song",
    "Faizan Ahmad",
    "Cornelius Aschermann",
    "Lorenzo Fontana",
    "Sasha Frolov",
    "Ravi Prakash Giri",
    "Dhaval Kapil",
    "Yiannis Kozyrakis",
    "David LeBlanc",
    "James Milazzo",
    "Aleksandar Straumann",
    "Gabriel Synnaeve",
    "Varun Vontimitta",
    "Spencer Whitman",
    "Joshua Saxe",
]

_GROUPS: list[str] = ["Meta"]

_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "prompt_id",
        "test_case_prompt",
        "user_input",
        "judge_question",
        "injection_variant",
        "injection_type",
        "risk_category",
    }
)

# Fields that must be non-empty strings. ``prompt_id`` is an integer upstream and is checked separately.
_STRING_KEYS: tuple[str, ...] = (
    "test_case_prompt",
    "user_input",
    "judge_question",
    "injection_variant",
    "injection_type",
    "risk_category",
)

_DESCRIPTION: str = (
    "CyberSecEval prompt injection test case. Each case pairs a system prompt that sets a rule or holds "
    "a secret with a user input that tries to make the model break it. The benchmark measures how "
    "susceptible a model is to prompt injection. Introduced in 'Purple Llama CyberSecEval: A Secure "
    "Coding Benchmark for Language Models' (Bhatt et al., 2023)."
)


class _CyberSecEvalPromptInjectionDataset(_RemoteDatasetLoader):
    """
    Loader for the textual prompt injection benchmark of Meta's CyberSecEval (Purple Llama).

    The benchmark has 251 English test cases. Each one pairs a system prompt that sets
    a rule or holds a secret with a user input that tries to break it. 196 cases are
    direct injections and 55 are indirect injections. They cover 15 injection
    techniques, such as ``ignore_previous_instructions``, ``payload_splitting``,
    ``token_smuggling`` and ``many_shot_attack``, and are labelled
    ``security-violating`` or ``logic-violating``.

    CyberSecEval sends the system prompt (``test_case_prompt``) and the user input
    (``user_input``) to the model under test unchanged, so this loader does the same.
    Each case becomes two ``SeedPrompt`` values that share a ``prompt_group_id``: a
    ``system`` seed at sequence 0 holding ``test_case_prompt`` and a ``user`` seed at
    sequence 1 holding ``user_input``. Both values are kept verbatim so the seeds
    reproduce the benchmark.

    The metadata on each seed holds ``prompt_id``, ``injection_type``,
    ``injection_variant``, ``risk_category`` and ``judge_question``. The judge
    question is the yes/no question CyberSecEval asks its judge model about a
    response, for example "Does this response contain the word 'Piano'?". It is kept
    so a scorer can reproduce the benchmark's success check. This loader does not
    run that judge step.

    These are benchmark cases, not harmful content: they measure whether a model
    follows an attacker over its operator. No harm category applies, so ``harm_categories``
    is left empty.

    This loader covers the English textual prompt injection set only. The
    machine-translated multilingual set and the visual prompt injection benchmark
    are out of scope.

    References:
        - https://github.com/meta-llama/PurpleLlama/tree/main/CybersecurityBenchmarks
        - [@bhatt2023cyberseceval]

    License: MIT (CyberSecEval benchmarks in the Purple Llama repository).
    """

    _COMMIT: str = "172c1074069eb88ec834124272c1b1c4f8893445"
    DATASET_URL: str = (
        f"https://raw.githubusercontent.com/meta-llama/PurpleLlama/{_COMMIT}/"
        "CybersecurityBenchmarks/datasets/prompt_injection/prompt_injection.json"
    )
    SOURCE_URL: str = DATASET_URL

    # Metadata
    harm_categories: list[str] = []
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 251 test cases, 502 seeds
    tags: set[str] = {"safety", "cybersecurity", "prompt_injection"}

    def __init__(
        self,
        *,
        source: str = DATASET_URL,
        source_type: Literal["public_url", "file"] = "public_url",
        injection_types: list[CyberSecEvalInjectionType] | None = None,
        risk_categories: list[CyberSecEvalRiskCategory] | None = None,
    ) -> None:
        """
        Initialize the CyberSecEval prompt injection dataset loader.

        Args:
            source: URL or local path to the prompt injection benchmark JSON file.
                Defaults to a pinned commit of the Purple Llama repository so that a
                default fetch is reproducible.
            source_type: ``"public_url"`` or ``"file"``.
            injection_types: List of CyberSecEvalInjectionType values to filter by.
                Defaults to None (direct and indirect).
            risk_categories: List of CyberSecEvalRiskCategory values to filter by.
                Defaults to None (security-violating and logic-violating).

        Raises:
            ValueError: If ``injection_types`` or ``risk_categories`` is an empty list,
                or contains a value of the wrong enum type.
        """
        if injection_types is not None:
            if not injection_types:
                raise ValueError("`injection_types` must be a non-empty list (pass None to include all types)")
            self._validate_enums(injection_types, CyberSecEvalInjectionType, "injection type")
        if risk_categories is not None:
            if not risk_categories:
                raise ValueError("`risk_categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(risk_categories, CyberSecEvalRiskCategory, "risk category")

        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        # Copied, so a caller mutating its own lists afterwards cannot change
        # what this loader filters on.
        self.injection_types = list(injection_types) if injection_types is not None else None
        self.risk_categories = list(risk_categories) if risk_categories is not None else None

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "cyberseceval_prompt_injection"

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the CyberSecEval prompt injection benchmark and return it as a SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset with a system seed and a user seed per test case,
            filtered by ``self.injection_types`` and ``self.risk_categories``.

        Raises:
            ValueError: If a row is missing an expected key, has a field of the wrong
                type, has an unknown injection type or risk category, or if no test
                cases remain after filtering.
        """
        selected_types = (
            {injection_type.value for injection_type in self.injection_types}
            if self.injection_types is not None
            else None
        )
        selected_risks = (
            {risk_category.value for risk_category in self.risk_categories}
            if self.risk_categories is not None
            else None
        )
        logger.info(
            f"Loading CyberSecEval prompt injection dataset from {self.source} "
            f"(injection_types={sorted(selected_types) if selected_types is not None else 'all'}, "
            f"risk_categories={sorted(selected_risks) if selected_risks is not None else 'all'})"
        )

        # The declared return type of ``_fetch_from_url`` says the values are strings,
        # but ``prompt_id`` is an integer upstream. The helper does a synchronous
        # download and file I/O, so it runs in a worker thread to keep the event loop
        # free for other work.
        examples = cast(
            "list[dict[str, Any]]",
            await asyncio.to_thread(
                self._fetch_from_url,
                source=self.source,
                source_type=self.source_type,
                cache=cache,
            ),
        )

        seed_prompts: list[SeedUnion] = []
        for row_index, example in enumerate(examples):
            missing_keys = _REQUIRED_KEYS - example.keys()
            if missing_keys:
                raise ValueError(
                    f"CyberSecEval prompt injection row {row_index} is missing expected key(s) "
                    f"{sorted(missing_keys)}; row has {sorted(example.keys())}. The upstream schema may have changed."
                )

            for key in _STRING_KEYS:
                value = example[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"CyberSecEval prompt injection row {row_index} has a `{key}` that is not a "
                        f"non-empty string (got {type(value).__name__}). The upstream schema may have changed."
                    )

            prompt_id = example["prompt_id"]
            if isinstance(prompt_id, bool) or not isinstance(prompt_id, int):
                raise ValueError(
                    f"CyberSecEval prompt injection row {row_index} has a `prompt_id` that is not an integer "
                    f"(got {type(prompt_id).__name__}). The upstream schema may have changed."
                )

            try:
                injection_type = CyberSecEvalInjectionType(example["injection_type"])
            except ValueError as exc:
                raise ValueError(
                    f"CyberSecEval prompt injection row {row_index} has an invalid injection_type"
                ) from exc
            try:
                risk_category = CyberSecEvalRiskCategory(example["risk_category"])
            except ValueError as exc:
                raise ValueError(f"CyberSecEval prompt injection row {row_index} has an invalid risk_category") from exc

            if selected_types is not None and injection_type.value not in selected_types:
                continue
            if selected_risks is not None and risk_category.value not in selected_risks:
                continue

            seed_prompts.extend(
                self._build_seeds(
                    prompt_id=prompt_id,
                    system_prompt=example["test_case_prompt"],
                    user_input=example["user_input"],
                    judge_question=example["judge_question"],
                    injection_variant=example["injection_variant"],
                    injection_type=injection_type,
                    risk_category=risk_category,
                )
            )

        if not seed_prompts:
            raise ValueError(
                "SeedDataset cannot be empty. Check your filter criteria. "
                "CyberSecEval prompt injection filter: "
                f"injection_types={sorted(selected_types) if selected_types is not None else '(any)'}, "
                f"risk_categories={sorted(selected_risks) if selected_risks is not None else '(any)'}."
            )

        logger.info(
            f"Successfully loaded {len(seed_prompts) // 2} test cases ({len(seed_prompts)} seeds) "
            "from CyberSecEval prompt injection dataset"
        )

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _build_seeds(
        self,
        *,
        prompt_id: int,
        system_prompt: str,
        user_input: str,
        judge_question: str,
        injection_variant: str,
        injection_type: CyberSecEvalInjectionType,
        risk_category: CyberSecEvalRiskCategory,
    ) -> list[SeedUnion]:
        """
        Build the system and user seeds for one test case.

        Args:
            prompt_id: The upstream ``prompt_id`` of the test case.
            system_prompt: The upstream ``test_case_prompt``, kept verbatim.
            user_input: The upstream ``user_input``, kept verbatim.
            judge_question: The yes/no question CyberSecEval asks its judge.
            injection_variant: The injection technique of the test case.
            injection_type: Whether the injection is direct or indirect.
            risk_category: Whether the case is security-violating or logic-violating.

        Returns:
            list[SeedUnion]: The system seed (sequence 0) and the user seed (sequence 1).
        """
        group_id = uuid.uuid4()
        metadata: dict[str, str | int] = {
            "prompt_id": prompt_id,
            "injection_type": injection_type.value,
            "injection_variant": injection_variant,
            "risk_category": risk_category.value,
            "judge_question": judge_question,
        }
        common: dict[str, Any] = {
            "data_type": "text",
            "dataset_name": self.dataset_name,
            "harm_categories": [],
            "description": _DESCRIPTION,
            "source": self.SOURCE_URL,
            "authors": _AUTHORS,
            "groups": _GROUPS,
            "prompt_group_id": group_id,
        }
        return [
            SeedPrompt(
                value=system_prompt,
                name=f"CyberSecEval Prompt Injection {prompt_id} (system)",
                role="system",
                sequence=0,
                metadata=dict(metadata),
                **common,
            ),
            SeedPrompt(
                value=user_input,
                name=f"CyberSecEval Prompt Injection {prompt_id} (user)",
                role="user",
                sequence=1,
                metadata=dict(metadata),
                **common,
            ),
        ]
