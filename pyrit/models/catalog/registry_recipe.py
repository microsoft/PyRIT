# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved registry entries: the recipe kept for each one and what restoring them found."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from pyrit.common import AuthMode  # noqa: TC001 (runtime-required by Pydantic)
from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS, MAX_TEXT_LENGTH

RECIPE_SCHEMA_VERSION = 1

_Identifier = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]
_TechniqueTag = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]


class RegistryEntryOrigin(str, Enum):
    """Where a registered entry comes from, and so whether it is still there after a restart."""

    SAVED = "saved"
    CONFIGURATION = "configuration"
    TEMPORARY = "temporary"


class RegistryRecipeKind(str, Enum):
    """The registries whose entries can be saved, named after their storage folders."""

    TARGETS = "targets"
    CONVERTERS = "converters"
    SCORERS = "scorers"
    TECHNIQUES = "techniques"


class _RegistryRecipe(BaseModel):
    """Fields every saved recipe shares. The entry's name is its document name, so it isn't repeated."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    # A literal 1 rather than RECIPE_SCHEMA_VERSION: a document saved without this field is a version 1
    # document and must keep reading as one after the constant is bumped, or a migration would skip it.
    schema_version: int = Field(1, ge=1, le=RECIPE_SCHEMA_VERSION, description="Shape of this document")


class SavedEntryOwner(BaseModel):
    """The signed-in user who saved an entry."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    oid: _Identifier = Field(..., min_length=1, description="The user's Microsoft Entra object ID")
    name: _Identifier = Field(..., description="The user's display name, or email when no name is set")


class ComponentRecipe(_RegistryRecipe):
    """How to rebuild one saved target, converter, or scorer."""

    class_module: _Identifier = Field(
        ..., min_length=1, description="Module that defined the class when the entry was saved"
    )
    class_name: _Identifier = Field(..., min_length=1, description="Registered class name")
    auth_mode: AuthMode | None = Field(
        None, description="How a saved target signs in, as its create request chose; None for converters and scorers"
    )
    params: dict[_Identifier, JsonValue] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description="Constructor parameters as the API accepted them; other entries are named, never embedded",
    )
    server_credential: _Identifier | None = Field(
        None,
        description="The server credential from this server's list that supplies the key; the key isn't stored",
    )
    owner: SavedEntryOwner | None = Field(None, description="Who saved the entry; None when sign-in is off")


class TechniqueRecipe(_RegistryRecipe):
    """
    How to rebuild one saved technique: the fields of its create request, without the name.

    This is a deliberate copy of the technique API's create request, so a change to the API is also a decision
    about the saved format. Tests keep the two in step.
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
    name: str


class StoredRecipe(BaseModel):
    """A saved recipe and the version of the document it was read from."""

    ref: SavedEntryRef
    recipe: ComponentRecipe | TechniqueRecipe
    version: str = Field(..., description="Opaque version of the document this recipe was read from")

    @model_validator(mode="after")
    def _validate_recipe_kind(self) -> StoredRecipe:
        """
        Reject a recipe filed under a registry it doesn't fit.

        A technique recipe belongs only under techniques, and only a target has an ``auth_mode``.

        Returns:
            StoredRecipe: The stored recipe.

        Raises:
            ValueError: If the recipe doesn't fit the registry it's filed under.
        """
        expects_technique = self.ref.kind is RegistryRecipeKind.TECHNIQUES
        if expects_technique != isinstance(self.recipe, TechniqueRecipe):
            expected = TechniqueRecipe if expects_technique else ComponentRecipe
            raise ValueError(f"A saved {self.ref.kind.value} entry needs a {expected.__name__}")
        has_auth_mode = isinstance(self.recipe, ComponentRecipe) and self.recipe.auth_mode is not None
        if has_auth_mode and self.ref.kind is not RegistryRecipeKind.TARGETS:
            raise ValueError(f"Only a saved target has an auth_mode, not a saved {self.ref.kind.value} entry")
        return self


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

    @property
    def explanation(self) -> str:
        """A fixed explanation of the reason that's safe to show anyone, unlike a failure's message."""
        return _RESTORE_FAILURE_EXPLANATIONS[self]


_RESTORE_FAILURE_EXPLANATIONS: dict[RestoreFailureReason, str] = {
    RestoreFailureReason.UNREADABLE_STORAGE: "The folder of saved entries couldn't be read.",
    RestoreFailureReason.UNREADABLE_RECIPE: "The saved file couldn't be read.",
    RestoreFailureReason.INVALID_RECIPE: "The saved file isn't a valid entry.",
    RestoreFailureReason.NEWER_SCHEMA_VERSION: "It was saved by a newer version of PyRIT.",
    RestoreFailureReason.UNKNOWN_CLASS: "Its class or attack type isn't available.",
    RestoreFailureReason.MODULE_MISMATCH: "Its class is now one from outside PyRIT.",
    RestoreFailureReason.UNSUPPORTED_CLASS: "Its class can't be rebuilt from a saved file.",
    RestoreFailureReason.UNSUPPORTED_CREDENTIAL: (
        "It holds a credential, or would use one of this server's, which a saved entry can't do."
    ),
    RestoreFailureReason.NAME_CLASH: "Its name or one of its tags is already taken.",
    RestoreFailureReason.MISSING_DEPENDENCY: "It uses an entry that doesn't exist.",
    RestoreFailureReason.FAILED_DEPENDENCY: "It uses an entry that couldn't be restored.",
    RestoreFailureReason.DEPENDENCY_LOOP: "It is part of a group of entries that use each other.",
    RestoreFailureReason.BUILD_ERROR: "It couldn't be built.",
}


class RestoreFailure(BaseModel):
    """One saved entry, or one whole storage folder, that couldn't be restored."""

    model_config = ConfigDict(frozen=True)

    kind: RegistryRecipeKind
    name: str | None = Field(None, description="Entry name; None when the whole folder couldn't be read")
    reason: RestoreFailureReason
    message: str = Field(..., description="Why, in words safe to show users")
    version: str | None = Field(None, description="Version of the stored document, when it could be read")
    dependency: SavedEntryRef | None = Field(None, description="The entry this one needed that is missing or failed")


class IgnoredRecipeFile(BaseModel):
    """A stored file whose name can't be an entry name, so it was never read."""

    model_config = ConfigDict(frozen=True)

    kind: RegistryRecipeKind
    file_name: str
    message: str


class RegistryRestoreReport(BaseModel):
    """What a restore rebuilt, what it couldn't, and which files it skipped."""

    restored: list[SavedEntryRef] = Field(default_factory=list)
    failures: list[RestoreFailure] = Field(default_factory=list)
    ignored: list[IgnoredRecipeFile] = Field(default_factory=list)
