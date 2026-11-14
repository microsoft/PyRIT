# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for a saved registry entry's file."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.catalog import RegistryRecipeKind, RestoreFailureReason


class SavedEntryResponse(BaseModel):
    """A saved target, converter, scorer, or technique, as its file describes it."""

    kind: RegistryRecipeKind = Field(..., description="The kind of entry: targets, converters, scorers, or techniques")
    name: str = Field(..., description="The entry's name")
    version: str | None = Field(
        None,
        description=(
            "The file's version, to send back as expected_version when deleting it; null when the file can't be read"
        ),
    )
    problem: RestoreFailureReason | None = Field(
        None, description="Why the file can't be used, when it can't be read or isn't a valid entry"
    )
    recipe: dict[str, Any] | None = Field(
        None,
        description=(
            "What the file saves, without who saved it, for a caller who may change it; null for anyone else and "
            "when the file can't be used. Send it back changed to edit the entry."
        ),
    )
    saved_by: str | None = Field(None, description="The name of the person who saved it, when sign-in was on")
    saved_by_me: bool = Field(False, description="Whether the signed-in user asking is the one who saved it")
    can_change: bool = Field(
        ...,
        description=(
            "Whether the caller may change or delete it: the person who saved it or an administrator, or anyone when "
            "sign-in is off. A file that doesn't say who saved it needs an administrator."
        ),
    )


class UpdateSavedEntryRequest(BaseModel):
    """New settings for a saved entry, which keeps its name, its class or attack type, and who saved it."""

    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="The version GET returned, so a file that changed since then isn't overwritten",
    )
    recipe: dict[str, Any] = Field(
        ...,
        description=(
            "The recipe GET returned, changed. A target, converter, or scorer changes its params; a technique changes "
            "anything its create request takes except its name, attack type, and persist."
        ),
    )


class SavedEntryUpdateResponse(SavedEntryResponse):
    """A saved entry after an edit."""

    running: bool = Field(
        ...,
        description=(
            "Whether the entry was running before the edit. False when it hadn't been restored; the edit registers "
            "it once its new settings work."
        ),
    )
    still_using_old: list[str] = Field(
        default_factory=list,
        description="Registered entries that hold the entry and keep its old settings until the next apply or restart",
    )
