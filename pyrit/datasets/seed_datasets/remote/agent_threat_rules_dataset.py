# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


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


# Maps rule IDs in the autoresearch coverage to their ATR taxonomy category.
# This dict reflects the rules currently represented in adversarial-samples.json.
# When ATR extends autoresearch coverage to additional rules, add entries here.
# The ATRCategory enum is the single source of truth for the category strings —
# a typo in either side becomes a static error at import time rather than a
# silent data-quality bug.
_RULE_ID_TO_CATEGORY: dict[str, ATRCategory] = {
    "ATR-2026-00001": ATRCategory.PROMPT_INJECTION,
    "ATR-2026-00002": ATRCategory.PROMPT_INJECTION,
    "ATR-2026-00003": ATRCategory.PROMPT_INJECTION,
    "ATR-2026-00010": ATRCategory.TOOL_POISONING,
    "ATR-2026-00020": ATRCategory.CONTEXT_EXFILTRATION,
    "ATR-2026-00021": ATRCategory.CONTEXT_EXFILTRATION,
    "ATR-2026-00030": ATRCategory.AGENT_MANIPULATION,
    "ATR-2026-00040": ATRCategory.PRIVILEGE_ESCALATION,
    "ATR-2026-00060": ATRCategory.SKILL_COMPROMISE,
    "ATR-2026-00064": ATRCategory.SKILL_COMPROMISE,
}


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

    Reference: [@atr2026].
    License: MIT.

    Each entry is mapped to a SeedPrompt with the payload as ``value``. The
    upstream metadata fields (``original_rule_id``, ``technique``,
    ``detection_field``, ``variation_type``) are preserved on
    ``SeedPrompt.metadata`` so downstream consumers can route, filter, or
    score by them. ``harm_categories`` is set to the rule's ATR category
    (single-element list).

    The optional ``categories``, ``techniques``, ``detection_fields``, and
    ``variation_types`` arguments narrow the dataset client-side after fetch.
    Passing an empty list is rejected — pass ``None`` to disable a filter.
    """

    # Class-attribute metadata picked up by SeedDatasetMetadata. Derived from
    # _RULE_ID_TO_CATEGORY so the categories the loader claims to cover always
    # match the categories it actually produces — same single-source-of-truth
    # rationale as the enum-typed dict above.
    harm_categories: list[str] = sorted({c.value for c in _RULE_ID_TO_CATEGORY.values()})
    modalities: list[str] = ["text"]
    size: str = "large"  # 1,054 seeds
    tags: set[str] = {"safety", "agent_security", "prompt_injection"}

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/"
            "db793f9/data/autoresearch/adversarial-samples.json"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
        categories: list[ATRCategory] | None = None,
        techniques: list[str] | None = None,
        detection_fields: list[ATRDetectionField] | None = None,
        variation_types: list[ATRVariationType] | None = None,
    ) -> None:
        """
        Initialize the ATR dataset loader.

        Args:
            source: URL or local path to ``adversarial-samples.json``. Defaults
                to a pinned commit on the upstream ATR repository for
                reproducibility; pass the raw URL on ``main`` or a different
                tag to track upstream.
            source_type: ``"public_url"`` or ``"file"``.
            categories: Optional non-empty list of ATRCategory values; if
                provided, only payloads whose original rule maps to one of
                these categories are returned. Pass ``None`` (not ``[]``) to
                include all categories.
            techniques: Optional non-empty list of technique strings (free
                text, since the upstream taxonomy of techniques is open-set);
                if provided, only payloads with a matching technique are
                returned. Pass ``None`` (not ``[]``) to include all techniques.
            detection_fields: Optional non-empty list of ATRDetectionField
                values; if provided, only payloads targeting one of these
                surfaces are returned. Pass ``None`` (not ``[]``) to include
                all surfaces.
            variation_types: Optional non-empty list of ATRVariationType
                values; if provided, only payloads of those variation types
                are returned. Pass ``None`` (not ``[]``) to include all
                variation types.

        Raises:
            ValueError: If any filter is an empty list (``[]``), or if
                ``categories``, ``detection_fields``, or ``variation_types``
                contain values that are not instances of their expected enum.
        """
        # Reject empty-list filters — a silent empty filter that returns the
        # full dataset is almost always a caller bug; force the caller to use
        # ``None`` if they want all entries.
        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(categories, ATRCategory, "category")
        if techniques is not None and not techniques:
            raise ValueError("`techniques` must be a non-empty list (pass None to include all techniques)")
        if detection_fields is not None:
            if not detection_fields:
                raise ValueError(
                    "`detection_fields` must be a non-empty list (pass None to include all detection fields)"
                )
            self._validate_enums(detection_fields, ATRDetectionField, "detection_field")
        if variation_types is not None:
            if not variation_types:
                raise ValueError(
                    "`variation_types` must be a non-empty list (pass None to include all variation types)"
                )
            self._validate_enums(variation_types, ATRVariationType, "variation_type")

        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self._categories = {c.value for c in categories} if categories else None
        self._techniques = set(techniques) if techniques else None
        self._detection_fields = {d.value for d in detection_fields} if detection_fields else None
        self._variation_types = {v.value for v in variation_types} if variation_types else None

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "agent_threat_rules"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
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

        authors = ["Kuan-Hsin Lin", "ATR Community"]
        groups = ["ATR Project"]
        source_url = "https://github.com/Agent-Threat-Rule/agent-threat-rules"

        seeds: list[SeedUnion] = []
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

            category_value = category.value

            if self._categories and category_value not in self._categories:
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

            # Per-rule description so downstream consumers reading metadata see
            # the category that actually applies to this seed (rather than a
            # corpus-wide list that ignores the rule's specific family).
            category_label = category_value.replace("-", " ")
            description = (
                f"Agent Threat Rules (ATR) adversarial payload in the {category_label} family. Rule {rule_id}."
            )

            seeds.append(
                SeedPrompt(
                    value=example["payload"],
                    data_type="text",
                    name=rule_id,
                    dataset_name=self.dataset_name,
                    harm_categories=[category_value],
                    description=description,
                    authors=authors,
                    groups=groups,
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
