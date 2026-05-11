# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Literal, Optional

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


# Maps rule IDs in the autoresearch coverage to their ATR taxonomy category.
# This dict reflects the rules currently represented in adversarial-samples.json.
# When ATR extends autoresearch coverage to additional rules, add entries here.
# Source of truth for the mapping is the rules/<category>/<rule-id>*.yaml layout
# in the upstream ATR repository.
_RULE_ID_TO_CATEGORY: dict[str, str] = {
    "ATR-2026-00001": "prompt-injection",
    "ATR-2026-00002": "prompt-injection",
    "ATR-2026-00003": "prompt-injection",
    "ATR-2026-00010": "tool-poisoning",
    "ATR-2026-00020": "context-exfiltration",
    "ATR-2026-00021": "context-exfiltration",
    "ATR-2026-00030": "agent-manipulation",
    "ATR-2026-00040": "privilege-escalation",
    "ATR-2026-00060": "skill-compromise",
    "ATR-2026-00064": "skill-compromise",
}


class ATRCategory(Enum):
    """
    ATR taxonomy categories.

    Reflects the full ATR rule taxonomy (ten categories). The autoresearch
    payload corpus currently covers six of these; filtering by an uncovered
    category returns an empty dataset.
    """

    PROMPT_INJECTION = "prompt-injection"
    TOOL_POISONING = "tool-poisoning"
    CONTEXT_EXFILTRATION = "context-exfiltration"
    AGENT_MANIPULATION = "agent-manipulation"
    PRIVILEGE_ESCALATION = "privilege-escalation"
    SKILL_COMPROMISE = "skill-compromise"
    DATA_POISONING = "data-poisoning"
    EXCESSIVE_AUTONOMY = "excessive-autonomy"
    MODEL_ABUSE = "model-abuse"
    MODEL_SECURITY = "model-security"


class ATRDetectionField(Enum):
    """
    Agent surface that an ATR payload targets.

    Each entry in adversarial-samples.json carries a ``detection_field`` value
    indicating which agent input or output channel the payload is intended to
    appear on. Useful for narrowing the dataset to the surface the user is
    actually testing.
    """

    USER_INPUT = "user_input"
    CONTENT = "content"
    TOOL_ARGS = "tool_args"
    TOOL_NAME = "tool_name"
    TOOL_RESPONSE = "tool_response"
    AGENT_OUTPUT = "agent_output"


class ATRVariationType(Enum):
    """
    Variation type label for an ATR payload.

    Indicates whether a payload is an original seed entry or an
    autoresearch-derived variant.
    """

    ORIGINAL = "original"
    GENERATED = "generated"


# Default upstream URL pinned to a specific commit for reproducibility, mirroring
# the HarmBench loader convention.
_DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/"
    "db793f9/data/autoresearch/adversarial-samples.json"
)


