# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Build saved registry entries through the API's own create for each kind."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.backend.models import CreateConverterRequest, CreateScorerRequest, CreateTargetRequest
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.backend.services.target_service import get_target_service
from pyrit.backend.services.technique_service import get_technique_service
from pyrit.models.catalog import RegistryRecipeKind, RestoreFailureReason, TechniqueRecipe
from pyrit.registry import ConverterRegistry
from pyrit.registry.registry_recipe_restore import RecipeRejectedError

if TYPE_CHECKING:
    from pyrit.models.catalog import StoredRecipe


async def build_saved_entry_async(stored: StoredRecipe, *, upload_directory: str | None) -> None:
    """
    Build one saved entry with the create the API uses for its kind, and register it under its name.

    The entry passes the same checks a create request does. Restore runs as the server and can't tell who
    wrote a saved file, so a target is built as an administrator would build it. A target that
    uploads files gets this server's upload folder, as it does through the API.

    Args:
        stored (StoredRecipe): The checked recipe to build.
        upload_directory (str | None): The folder this server lets targets upload files from, or None.

    Raises:
        RecipeRejectedError: If the recipe doesn't make a valid create request.
    """
    request = _create_request(stored)
    if isinstance(request, CreateTechniqueRequest):
        await get_technique_service().create_async(request)
    elif isinstance(request, CreateTargetRequest):
        await get_target_service().create_target_async(
            request=request, upload_directory=upload_directory, is_admin=True
        )
    elif isinstance(request, CreateConverterRequest):
        await get_converter_service().create_converter_async(request=request)
    else:
        await get_scorer_service().create_scorer_async(request=request)


def _create_request(
    stored: StoredRecipe,
) -> CreateTargetRequest | CreateConverterRequest | CreateScorerRequest | CreateTechniqueRequest:
    """
    Turn a saved recipe back into the create request it was saved from.

    Returns:
        CreateTargetRequest | CreateConverterRequest | CreateScorerRequest | CreateTechniqueRequest: The request.

    Raises:
        RecipeRejectedError: If the recipe doesn't make a valid request, such as a technique with a reserved name.
    """
    recipe = stored.recipe
    fields: dict[str, Any] = {"name": stored.ref.name}
    try:
        if isinstance(recipe, TechniqueRecipe):
            return CreateTechniqueRequest.model_validate(
                fields | recipe.model_dump(exclude_unset=True, exclude={"schema_version"})
            )
        fields |= {"type": recipe.class_name, "params": recipe.params}
        if stored.ref.kind is RegistryRecipeKind.TARGETS:
            if recipe.auth_mode is not None:
                fields["auth_mode"] = recipe.auth_mode
            return CreateTargetRequest.model_validate(fields)
        if stored.ref.kind is RegistryRecipeKind.CONVERTERS:
            if recipe.source is not None:
                fields |= {"params": {}, "source": _copy_source(name=recipe.source, params=recipe.params)}
            return CreateConverterRequest.model_validate(fields)
        return CreateScorerRequest.model_validate(fields)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, issue['loc'])) or 'request'}: {issue['msg']}" for issue in error.errors()
        )
        raise RecipeRejectedError(
            reason=RestoreFailureReason.INVALID_RECIPE, message=f"Not a valid create request: {problems}"
        ) from error


def _copy_source(*, name: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Name the converter a saved copy is rebuilt from by the identity it has now, as a copy request does.

    Returns:
        dict[str, Any]: The request's ``source``, with the copy's saved changes as its overrides.

    Raises:
        RecipeRejectedError: If the copied converter isn't registered.
    """
    source = ConverterRegistry.get_registry_singleton().instances.get(name)
    if source is None:
        raise RecipeRejectedError(
            reason=RestoreFailureReason.MISSING_DEPENDENCY, message=f"Copies converter '{name}', which doesn't exist."
        )
    return {"source_name": name, "source_hash": source.get_identifier().hash, "params": params}
