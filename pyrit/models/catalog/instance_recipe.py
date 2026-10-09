# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved construction recipes for named targets, converters, and scorers."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.models.identifiers.component_identifier import JSONValue
from pyrit.models.parameter import ComponentType

# Names a target, converter, or scorer can have; the API accepts the same names.
INSTANCE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class CredentialReference(BaseModel):
    """
    Point a constructor parameter at a server environment variable.

    Only the variable name is saved. The value is read when the instance is built,
    so a recipe never holds the credential itself. Key Vault values reach the
    variable through the existing environment loading (a ``kv:`` value in a
    ``.env`` file or the ``env_akv_ref`` bootstrap document).
    """

    model_config = ConfigDict(extra="forbid")

    env_var: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Name of the server environment variable that holds the value",
    )


class InstanceRecipe(BaseModel):
    """
    The saved recipe for rebuilding one named target, converter, or scorer.

    ``params`` holds constructor arguments as the API received them: plain values,
    registry names for references to other instances, and data URIs for uploaded
    files. ``credentials`` holds environment-variable references only.
    ``schema_version`` lets a later release recognize, and upgrade, recipes it
    wrote earlier.
    """

    model_config = ConfigDict(extra="forbid")

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1
    PERSISTED_KINDS: ClassVar[frozenset[ComponentType]] = frozenset(
        {ComponentType.TARGET, ComponentType.CONVERTER, ComponentType.SCORER}
    )

    schema_version: int = Field(1, description="Format version of the saved recipe")
    kind: ComponentType = Field(..., description="Registry the instance belongs to")
    name: str = Field(..., pattern=INSTANCE_NAME_PATTERN, description="Registry name of the instance")
    type: str = Field(..., min_length=1, description="Registered class name to build")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Constructor arguments")
    credentials: dict[str, CredentialReference] = Field(
        default_factory=dict, description="Constructor arguments read from environment variables"
    )
    auth_mode: Literal["api_key", "identity"] | None = Field(None, description="Target authentication mode")

    @model_validator(mode="after")
    def _validate_kind(self) -> "InstanceRecipe":
        """
        Reject kinds that are not persisted and an authentication mode on non-targets.

        Returns:
            InstanceRecipe: The validated recipe.

        Raises:
            ValueError: If the format version is not the current one, the kind is not a
                target, converter, or scorer, or a non-target recipe carries an
                authentication mode.
        """
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported recipe format {self.schema_version}.")
        if self.kind not in self.PERSISTED_KINDS:
            raise ValueError(f"Instances of kind '{self.kind.value}' are not persisted.")
        if self.auth_mode is not None and self.kind is not ComponentType.TARGET:
            raise ValueError("Only target recipes carry an authentication mode.")
        return self


class StoredInstanceRecipe(BaseModel):
    """A saved recipe paired with the version of the document it was read from."""

    recipe: InstanceRecipe = Field(..., description="The saved recipe")
    version: str = Field(..., description="Opaque version of the saved document; required to change it")
    content: bytes | None = Field(
        None,
        exclude=True,
        repr=False,
        description="The saved document as stored, so a failed replacement can put back exactly what it replaced",
    )


class UnrestorableInstance(BaseModel):
    """A saved instance that could not be rebuilt, and the reason."""

    kind: ComponentType = Field(..., description="Registry the saved instance belongs to")
    name: str = Field(..., description="Registry name of the saved instance")
    type: str | None = Field(None, description="Registered class name, when the saved document could be read")
    reason: str = Field(..., description="Why the instance could not be rebuilt")
    version: str | None = Field(None, description="Version of the saved document, for repairing or deleting it")
    content: bytes | None = Field(
        None,
        exclude=True,
        repr=False,
        description="The saved document, when it is not a usable recipe, so the names it holds can still be found",
    )
