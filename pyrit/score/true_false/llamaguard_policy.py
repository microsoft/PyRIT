# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.common import verify_and_resolve_path

if TYPE_CHECKING:
    from pathlib import Path


class LlamaGuardCategory(BaseModel):
    """One category in a LlamaGuard safety policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None

    @field_validator("code", "name")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("LlamaGuard category codes and names must not have surrounding whitespace.")
        if not value:
            raise ValueError("LlamaGuard category codes and names must not be empty.")
        return value

    @field_validator("code")
    @classmethod
    def _validate_code_delimiters(cls, code: str) -> str:
        if any(delimiter in code for delimiter in (",", "\n", "\r")):
            raise ValueError("LlamaGuard category codes must not contain commas or newlines.")
        return code

    @field_validator("description")
    @classmethod
    def _validate_description(cls, description: str | None) -> str | None:
        if description is not None and not description.strip():
            raise ValueError("LlamaGuard category descriptions must not be blank.")
        return description


class LlamaGuardPolicy(BaseModel):
    """A versioned set of categories used to prompt and validate Llama Guard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    categories: tuple[LlamaGuardCategory, ...] = Field(min_length=1)

    @field_validator("name", "version")
    @classmethod
    def _validate_policy_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("LlamaGuard policy names and versions must not have surrounding whitespace.")
        return value

    @model_validator(mode="after")
    def _validate_unique_category_codes(self) -> LlamaGuardPolicy:
        category_codes = self.category_codes
        if len(set(category_codes)) != len(category_codes):
            raise ValueError("LlamaGuard policy category codes must be unique.")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> LlamaGuardPolicy:
        """
        Load a LlamaGuard policy from YAML.

        Args:
            path (str | Path): Path to the policy YAML file.

        Returns:
            LlamaGuardPolicy: The loaded policy.

        Raises:
            ValueError: If the YAML does not contain a mapping or fails validation.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"LlamaGuard policy YAML file '{resolved_path}' must contain a mapping.")
        return cls.model_validate(loaded)

    @property
    def category_codes(self) -> tuple[str, ...]:
        """The configured category codes in policy order."""
        return tuple(category.code for category in self.categories)

    @property
    def rendered_categories(self) -> str:
        """The category block rendered for a LlamaGuard request."""
        rendered: list[str] = []
        for category in self.categories:
            name = category.name if category.name.endswith((".", "!", "?")) else f"{category.name}."
            rendered.append(f"{category.code}: {name}")
            if category.description is not None:
                rendered.append(category.description)
        return "\n".join(rendered)
