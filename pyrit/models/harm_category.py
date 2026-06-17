# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# type: ignore[misc, override, arg-type, union-attr, return-value]

"""Harm category taxonomy and standardization utilities for seed classification."""

import os
from enum import Enum

import yaml

try:
    from enum import StrEnum  # type: ignore[attr-defined]
except ImportError:  # Python 3.10 and below

    class StrEnum(str, Enum):  # type: ignore[misc]
        """Minimal backport of enum.StrEnum for Python < 3.11."""


_HARM_CATEGORY_ALIASES: dict[str, "HarmCategory"] = {}

with open(os.path.join(os.path.dirname(__file__), "harm_category_definitions.yaml")) as f:
    _HARM_CATEGORY_DEFINITIONS: dict[str, str] = yaml.safe_load(f).get("definitions", {})


class HarmCategory(StrEnum):
    """Standardized harm category taxonomy for seed classification and attack evaluation."""

    VERSION = "v1.0.0"

    HATESPEECH = "Hate Speech"
    HARASSMENT = "Harassment"
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
    ILLEGAL = "Illegal Activity"
    OTHER = "Other"

    @classmethod
    def _initialize_aliases(cls) -> None:  # type: ignore[override]
        """Initialize the alias mapping from raw strings to canonical categories."""
        if _HARM_CATEGORY_ALIASES:
            return
        _HARM_CATEGORY_ALIASES.update(  # type: ignore[arg-type, union-attr]
            {
                "ableism": cls.REPRESENTATIONAL,
                "bias": cls.REPRESENTATIONAL,
                "biology": cls.CBRN,
                "bullying": cls.HARASSMENT,
                "chemistry": cls.CBRN,
                "copyright issues": cls.COPYRIGHT,
                "cybercrime": cls.ILLEGAL,
                "discriminatory behavior": cls.REPRESENTATIONAL,
                "discrimination": cls.REPRESENTATIONAL,
                "drugs": cls.DRUG_USE,
                "economic crime": cls.ILLEGAL,
                "endangering national security": cls.HIGH_RISK_GOVERNMENT,
                "endangering public health": cls.PUBLIC_HEALTH,
                "homophobia": cls.REPRESENTATIONAL,
                "human trafficking": cls.COORDINATION_HARM,
                "illegal": cls.ILLEGAL,
                "insulting behavior": cls.HARASSMENT,
                "medicine": cls.PUBLIC_HEALTH,
                "mental manipulation": cls.MENTAL_HEALTH,
                "misogyny": cls.REPRESENTATIONAL,
                "pharmacology": cls.PSEUDO_PHARMA,
                "physical harm": cls.VIOLENT_CONTENT,
                "physics": cls.HIGH_RISK_GOVERNMENT,
                "privacy violation": cls.PPI,
                "psychological harm": cls.EMOTIONAL,
                "psychology": cls.MENTAL_HEALTH,
                "racism": cls.REPRESENTATIONAL,
                "religious discrimination": cls.REPRESENTATIONAL,
                "sexism": cls.REPRESENTATIONAL,
                "sexual content": cls.SEXUAL_CONTENT,
                "stereotyping": cls.REPRESENTATIONAL,
                "violent": cls.VIOLENT_CONTENT,
                "violence": cls.VIOLENT_CONTENT,
                "white-collar crime": cls.ILLEGAL,
            }
        )

    @classmethod
    def parse(cls, value: str) -> "HarmCategory":  # type: ignore[override]
        """
        Parse a raw harm category string to a canonical HarmCategory.

        Performs case-insensitive matching against both canonical values and aliases.
        Falls back to OTHER for unknown categories.

        Args:
            value: Raw category string from a dataset.

        Returns:
            Canonical HarmCategory enum member.
        """
        value = value.strip().lower()

        for member in cls:  # type: ignore[union-attr]
            if str(member.value).lower() == value:
                return member

        if value in _HARM_CATEGORY_ALIASES:
            return _HARM_CATEGORY_ALIASES[value]

        return cls.OTHER  # type: ignore[return-value]

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
) -> list[str]:
    """
    Standardize raw harm categories to the canonical HarmCategory taxonomy.

    Converts a single category string or list of strings to standardized HarmCategory enum names.
    Uses HarmCategory.parse() for alias resolution and fallback to OTHER.

    Args:
        raw_categories: Raw category string(s) from the dataset (e.g., "violence", "harmful"),
                       or None for datasets that don't specify categories.

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

    # Parse and standardize each category
    HarmCategory._initialize_aliases()
    standardized = []
    for raw_cat in categories_list:
        if raw_cat:  # Skip empty strings
            parsed = HarmCategory.parse(raw_cat)
            standardized.append(parsed.name)

    return standardized
