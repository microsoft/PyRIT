# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# type: ignore[misc, override, arg-type, union-attr, return-value]

"""Harm category taxonomy and standardization utilities for seed classification."""

import logging
import os
from collections.abc import Mapping
from enum import Enum
from typing import cast

import yaml

logger = logging.getLogger(__name__)

try:
    from enum import StrEnum  # type: ignore[attr-defined]
except ImportError:  # Python 3.10 and below

    class StrEnum(str, Enum):  # type: ignore[misc]
        """Minimal backport of enum.StrEnum for Python < 3.11."""


_HARM_CATEGORY_ALIASES: dict[str, object] = {}
HARM_CATEGORY_TAXONOMY_VERSION = "v1.0.0"

with open(os.path.join(os.path.dirname(__file__), "harm_category_definitions.yaml")) as f:
    _HARM_CATEGORY_DEFINITIONS: dict[str, str] = yaml.safe_load(f).get("definitions", {})


class HarmCategory(StrEnum):
    """Standardized harm category taxonomy for seed classification and attack evaluation."""

    HATESPEECH = "Hate Speech"
    HARASSMENT = "Harassment"
    SEXUALIZATION = "Sexualization"
    GROOMING = "Grooming"
    CHILD_LEAKAGE = "Child Leakage"
    DANGEROUS_SITUATIONS = "Dangerous Situations"
    VIOLENT_CONTENT = "Graphic Violence and Gore"
    SEXUAL_CONTENT = "Pornography & Sexual Content"
    PROFANITY = "Profanity"
    QUALITY_OF_SERVICE = "Quality of Service (QoS)"
    ALLOCATION = "Allocation of Resources & Opportunities"
    REPRESENTATIONAL = "Representational Harms (Stereotyping, Demeaning & Erasing Outputs)"
    SUICIDE = "Suicide"
    SELF_HARM = "Self-Harm"
    EATING_DISORDERS = "Eating Disorders"
    DRUG_USE = "Drug Use"
    VIOLENT_THREATS = "Violent Threats, Incitement & Glorification"
    VIOLENT_EXTREMISM = "Terrorism & Violent Extremism"
    COORDINATION_HARM = "Coordination of Harm"
    REGULATED_GOODS = "Sale of Regulated Goods"
    SEXUAL_SOLICITATION = "Sexual Solicitation"
    SCAMS = "Scams"
    SPAM = "Spam"
    IMPERSONATION = "Impersonation"
    FAKE_ACCOUNTS = "Fake Accounts"
    INFO_INTEGRITY = "Inaccurate, Incomplete, False & Misleading Information"
    CURRENT_EVENTS_MISINFO = "Misinformation for current events"
    HISTORICAL_EVENTS_BIAS = "Biased or revisionist retelling of controversial historical events"
    ELECTION_INTEGRITY = "Inaccurate, Incomplete, False & Misleading Info in Election Context"
    DECEPTION = "Deceptive Inducement"
    COVERT_TARGETED = "Covert Targeted Persuasion"
    REPUTATIONAL_DAMAGE = "Reputational Damage"
    COPYRIGHT = "Copyright & Piracy"
    TRADEMARK = "Trademark"
    IP_UPLOAD = "Upload IP Images"
    PLAGIARISM = "Plagiarism & Academic Dishonesty"
    PROPRIETARY_INFO = "Proprietary, Confidential & Classified Information"
    PPI = "Private Personal Information"
    PUBLIC_FIGURES = "Images of Public Figures"
    NONCONSENSUAL_UPLOAD = "Upload Images of People without Consent"
    INSECURE_CODE = "Insecure Code"
    MALWARE = "Malware"
    MILITARY = "Weapons Development & Military"
    CBRN = "Chemical, Biological, Radiological, and Nuclear"
    HIGH_RISK_GOVERNMENT = "High-Risk Government Decision-Making"
    INFRASTRUCTURE_RISK = "Management or Operation of Critical Infrastructure in Energy, Transportation & Water"
    FINANCIAL_ADVICE = "Financial Advice"
    MLM = "Multi-Level Marketing"
    GAMBLING = "Gambling"
    LENDING = "Lending"
    FINANCIAL_ELIGIBILITY = "Financial Service Eligibility"
    HEALTH_DIAGNOSIS = "Health Diagnosis"
    PSEUDO_PHARMA = "Pseudo-Pharmaceuticals"
    PUBLIC_HEALTH = "Public & Personal Health"
    CAMPAIGNING = "Political Campaigning & Lobbying"
    LEGAL_ADVICE = "Legal Advice"
    ROMANTIC = "Romantic"
    SELF_VALIDATION = "Self-Validation"
    MENTAL_HEALTH = "Mental Health"
    EMOTIONAL = "Emotional"
    PROTECTED_INFERENCE = "Legally-Protected Attributes"
    EMOTION_INFERENCE = "Emotion"
    OTHER = "Other"

    @classmethod
    def _initialize_aliases(cls) -> None:  # type: ignore[override]
        """Initialize the alias mapping from raw strings to canonical categories."""
        if _HARM_CATEGORY_ALIASES:
            return
        _HARM_CATEGORY_ALIASES.update(  # type: ignore[arg-type, union-attr]
            {
                "ableism": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "bias": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "biology": [cls.CBRN],
                "bullying": [cls.HARASSMENT],
                "chemistry": [cls.CBRN],
                "controlled/regulated substances": [cls.DRUG_USE],
                "copyright issues": [cls.COPYRIGHT],
                "copyright/trademark/plagiarism": [cls.COPYRIGHT, cls.TRADEMARK, cls.PLAGIARISM],
                "criminal planning/confessions": [cls.COORDINATION_HARM],
                "discriminatory behavior": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "discrimination": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "drugs": [cls.DRUG_USE],
                "economic crime": [cls.SCAMS, cls.DECEPTION],
                "endangering national security": [cls.HIGH_RISK_GOVERNMENT],
                "endangering public health": [cls.PUBLIC_HEALTH],
                "fraud/deception": [cls.SCAMS, cls.DECEPTION],
                "guns and illegal weapons": [cls.MILITARY],
                "hate/identity hate": [cls.HATESPEECH, cls.REPRESENTATIONAL],
                "high risk gov decision making": [cls.HIGH_RISK_GOVERNMENT],
                "homophobia": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "human trafficking": [cls.COORDINATION_HARM],
                "illegal": [cls.COORDINATION_HARM],
                "insulting behavior": [cls.HARASSMENT],
                "medicine": [cls.PUBLIC_HEALTH],
                "mental manipulation": [cls.MENTAL_HEALTH],
                "misogyny": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "pharmacology": [cls.PSEUDO_PHARMA],
                "physical harm": [cls.VIOLENT_CONTENT],
                "physics": [cls.HIGH_RISK_GOVERNMENT],
                "pii/privacy": [cls.PPI],
                "political/misinformation/conspiracy": [cls.INFO_INTEGRITY, cls.CAMPAIGNING],
                "privacy violation": [cls.PPI],
                "psychological harm": [cls.EMOTIONAL],
                "psychology": [cls.MENTAL_HEALTH],
                "racism": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "religious discrimination": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "sexism": [cls.REPRESENTATIONAL, cls.HATESPEECH],
                "sexual": [cls.SEXUAL_CONTENT],
                "sexual content": [cls.SEXUAL_CONTENT],
                "sexual violence": [cls.SEXUAL_CONTENT, cls.VIOLENT_CONTENT],
                "sexualization": [cls.SEXUALIZATION],
                "stereotyping": [cls.REPRESENTATIONAL],
                "suicide and self harm": [cls.SUICIDE, cls.SELF_HARM],
                "threat": [cls.VIOLENT_THREATS],
                "grooming": [cls.GROOMING],
                "child leakage": [cls.CHILD_LEAKAGE],
                "dangerous situations": [cls.DANGEROUS_SITUATIONS],
                "violent": [cls.VIOLENT_CONTENT],
                "violence": [cls.VIOLENT_CONTENT],
                "white-collar crime": [cls.SCAMS, cls.DECEPTION],
            }
        )

    @classmethod
    def _resolve_canonical_category(cls, value: str) -> "HarmCategory | None":
        """
        Resolve a canonical category from enum name or display value.

        Returns:
            HarmCategory enum member if found, None otherwise.
        """
        normalized_value = value.strip().lower()
        if not normalized_value:
            return None

        for member in cls.__members__.values():
            if str(member.value).lower() == normalized_value or str(member.name).lower() == normalized_value:
                return member

        return None

    @classmethod
    def _coerce_alias_mapping_value(
        cls,
        *,
        alias_value: object,
        strict: bool = False,
    ) -> list["HarmCategory"]:
        """
        Convert an alias/override mapping value (list of strings) to canonical categories.

        Args:
            alias_value: List or tuple of strings mapping to canonical categories.
            strict: If True, raise ValueError for unmapped strings. Otherwise fallback to OTHER.

        Returns:
            List of canonical HarmCategory enum members.

        Raises:
            ValueError: If strict=True and an unmapped string is encountered.
        """
        values = alias_value if isinstance(alias_value, (list, tuple)) else [alias_value]
        other_category = cast("HarmCategory", cls.OTHER)

        resolved_categories: list[HarmCategory] = []
        for value in values:
            if isinstance(value, cls):
                resolved_categories.append(value)
                continue

            if isinstance(value, str):
                category = cls._resolve_canonical_category(value)
                if category is not None:
                    resolved_categories.append(category)
                    continue

            if strict:
                raise ValueError(f"Invalid harm category mapping value: {value!r}")

            resolved_categories.append(other_category)

        return resolved_categories if resolved_categories else [other_category]

    @classmethod
    def parse_many(
        cls,
        value: str,
        *,
        alias_overrides: Mapping[str, object] | None = None,
    ) -> list["HarmCategory"]:
        """
        Parse a raw harm category string to one or more canonical HarmCategory values.

        Performs case-insensitive matching against canonical names/values, then
        dataset-specific overrides, then built-in aliases. Falls back to OTHER.

        Args:
            value: Raw category string from a dataset.
            alias_overrides: Dataset-specific alias mapping to override defaults.

        Returns:
            List of one or more canonical HarmCategory enum members.
        """
        normalized_value = value.strip().lower()
        other_category = cast("HarmCategory", cls.OTHER)
        if not normalized_value:
            return [other_category]

        cls._initialize_aliases()

        canonical = cls._resolve_canonical_category(normalized_value)
        if canonical is not None:
            return [canonical]

        if alias_overrides and normalized_value in alias_overrides:
            return cls._coerce_alias_mapping_value(
                alias_value=alias_overrides[normalized_value],
                strict=True,
            )

        if normalized_value in _HARM_CATEGORY_ALIASES:
            return cls._coerce_alias_mapping_value(alias_value=_HARM_CATEGORY_ALIASES[normalized_value])

        logger.warning(
            "Unknown harm category %r — mapping to OTHER. "
            "Consider adding an alias in HarmCategory._initialize_aliases or passing alias_overrides.",
            value.strip(),
        )
        return [other_category]

    @classmethod
    def parse(
        cls,
        value: str,
        *,
        alias_overrides: Mapping[str, object] | None = None,
    ) -> "HarmCategory":
        """
        Parse a raw harm category string to a canonical HarmCategory.

        Performs case-insensitive matching against canonical names/values, aliases,
        and optional dataset-specific overrides.
        Falls back to OTHER for unknown categories.

        Args:
            value: Raw category string from a dataset.
            alias_overrides: Dataset-specific alias mapping to override defaults.

        Returns:
            Canonical HarmCategory enum member. For one-to-many mappings, returns
            the first mapped category.
        """
        return cls.parse_many(value, alias_overrides=alias_overrides)[0]

    @classmethod
    def get_definition(cls, category: "HarmCategory") -> str:
        """
        Retrieve the definition text for a harm category.

        Args:
            category: The HarmCategory to look up.

        Returns:
            Definition string, or placeholder if not found.
        """
        return _HARM_CATEGORY_DEFINITIONS.get(category.name, "No definition available.")


