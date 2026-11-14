# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Response model for a saved registry entry's file."""

from pydantic import BaseModel, Field

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
    saved_by: str | None = Field(None, description="The name of the person who saved it, when sign-in was on")
    saved_by_me: bool = Field(False, description="Whether the signed-in user asking is the one who saved it")
    can_change: bool = Field(
        ...,
        description=(
            "Whether the caller may change or delete it: the person who saved it or an administrator, or anyone when "
            "sign-in is off. A file that doesn't say who saved it needs an administrator."
        ),
    )