class _AgentThreatRulesDataset(_RemoteDatasetLoader):
    """
    Loader for the Agent Threat Rules (ATR) adversarial payload corpus.

    ATR is an open MIT-licensed detection standard for AI agent threats. The
    upstream catalog ships rules across ten attack categories (prompt-injection,
    tool-poisoning, skill-compromise, agent-manipulation, context-exfiltration,
    data-poisoning, excessive-autonomy, model-abuse, model-security,
    privilege-escalation) and 336 rules at the time of this loader's pin.

    This loader surfaces the autoresearch adversarial-payload corpus
    (``data/autoresearch/adversarial-samples.json``), which contains 1,054
    attack-prompt entries across ten base rule scenarios in six of the ten
    categories. Each entry carries an attack technique label (paraphrase,
    language_switch, encoding, role_play, and 17 others) and the agent surface
    the payload targets (``user_input``, ``content``, ``tool_args``,
    ``tool_name``, ``tool_response``, ``agent_output``).

    Reference: https://github.com/Agent-Threat-Rule/agent-threat-rules
    License: MIT.

    Each entry is mapped to a SeedPrompt with the payload as ``value``. The
    upstream metadata fields (``original_rule_id``, ``technique``,
    ``detection_field``, ``variation_type``) are preserved on
    ``SeedPrompt.metadata`` so downstream consumers can route, filter, or
    score by them. ``harm_categories`` is set to the rule's ATR category
    (single-element list).

    The optional ``categories``, ``techniques``, ``detection_fields``, and
    ``variation_types`` arguments narrow the dataset client-side after fetch.
    """

    # Class-attribute metadata picked up by SeedDatasetMetadata
    harm_categories: list[str] = [
        "prompt-injection",
        "tool-poisoning",
        "context-exfiltration",
        "agent-manipulation",
        "privilege-escalation",
        "skill-compromise",
    ]
    modalities: list[str] = ["text"]
    size: str = "large"  # 1,054 seeds
    tags: set[str] = {"safety", "agent_security", "prompt_injection"}

    def __init__(
        self,
        *,
        source: str = _DEFAULT_SOURCE,
        source_type: Literal["public_url", "file"] = "public_url",
        categories: Optional[list[ATRCategory]] = None,
        techniques: Optional[list[str]] = None,
        detection_fields: Optional[list[ATRDetectionField]] = None,
        variation_types: Optional[list[ATRVariationType]] = None,
    ) -> None:
        """
        Initialize the ATR dataset loader.

        Args:
            source: URL or local path to ``adversarial-samples.json``. Defaults
                to a pinned commit on the upstream ATR repository for
                reproducibility; pass the raw URL on ``main`` or a different
                tag to track upstream.
            source_type: ``"public_url"`` or ``"file"``.
            categories: Optional list of ATRCategory values; if provided, only
                payloads whose original rule maps to one of these categories
                are returned.
            techniques: Optional list of technique strings (free text, since
                the upstream taxonomy of techniques is open-set); if provided,
                only payloads with a matching technique are returned.
            detection_fields: Optional list of ATRDetectionField values; if
                provided, only payloads targeting one of these surfaces are
                returned.
            variation_types: Optional list of ATRVariationType values; if
                provided, only payloads of those variation types are returned.

        Raises:
            ValueError: If ``categories``, ``detection_fields``, or
                ``variation_types`` contain values that are not instances of
                their expected enum class.
        """
        if categories is not None:
            self._validate_enums(categories, ATRCategory, "category")
        if detection_fields is not None:
            self._validate_enums(detection_fields, ATRDetectionField, "detection_field")
        if variation_types is not None:
            self._validate_enums(variation_types, ATRVariationType, "variation_type")

        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self._categories = {c.value for c in categories} if categories else None
        self._techniques = set(techniques) if techniques else None
        self._detection_fields = {d.value for d in detection_fields} if detection_fields else None
        self._variation_types = {v.value for v in variation_types} if variation_types else None

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "agent_threat_rules"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the ATR adversarial payload corpus and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing one SeedPrompt per matching
            ATR payload entry.

        Raises:
            ValueError: If any entry is missing a required field.
        """
        required_keys = {
            "id",
            "original_rule_id",
            "technique",
            "payload",
            "detection_field",
            "variation_type",
        }

        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        description = (
            "Agent Threat Rules (ATR) adversarial payload corpus from the "
            "autoresearch dataset. Attack payloads spanning prompt injection, "
            "tool poisoning, context exfiltration, agent manipulation, "
            "privilege escalation, and skill compromise."
        )
        authors = ["ATR Community"]
        source_url = "https://github.com/Agent-Threat-Rule/agent-threat-rules"

        seeds: list[SeedPrompt] = []
        skipped_unknown_rule = 0

        for example in examples:
            missing = required_keys - example.keys()
            if missing:
                raise ValueError(f"Missing keys in ATR entry: {', '.join(sorted(missing))}")

            rule_id = example["original_rule_id"]
            category = _RULE_ID_TO_CATEGORY.get(rule_id)
            if category is None:
                # Unknown rule — likely a new rule_id that landed upstream
                # before the loader's mapping was extended. Skip rather than
                # mislabel; warn in aggregate at end.
                skipped_unknown_rule += 1
                continue

            if self._categories and category not in self._categories:
                continue
            if self._techniques and example["technique"] not in self._techniques:
                continue
            if self._detection_fields and example["detection_field"] not in self._detection_fields:
                continue
            if self._variation_types and example["variation_type"] not in self._variation_types:
                continue

            metadata: dict[str, str | int] = {
                "original_rule_id": rule_id,
                "technique": example["technique"],
                "detection_field": example["detection_field"],
                "variation_type": example["variation_type"],
                "atr_id": example["id"],
            }

            seeds.append(
                SeedPrompt(
                    value=example["payload"],
                    data_type="text",
                    name=rule_id,
                    dataset_name=self.dataset_name,
                    harm_categories=[category],
                    description=description,
                    authors=authors,
                    source=source_url,
                    metadata=metadata,
                )
            )

        if skipped_unknown_rule:
            logger.warning(
                "Skipped %d ATR payload(s) whose original_rule_id is not in the "
                "loader's category mapping. Update _RULE_ID_TO_CATEGORY in "
                "agent_threat_rules_dataset.py to extend coverage.",
                skipped_unknown_rule,
            )

        logger.info(
            "Successfully loaded %d ATR adversarial payloads (from %d total upstream entries)",
            len(seeds),
            len(examples),
        )

        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
