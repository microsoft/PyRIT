# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Policy models for text ShieldGemma classifiers.

Reference: Wenjun Zeng et al., "ShieldGemma: Generative AI Content Moderation Based on
Gemma" (2024), https://arxiv.org/abs/2407.21772.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_SEED_PROMPT_PATH

if TYPE_CHECKING:
    from pathlib import Path

SHIELDGEMMA_DEFAULT_POLICY_PATH = (SCORER_SEED_PROMPT_PATH / "shieldgemma" / "shieldgemma_policy.yaml").resolve()


class ShieldGemmaMessageRole(enum.Enum):
    """
    The conversation role whose message ShieldGemma classifies.

    Prompt and response classification use different guideline wording and request layouts,
    so the role selects both.
    """

    USER = "user"
    CHATBOT = "chatbot"

    @property
    def guideline_prefix(self) -> str:
        """The clause Google's policy text uses to attribute a guideline to this role."""
        if self is ShieldGemmaMessageRole.USER:
            return "The prompt shall not contain or seek generation of"
        return "The chatbot shall not generate"


class ShieldGemmaGuideline(BaseModel):
    """
    One safety principle that ShieldGemma judges a message against.

    ShieldGemma evaluates a single guideline per request, so a guideline is the unit a
    scorer is configured with rather than a code in a larger taxonomy.

    ``description`` holds only the role-independent body of the principle, which Google's
    policy text phrases as ``content that ...``. The attributing clause differs between
    prompt and response classification, so it is supplied by the role at render
    time rather than baked into the stored text. That keeps a guideline from being worded
    for one use case and then sent in the other.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)

    @field_validator("name", "description")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("ShieldGemma guideline names and descriptions must not have surrounding whitespace.")
        if not value:
            raise ValueError("ShieldGemma guideline names and descriptions must not be empty.")
        return value

    def rendered(self, message_role: ShieldGemmaMessageRole) -> str:
        """
        Render the guideline for one ShieldGemma use case.

        Args:
            message_role (ShieldGemmaMessageRole): The role being judged, which selects the
                attributing clause.

        Returns:
            str: The guideline as it appears in the request.
        """
        return f'"{self.name}": {message_role.guideline_prefix} {self.description}'


class ShieldGemmaPolicy(BaseModel):
    """A named set of ShieldGemma guidelines, used to look up the one a scorer judges."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    guidelines: tuple[ShieldGemmaGuideline, ...] = Field(min_length=1)

    @field_validator("name", "version")
    @classmethod
    def _validate_policy_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("ShieldGemma policy names and versions must not have surrounding whitespace.")
        return value

    @model_validator(mode="after")
    def _validate_unique_guideline_names(self) -> ShieldGemmaPolicy:
        # Matched the same way get() matches, so a policy cannot validate with two names
        # that only differ in case and then leave the second one unreachable.
        names = [name.casefold() for name in self.guideline_names]
        if len(set(names)) != len(names):
            raise ValueError("ShieldGemma policy guideline names must be unique, ignoring case.")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> ShieldGemmaPolicy:
        """
        Load a ShieldGemma policy from YAML.

        Args:
            path (str | Path): Path to the policy YAML file.

        Returns:
            ShieldGemmaPolicy: The loaded policy.

        Raises:
            ValueError: If the YAML does not contain a mapping or fails validation.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"ShieldGemma policy YAML file '{resolved_path}' must contain a mapping.")
        return cls.model_validate(loaded)

    @classmethod
    def default(cls) -> ShieldGemmaPolicy:
        """
        Load the bundled ShieldGemma policy.

        Returns:
            ShieldGemmaPolicy: The bundled policy covering Google's documented harm types.
        """
        return cls.from_yaml(SHIELDGEMMA_DEFAULT_POLICY_PATH)

    @property
    def guideline_names(self) -> tuple[str, ...]:
        """The configured guideline names in policy order."""
        return tuple(guideline.name for guideline in self.guidelines)

    def get(self, name: str) -> ShieldGemmaGuideline:
        """
        Look up a guideline by name.

        Args:
            name (str): The guideline name, matched case-insensitively.

        Returns:
            ShieldGemmaGuideline: The matching guideline.

        Raises:
            KeyError: If no guideline in the policy has that name.
        """
        for guideline in self.guidelines:
            if guideline.name.casefold() == name.casefold():
                return guideline
        available = ", ".join(self.guideline_names)
        raise KeyError(f"ShieldGemma policy '{self.name}' has no guideline named '{name}'. Available: {available}.")
