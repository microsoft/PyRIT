# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Harm category taxonomy and standardization utilities for seed classification."""

import logging
import os
import sys
from collections.abc import Mapping
from enum import Enum
from typing import cast

import yaml

logger = logging.getLogger(__name__)

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # Python 3.10 and below

    class StrEnum(str, Enum):
        """Minimal backport of enum.StrEnum for Python < 3.11."""

        __str__ = str.__str__


_HARM_CATEGORY_ALIASES: "dict[str, list[HarmCategory]]" = {}
_CANONICAL_LOOKUP: "dict[str, HarmCategory]" = {}

with open(os.path.join(os.path.dirname(__file__), "harm_category_definitions.yaml")) as f:
    _HARM_CATEGORY_YAML: dict = yaml.safe_load(f) or {}
    _HARM_CATEGORY_DEFINITIONS: dict[str, str] = _HARM_CATEGORY_YAML.get("definitions", {})

HARM_CATEGORY_TAXONOMY_VERSION: str = _HARM_CATEGORY_YAML.get("version", "v1.0.0")


class HarmCategory(StrEnum):
    """Standardized harm category taxonomy for seed classification and attack evaluation."""

    HATE_SPEECH = "Hate Speech"
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
    CBRN = "Chemical, Biological, Radiological, & Nuclear"
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
    def _initialize_aliases(cls) -> None:
        """Initialize the alias mapping from raw strings to canonical categories."""
        if _HARM_CATEGORY_ALIASES:
            return
        _HARM_CATEGORY_ALIASES.update(
            {
                "ableism": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "bias": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "bullying": [cls.HARASSMENT],
                "chemistry": [cls.CBRN],
                "controlled/regulated substances": [cls.DRUG_USE],
                "copyright issues": [cls.COPYRIGHT],
                "copyright/trademark/plagiarism": [cls.COPYRIGHT, cls.TRADEMARK, cls.PLAGIARISM],
                "criminal planning/confessions": [cls.COORDINATION_HARM],
                "discriminatory behavior": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "discrimination": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "drugs": [cls.DRUG_USE],
                "economic crime": [cls.SCAMS, cls.DECEPTION],
                "endangering national security": [cls.HIGH_RISK_GOVERNMENT],
                "endangering public health": [cls.PUBLIC_HEALTH],
                "fraud/deception": [cls.SCAMS, cls.DECEPTION],
                "guns and illegal weapons": [cls.MILITARY],
                "hate/identity hate": [cls.HATE_SPEECH, cls.REPRESENTATIONAL],
                "high risk gov decision making": [cls.HIGH_RISK_GOVERNMENT],
                "homophobia": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "human trafficking": [cls.COORDINATION_HARM],
                "illegal": [cls.COORDINATION_HARM],
                "insulting behavior": [cls.HARASSMENT],
                "medicine": [cls.PUBLIC_HEALTH],
                "mental manipulation": [cls.MENTAL_HEALTH],
                "misogyny": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "pharmacology": [cls.PSEUDO_PHARMA],
                "physical harm": [cls.VIOLENT_CONTENT],
                "pii/privacy": [cls.PPI],
                "political/misinformation/conspiracy": [cls.INFO_INTEGRITY, cls.CAMPAIGNING],
                "privacy violation": [cls.PPI],
                "psychological harm": [cls.EMOTIONAL],
                "psychology": [cls.MENTAL_HEALTH],
                "racism": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "religious discrimination": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
                "sexism": [cls.REPRESENTATIONAL, cls.HATE_SPEECH],
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
                # Cross-dataset labels that recurred in >=2 loader override maps,
                # promoted here so loaders don't redefine them individually.
                "adult content": [cls.SEXUAL_CONTENT],
                "chemical_biological": [cls.CBRN],
                "child abuse": [cls.CHILD_LEAKAGE, cls.GROOMING, cls.SEXUAL_CONTENT],
                "copyright": [cls.COPYRIGHT],
                "cybercrime": [cls.COORDINATION_HARM, cls.MALWARE],
                "cybercrime_intrusion": [cls.COORDINATION_HARM, cls.MALWARE],
                "defamation": [cls.REPUTATIONAL_DAMAGE],
                "disinformation": [cls.INFO_INTEGRITY],
                "environmental crimes": [cls.COORDINATION_HARM],
                "expert advice": [cls.FINANCIAL_ADVICE, cls.HEALTH_DIAGNOSIS, cls.LEGAL_ADVICE],
                "fraud": [cls.DECEPTION, cls.SCAMS],
                "government decision-making": [cls.HIGH_RISK_GOVERNMENT],
                "harassment/discrimination": [cls.HARASSMENT, cls.HATE_SPEECH, cls.REPRESENTATIONAL],
                "hate": [cls.HATE_SPEECH, cls.REPRESENTATIONAL],
                "illegal activities": [cls.COORDINATION_HARM],
                "illegal activity": [cls.COORDINATION_HARM],
                "legal opinion": [cls.LEGAL_ADVICE],
                "malware generation": [cls.MALWARE],
                "malware/hacking": [cls.MALWARE],
                "misinformation_disinformation": [cls.INFO_INTEGRITY],
                "political campaigning": [cls.CAMPAIGNING],
                "pornography": [cls.SEXUAL_CONTENT],
                "privacy": [cls.PPI],
                "privacy violations": [cls.PPI],
                "sex-related crimes": [cls.SEXUAL_CONTENT],
                "sexual/adult content": [cls.SEXUAL_CONTENT],
                "suicide & self-harm": [cls.SUICIDE, cls.SELF_HARM],
                "theft": [cls.COORDINATION_HARM],
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

        if not _CANONICAL_LOOKUP:
            for member in cls.__members__.values():
                _CANONICAL_LOOKUP[str(member.value).lower()] = member
                _CANONICAL_LOOKUP[str(member.name).lower()] = member

        return _CANONICAL_LOOKUP.get(normalized_value)

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
                category = value
            elif isinstance(value, str):
                category = cls._resolve_canonical_category(value)
            else:
                category = None

            if category is None:
                if strict:
                    raise ValueError(f"Invalid harm category mapping value: {value!r}")
                category = other_category

            resolved_categories.append(category)

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

        if alias_overrides:
            # Match override keys case-insensitively so callers can pass raw dataset
            # labels without pre-normalizing (parity with standardize_harm_categories).
            for override_key, override_value in alias_overrides.items():
                if override_key and override_key.strip().lower() == normalized_value:
                    return cls._coerce_alias_mapping_value(alias_value=override_value, strict=True)

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

    def pillars(self) -> list["HarmCategoryPillar"]:
        """
        Return the pillars this category belongs to.

        A category can belong to multiple pillars, or to none (only OTHER is unassigned).

        Returns:
            list[HarmCategoryPillar]: The pillars containing this category.
        """
        return list(_CATEGORY_TO_PILLARS.get(self, []))


class HarmCategoryPillar(StrEnum):
    """
    Coarse groupings ("pillars") layered over the fine-grained HarmCategory taxonomy.

    A pillar contains many harm categories, and a single category can belong to
    multiple pillars (e.g. HARASSMENT is in both CHILD_SAFETY and HARMFUL_CONTENT).
    This is a rollup layer: filtering by a pillar expands to its member categories.
    Every HarmCategory except OTHER belongs to at least one pillar.
    """

    CHILD_SAFETY = "Child Safety"
    HARMFUL_CONTENT = "Harmful Content"
    FAIRNESS = "Fairness"
    SELF_INJURY = "Self-Injury"
    INCITEMENT = "Incitement"
    SENSITIVE_GOODS_SERVICES = "Sensitive Goods & Services"
    SPAM_SCAMS = "Spam & Scams"
    INAUTHENTIC_ACCOUNTS = "Inauthentic Accounts"
    INFO_INTEGRITY = "Information Integrity excluding Elections"
    ELECTION_INTEGRITY = "Election-Related Integrity"
    PERSUASION = "Persuasion"
    IP = "Intellectual Property"
    PRIVACY = "Privacy"
    EXPLOITS = "Exploits"
    WEAPONS = "Weapons"
    HIGH_RISK_DECISIONS = "High-Risk Decision-Making"
    FINANCE = "Finance"
    HEALTH = "Health"
    POLITICS = "Politics"
    LEGAL = "Legal"
    PSYCHOSOCIAL = "Psychosocial Harms"
    ATTRIBUTE_INFERENCE = "Inference of Sensitive Attributes"

    def categories(self) -> list["HarmCategory"]:
        """Return the harm categories that belong to this pillar."""
        return list(_PILLAR_TO_CATEGORIES.get(self, []))


# The taxonomy is static, so both direction maps are built once at import time. Both enums are
# fully defined at this point, so the maps can reference their members directly.
_PILLAR_TO_CATEGORIES: dict[HarmCategoryPillar, list[HarmCategory]] = {
    HarmCategoryPillar.CHILD_SAFETY: [
        HarmCategory.SEXUALIZATION,
        HarmCategory.GROOMING,
        HarmCategory.CHILD_LEAKAGE,
        HarmCategory.DANGEROUS_SITUATIONS,
        HarmCategory.VIOLENT_CONTENT,
        HarmCategory.HARASSMENT,
        HarmCategory.SUICIDE,
        HarmCategory.SELF_HARM,
        HarmCategory.EATING_DISORDERS,
        HarmCategory.DRUG_USE,
    ],
    HarmCategoryPillar.HARMFUL_CONTENT: [
        HarmCategory.HATE_SPEECH,
        HarmCategory.HARASSMENT,
        HarmCategory.VIOLENT_CONTENT,
        HarmCategory.SEXUAL_CONTENT,
        HarmCategory.PROFANITY,
    ],
    HarmCategoryPillar.FAIRNESS: [
        HarmCategory.QUALITY_OF_SERVICE,
        HarmCategory.ALLOCATION,
        HarmCategory.REPRESENTATIONAL,
    ],
    HarmCategoryPillar.SELF_INJURY: [
        HarmCategory.SUICIDE,
        HarmCategory.SELF_HARM,
        HarmCategory.EATING_DISORDERS,
        HarmCategory.DRUG_USE,
    ],
    HarmCategoryPillar.INCITEMENT: [
        HarmCategory.VIOLENT_THREATS,
        HarmCategory.VIOLENT_EXTREMISM,
        HarmCategory.COORDINATION_HARM,
    ],
    HarmCategoryPillar.SENSITIVE_GOODS_SERVICES: [
        HarmCategory.REGULATED_GOODS,
        HarmCategory.SEXUAL_SOLICITATION,
    ],
    HarmCategoryPillar.SPAM_SCAMS: [
        HarmCategory.SCAMS,
        HarmCategory.SPAM,
    ],
    HarmCategoryPillar.INAUTHENTIC_ACCOUNTS: [
        HarmCategory.IMPERSONATION,
        HarmCategory.FAKE_ACCOUNTS,
    ],
    HarmCategoryPillar.INFO_INTEGRITY: [
        HarmCategory.INFO_INTEGRITY,
        HarmCategory.CURRENT_EVENTS_MISINFO,
        HarmCategory.HISTORICAL_EVENTS_BIAS,
    ],
    HarmCategoryPillar.ELECTION_INTEGRITY: [
        HarmCategory.ELECTION_INTEGRITY,
    ],
    HarmCategoryPillar.PERSUASION: [
        HarmCategory.DECEPTION,
        HarmCategory.COVERT_TARGETED,
        HarmCategory.REPUTATIONAL_DAMAGE,
    ],
    HarmCategoryPillar.IP: [
        HarmCategory.COPYRIGHT,
        HarmCategory.TRADEMARK,
        HarmCategory.IP_UPLOAD,
        HarmCategory.PLAGIARISM,
    ],
    HarmCategoryPillar.PRIVACY: [
        HarmCategory.PROPRIETARY_INFO,
        HarmCategory.PPI,
        HarmCategory.PUBLIC_FIGURES,
        HarmCategory.NONCONSENSUAL_UPLOAD,
    ],
    HarmCategoryPillar.EXPLOITS: [
        HarmCategory.INSECURE_CODE,
        HarmCategory.MALWARE,
    ],
    HarmCategoryPillar.WEAPONS: [
        HarmCategory.MILITARY,
        HarmCategory.CBRN,
    ],
    HarmCategoryPillar.HIGH_RISK_DECISIONS: [
        HarmCategory.HIGH_RISK_GOVERNMENT,
        HarmCategory.INFRASTRUCTURE_RISK,
    ],
    HarmCategoryPillar.FINANCE: [
        HarmCategory.FINANCIAL_ADVICE,
        HarmCategory.MLM,
        HarmCategory.GAMBLING,
        HarmCategory.LENDING,
        HarmCategory.FINANCIAL_ELIGIBILITY,
    ],
    HarmCategoryPillar.HEALTH: [
        HarmCategory.HEALTH_DIAGNOSIS,
        HarmCategory.PSEUDO_PHARMA,
        HarmCategory.PUBLIC_HEALTH,
    ],
    HarmCategoryPillar.POLITICS: [
        HarmCategory.CAMPAIGNING,
    ],
    HarmCategoryPillar.LEGAL: [
        HarmCategory.LEGAL_ADVICE,
    ],
    HarmCategoryPillar.PSYCHOSOCIAL: [
        HarmCategory.ROMANTIC,
        HarmCategory.SELF_VALIDATION,
        HarmCategory.MENTAL_HEALTH,
        HarmCategory.EMOTIONAL,
    ],
    HarmCategoryPillar.ATTRIBUTE_INFERENCE: [
        HarmCategory.PROTECTED_INFERENCE,
        HarmCategory.EMOTION_INFERENCE,
    ],
}


def _build_category_to_pillars() -> dict[HarmCategory, list[HarmCategoryPillar]]:
    """
    Invert _PILLAR_TO_CATEGORIES so each category lists the pillars that contain it.

    Returns:
        dict[HarmCategory, list[HarmCategoryPillar]]: Each category mapped to its containing pillars.
    """
    inverse: dict[HarmCategory, list[HarmCategoryPillar]] = {}
    for pillar, categories in _PILLAR_TO_CATEGORIES.items():
        for category in categories:
            pillars_for_category = inverse.setdefault(category, [])
            if pillar not in pillars_for_category:
                pillars_for_category.append(pillar)
    return inverse


_CATEGORY_TO_PILLARS: dict[HarmCategory, list[HarmCategoryPillar]] = _build_category_to_pillars()


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
    standardized: list[str] = []
    for raw_cat in categories_list:
        if not raw_cat:
            continue

        raw_cat = raw_cat.strip()
        if not raw_cat:
            continue

        parsed_categories = HarmCategory.parse_many(raw_cat, alias_overrides=normalized_overrides)
        standardized.extend(parsed.name for parsed in parsed_categories)

    # De-duplicate while preserving order: overlapping n:1 / 1:many mappings
    # (e.g. "racism" + "sexism" -> REPRESENTATIONAL) must not repeat a category.
    return list(dict.fromkeys(standardized))
