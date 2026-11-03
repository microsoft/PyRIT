# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved registry entries: the recipe kept for each one and what restoring them found."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PlainSerializer, field_validator

from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS, MAX_TEXT_LENGTH

_Identifier = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]
_TechniqueTag = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]


def _escape_invalid_text(value: str) -> str:
    """
    Escape characters that can't be written as UTF-8, such as bytes from a file name that isn't UTF-8.

    Returns:
        str: The text, safe to send in a JSON response.
    """
    return value.encode("utf-8", "backslashreplace").decode("utf-8")


# The model keeps the text it was given, so a name still matches the entry it names; only JSON output is escaped.
_DisplayText = Annotated[str, PlainSerializer(_escape_invalid_text, return_type=str, when_used="json")]


class RegistryRecipeKind(str, Enum):
    """The registries whose entries can be saved, named after their storage folders."""

    TARGETS = "targets"
    CONVERTERS = "converters"
    SCORERS = "scorers"
    TECHNIQUES = "techniques"


class _RegistryRecipe(BaseModel):
    """Fields every saved recipe shares. The entry's name is its document name, so it isn't repeated."""

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    schema_version: int = Field(
        CURRENT_SCHEMA_VERSION, ge=1, le=CURRENT_SCHEMA_VERSION, description="Shape of this document"
    )


class ComponentRecipe(_RegistryRecipe):
    """How to rebuild one saved target, converter, or scorer."""

    class_module: _Identifier = Field(
        ..., min_length=1, description="Module that defined the class when the entry was saved"
    )
    class_name: _Identifier = Field(..., min_length=1, description="Registered class name")
    params: dict[_Identifier, JsonValue] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description="Constructor parameters as the API accepted them; other entries are named, never embedded",
    )


class TechniqueRecipe(_RegistryRecipe):
    """
    How to rebuild one saved technique: the fields of its create request, without the name.

    The fields and their limits mirror the technique API's create request, which this library
    can't import.
    """

    type: str = Field(..., min_length=1, max_length=256, description="Registered attack type")
    params: dict[_Identifier, JsonValue] = Field(
        default_factory=dict, max_length=MAX_ITEMS, description="Attack constructor parameters"
    )
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    tags: list[_TechniqueTag] = Field(default_factory=list, max_length=MAX_ITEMS)
    request_converters: list[_Identifier] | None = Field(default=None, max_length=MAX_ITEMS)
    response_converters: list[_Identifier] | None = Field(default=None, max_length=MAX_ITEMS)
    adversarial_chat: _Identifier | None = None
    adversarial_system_prompt: str | None = None
    adversarial_seed_prompt: str | None = None
    adversarial_prompt_template: str | None = None

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        folded = {tag.casefold() for tag in value}
        if len(folded) != len(value):
            raise ValueError("Tags must be unique, including letter case")
        if {"all", "default"} & folded:
            raise ValueError("all and default are reserved selectors")
        return value


class SavedEntryRef(BaseModel):
    """Points at one saved entry."""

    model_config = ConfigDict(frozen=True)

    kind: RegistryRecipeKind
    name: _DisplayText


class StoredRecipe(BaseModel):
    """A saved recipe and the version of the document it was read from."""

    model_config = ConfigDict(frozen=True)

    ref: SavedEntryRef
    recipe: ComponentRecipe | TechniqueRecipe
    version: str = Field(..., description="Opaque version of the document this recipe was read from")


class RestoreFailureReason(str, Enum):
    """Why a saved entry couldn't be restored."""

    UNREADABLE_STORAGE = "unreadable_storage"
    UNREADABLE_RECIPE = "unreadable_recipe"
    INVALID_RECIPE = "invalid_recipe"
    NEWER_SCHEMA_VERSION = "newer_schema_version"
    UNKNOWN_CLASS = "unknown_class"
    MODULE_MISMATCH = "module_mismatch"
    UNSUPPORTED_CLASS = "unsupported_class"
    UNSUPPORTED_CREDENTIAL = "unsupported_credential"
    NAME_CLASH = "name_clash"
    MISSING_DEPENDENCY = "missing_dependency"
    FAILED_DEPENDENCY = "failed_dependency"
    DEPENDENCY_LOOP = "dependency_loop"
    BUILD_ERROR = "build_error"


class RestoreFailure(BaseModel):
    """One saved entry, or one whole storage folder, that couldn't be restored."""

    model_config = ConfigDict(frozen=True)

    kind: RegistryRecipeKind
    name: _DisplayText | None = Field(None, description="Entry name; None when the whole folder couldn't be read")
    reason: RestoreFailureReason
    message: _DisplayText = Field(..., description="Why, in words safe to show users")
    version: str | None = Field(None, description="Version of the stored document, when it could be read")
    dependency: SavedEntryRef | None = Field(None, description="The entry this one needed that is missing or failed")


class IgnoredRecipeFile(BaseModel):
    """A stored file whose name can't be an entry name, so it was never read."""

    model_config = ConfigDict(frozen=True)

    kind: RegistryRecipeKind
    file_name: _DisplayText
    message: _DisplayText


class RegistryRestoreReport(BaseModel):
    """What a restore rebuilt, what it couldn't, and which files it skipped."""

    model_config = ConfigDict(frozen=True)

    restored: list[SavedEntryRef] = Field(default_factory=list)
    failures: list[RestoreFailure] = Field(default_factory=list)
    ignored: list[IgnoredRecipeFile] = Field(default_factory=list)