def standardize_harm_categories(
    raw_categories: list[str] | str | None,
    *,
    alias_overrides: Mapping[str, object] | None = None,
) -> list[str]:
    """
    Standardize raw harm categories to the canonical HarmCategory taxonomy.

    Converts a single category string or list of strings to standardized HarmCategory enum names.
    Supports one-to-many alias mappings and dataset-specific alias overrides.

    Args:
        raw_categories: Raw category string(s) from the dataset (e.g., "violence", "harmful"),
            or None for datasets that don't specify categories.
        alias_overrides: Optional dataset-specific mapping from raw categories to
            canonical category name(s) or enum values.

    Returns:
        List of standardized HarmCategory enum names (their .name attribute, e.g., "VIOLENT_CONTENT").

    Example:
        >>> standardize_harm_categories(["violence", "harassment"])
        ["VIOLENT_CONTENT", "HARASSMENT"]
        >>> standardize_harm_categories("sexual content")
        ["SEXUAL_CONTENT"]
    """
    if not raw_categories:
        return []

    # Normalize input to list
    categories_list = [raw_categories] if isinstance(raw_categories, str) else list(raw_categories)

    normalized_overrides: dict[str, object] = {}
    if alias_overrides:
        normalized_overrides = {k.strip().lower(): v for k, v in alias_overrides.items() if k and k.strip()}

    # Parse and standardize each category
    HarmCategory._initialize_aliases()
    standardized = []
    for raw_cat in categories_list:
        if raw_cat:  # Skip empty strings
            parsed_categories = HarmCategory.parse_many(raw_cat, alias_overrides=normalized_overrides)
            standardized.extend(parsed.name for parsed in parsed_categories)

    return standardized
