# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target service for managing target instances.

Handles creation and retrieval of target instances.
Uses TargetRegistry as the source of truth for instances.

Targets can be:
- Created via API request (instantiated from request params, then registered)
- Retrieved from registry (pre-registered at startup or created earlier)
"""

import asyncio
import logging
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from weakref import WeakKeyDictionary

from pyrit.backend.mappers import target_object_to_instance
from pyrit.backend.models import (
    CreateTargetRequest,
    PaginationInfo,
    TargetListResponse,
)
from pyrit.backend.models.targets import (
    ServerCredentialChoice,
    TargetTypeEntry,
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.services.component_lifecycle import construct_component_async, release_component_async
from pyrit.common import AUTH_MODES, REQUIRED_VALUE, AuthMode, default_values
from pyrit.models import ComponentType, Parameter, PromptDataType, TargetCapabilities, TargetIdentifier
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RestoreFailureReason,
    SavedEntryOwner,
    ServerCredential,
    TargetInstance,
)
from pyrit.models.component_spec import SourceInstanceSpec, TargetBinding
from pyrit.prompt_target import (
    GitHubCopilotTarget,
    PromptTarget,
    RoundRobinTarget,
    discover_target_capabilities_async,
)
from pyrit.prompt_target.common.discover_target_capabilities import (
    DEFAULT_TEST_ASSETS,
    CapabilityDiscoveryError,
)
from pyrit.prompt_target.common.tool_call_history import TOOL_CALL_INPUT_MODALITIES
from pyrit.registry import TargetRegistry
from pyrit.registry.file_document_storage import DocumentConflictError, DocumentStorageUnavailableError
from pyrit.registry.instance_registry import SAVED_ENTRY_METADATA_KEY, RegistryNameTakenError, registry_entry_origin
from pyrit.registry.registry_recipe_rules import (
    SavedEntryRefusedError,
    check_saveable_class,
    check_saved_entry_params,
    resolve_listed_credential,
)
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

logger = logging.getLogger(__name__)

_ENV_BACKED_REQUIRED_PARAMETERS: dict[str, frozenset[str]] = {
    "OpenAITarget": frozenset({"endpoint", "model_name"}),
    "AzureBlobStorageTarget": frozenset({"container_url"}),
    "AzureMLChatTarget": frozenset({"endpoint"}),
    "HackAPromptTarget": frozenset({"cookie", "session_id"}),
    "HuggingFaceChatTarget": frozenset({"hf_access_token"}),
    "PromptShieldTarget": frozenset({"endpoint"}),
}

# Constructor parameter through which a target accepts an explicit auth mode.
_AUTH_MODE_PARAM = "auth_mode"


class SavedTargetConflictError(ValueError):
    """A target can't be saved under a name that a registered or saved target already has."""


class SavedTargetRefusedError(ValueError):
    """A target can't be saved as asked, though it can still be created without saving it."""


class SavedTargetStorageError(RuntimeError):
    """Saved targets can't be written right now."""


def _launch_script_class_reason(target_type: str) -> str:
    """
    Explain why a target of a class that a scan's launch script added can't be saved.

    Returns:
        str: The reason.
    """
    return (
        f"'{target_type}' was added by a scan's launch script, so a saved target of it couldn't be restored "
        "after a restart."
    )


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth for class discovery,
    parameter coercion, reference resolution, and construction. Endpoint
    validation remains owned by the target classes.
    """

    DETECTION_TIMEOUT_SECONDS: float = 240.0

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()
        self._detecting: set[str] = set()
        self._detecting_sources: set[int] = set()
        self._legacy_binding_capabilities: WeakKeyDictionary[PromptTarget, TargetCapabilities] = WeakKeyDictionary()

    def saved_capabilities(self, *, target: PromptTarget, metadata: dict[str, Any]) -> TargetCapabilities:
        """
        Resolve a manual chat's snapshot, or its source's pre-detection session fallback.

        Returns:
            TargetCapabilities: Saved capabilities without changing the registered target.
        """
        binding = TargetBinding.from_metadata(metadata)
        if binding is not None and binding.capabilities is not None:
            return binding.capabilities
        saved = metadata.get("target_capabilities")
        if saved is not None:
            return TargetCapabilities.model_validate(saved)
        return self._legacy_binding_capabilities.get(target, target.capabilities)

    @contextmanager
    def capabilities_scope(
        self, *, target: PromptTarget | None, metadata: dict[str, Any]
    ) -> Generator[None, None, None]:
        """
        Preserve a manual chat's configuration on its original, caller-owned target.

        Yields:
            None: A task-local scope; the live target is neither mutated nor released.
        """
        if target is None:
            yield
            return
        capabilities = self.saved_capabilities(target=target, metadata=metadata)
        with target.capabilities_scope(capabilities):
            yield

    async def detect_capabilities_async(self, *, target_registry_name: str, expected_hash: str) -> TargetInstance:
        """
        Probe private targets and apply a completed result to the selected source.

        Round-robin detection probes each child independently and combines their
        results. Only the selected parent is updated; live children are not changed.

        Returns:
            TargetInstance: The updated live target.

        Raises:
            LookupError: The target is not registered.
            FileExistsError: Discovery is already running or the source changed.
            ValueError: The target cannot be probed safely.
            TimeoutError: Discovery exceeded its operation deadline.
        """
        target = self.get_target_object(target_registry_name=target_registry_name)
        if target is None:
            raise LookupError("Target not found")
        if target.get_identifier().hash != expected_hash:
            raise FileExistsError("The target changed. Refresh the target registry.")
        if getattr(target, "inner_targets", None) and not isinstance(target, RoundRobinTarget):
            raise ValueError("Capability detection is not supported for this composite target type.")
        sources = target.inner_targets if isinstance(target, RoundRobinTarget) else [target]
        snapshots = [(source, source.configuration) for source in [target, *sources]]
        source_ids = {id(source) for source, _ in snapshots}
        if target_registry_name in self._detecting or source_ids & self._detecting_sources:
            raise FileExistsError("Capability detection is already running for this target or one of its children.")
        self._detecting.add(target_registry_name)
        self._detecting_sources.update(source_ids)
        try:
            async with asyncio.timeout(self.DETECTION_TIMEOUT_SECONDS):
                results: list[TargetCapabilities] = []
                for index, source in enumerate(sources):
                    try:
                        results.append(await self._probe_capabilities_async(source))
                    except (CapabilityDiscoveryError, ValueError) as exc:
                        if isinstance(target, RoundRobinTarget):
                            error_type = (
                                CapabilityDiscoveryError if isinstance(exc, CapabilityDiscoveryError) else ValueError
                            )
                            raise error_type(f"Child {index + 1} ({type(source).__name__}): {exc}") from exc
                        raise
                capabilities = (
                    target.aggregate_capabilities(child_capabilities=results)
                    if isinstance(target, RoundRobinTarget)
                    else results[0]
                )
                if (
                    self.get_target_object(target_registry_name=target_registry_name) is not target
                    or any(source.configuration is not configuration for source, configuration in snapshots)
                    or target.get_identifier().hash != expected_hash
                    or (isinstance(target, RoundRobinTarget) and target.inner_targets != sources)
                ):
                    raise FileExistsError(
                        "The target changed during capability detection. Refresh the target registry."
                    )
                self._legacy_binding_capabilities.setdefault(target, target.capabilities)
                target.apply_capabilities(capabilities=capabilities)
                return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target)
        finally:
            self._detecting.discard(target_registry_name)
            self._detecting_sources.difference_update(source_ids)

    async def _probe_capabilities_async(self, target: PromptTarget) -> TargetCapabilities:
        """
        Check one private target and release its resources.

        Returns:
            TargetCapabilities: Completed capability results.
        """
        probe = await construct_component_async(self._registry.recreate_instance, source=target, params={})
        try:
            probe.apply_capabilities(capabilities=target.capabilities)
            modalities: set[frozenset[PromptDataType]] = {frozenset({"text"})}
            for modality in DEFAULT_TEST_ASSETS:
                modalities.update({frozenset({modality}), frozenset({"text", modality})})
            modalities.update(TOOL_CALL_INPUT_MODALITIES)
            return await discover_target_capabilities_async(
                target=probe, test_modalities=modalities, raise_on_error=True
            )
        finally:
            await release_component_async(probe)

    def _build_instance_from_object(
        self, *, target_registry_name: str, target_obj: Any, origin: RegistryEntryOrigin | None = None
    ) -> TargetInstance:
        """
        Build a TargetInstance from a registry object.

        Returns:
            TargetInstance with metadata derived from the object.
        """
        return target_object_to_instance(target_registry_name, target_obj, origin=origin)

    async def list_targets_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TargetListResponse:
        """
        List all target instances with pagination.

        Args:
            limit: Maximum items to return.
            cursor: Pagination cursor (target_registry_name to start after).

        Returns:
            TargetListResponse containing paginated targets.
        """
        items = [
            self._build_instance_from_object(
                target_registry_name=entry.name, target_obj=entry.instance, origin=registry_entry_origin(entry.metadata)
            )
            for entry in self._registry.instances.get_all_instances()
        ]
        page, has_more = self._paginate(items=items, cursor=cursor, limit=limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    @staticmethod
    def _paginate(*, items: list[TargetInstance], cursor: str | None, limit: int) -> tuple[list[TargetInstance], bool]:
        """
        Apply cursor-based pagination.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.target_registry_name == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more

    async def get_target_async(self, *, target_registry_name: str) -> TargetInstance | None:
        """
        Get a target instance by registry name.

        Returns:
            TargetInstance if found, None otherwise.
        """
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return None
        return self._build_instance_from_object(
            target_registry_name=target_registry_name,
            target_obj=entry.instance,
            origin=registry_entry_origin(entry.metadata),
        )

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.instances.get(target_registry_name)

    def get_target_object_by_identifier_hash(self, identifier_hash: str) -> PromptTarget | None:
        """
        Resolve a legacy saved target only when its registry identity is unambiguous.

        Returns:
            PromptTarget | None: The sole matching registry entry, or None when missing or ambiguous.
        """
        matches = [
            entry.instance
            for entry in self._registry.instances.get_all_instances()
            if entry.instance.get_identifier().hash == identifier_hash
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _get_supported_auth_modes(auth_modes: tuple[str, ...]) -> list[AuthMode]:
        """
        Validate and narrow registry authentication modes for the type response.

        Args:
            auth_modes (tuple[str, ...]): Authentication modes declared by a target class.

        Returns:
            list[AuthMode]: Validated authentication modes.

        Raises:
            ValueError: If a target class declares an unsupported authentication mode.
        """
        supported_auth_modes: list[AuthMode] = []
        for auth_mode in auth_modes:
            if auth_mode not in AUTH_MODES:
                raise ValueError(f"Unsupported target authentication mode: {auth_mode!r}")
            supported_auth_modes.append(auth_mode)
        return supported_auth_modes

    @staticmethod
    def _allowed_path_params(*, target_cls: type[PromptTarget]) -> frozenset[str]:
        """
        Return the path parameters a target built from API input may receive.

        These are the path parameters callers may set, plus the upload folder of a
        target that uploads local files, which only the server sets.

        Args:
            target_cls (type[PromptTarget]): Target class being described or built.

        Returns:
            frozenset[str]: Path parameter names the target may receive.
        """
        upload_directory = target_cls.upload_directory_parameter
        if upload_directory is None:
            return target_cls.caller_path_parameters
        return target_cls.caller_path_parameters | {upload_directory}

    def _project_target_parameters(self, *, target_type: str, parameters: tuple[Parameter, ...]) -> list[Parameter]:
        """
        Project registry parameters into the API contract.

        Environment-backed values remain optional in Python constructors so targets
        can resolve them from dotenv configuration. The GUI must still collect them
        explicitly, so the API marks those values required without changing the
        target constructor signatures. The upload folder of a target that uploads
        local files is set by the server, so it is left out, and so are parameters
        that take Python objects and path parameters callers may not set.

        Args:
            target_type (str): Registered target class name.
            parameters (tuple[Parameter, ...]): Constructor parameters derived by the registry.

        Returns:
            list[Parameter]: Parameters projected for dynamic form generation.
        """
        target_cls = self._registry.get_class(target_type)
        required_names = frozenset().union(
            *(_ENV_BACKED_REQUIRED_PARAMETERS.get(base.__name__, frozenset()) for base in target_cls.__mro__)
        )
        return [
            parameter.model_copy(update={"default": REQUIRED_VALUE}) if parameter.name in required_names else parameter
            for parameter in parameters
            if parameter.name != target_cls.upload_directory_parameter
            and parameter.is_allowed_external_input(allowed_path_params=target_cls.caller_path_parameters)
        ]

    async def list_target_types_async(
        self, *, upload_directory: str | None = None, server_credentials: Sequence[ServerCredential] = ()
    ) -> TargetTypeResponse:
        """
        List all available target types from the target class registry.

        Returns every target that external callers can build, with the
        constructor parameters they may supply, each described in the form callers
        send it, the auth modes it supports, and the ones a saved target of it can use
        (or why none can), all projected from the registry's ``TargetMetadata`` and the
        target class; targets that need a Python object or a path callers may not
        set for a required parameter are left out, and so are targets that upload local
        files when the server has no upload folder. Deciding which entries to surface to a
        user is a presentation concern owned by the caller (e.g. the frontend),
        not this service.

        Args:
            upload_directory (str | None): Folder the server lets API-created targets upload files
                from, or None when the server configures none.
            server_credentials (Sequence[ServerCredential]): The server credentials an administrator lets
                users pick; each type that can use one lists them by name and endpoint. Defaults to none.

        Returns:
            TargetTypeResponse containing all available target classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items: list[TargetTypeEntry] = []
        for metadata in metadata_items:
            if not (
                self._has_required_params(target_type=metadata.class_name, parameters=metadata.parameters)
                and self._has_upload_directory(target_type=metadata.class_name, upload_directory=upload_directory)
            ):
                continue
            saveable_auth_modes, not_saveable_reason = self._get_saveability(target_type=metadata.class_name)
            items.append(
                TargetTypeEntry(
                    target_type=metadata.class_name,
                    parameters=[
                        parameter.for_external_catalog()
                        for parameter in self._project_target_parameters(
                            target_type=metadata.class_name,
                            parameters=metadata.parameters,
                        )
                    ],
                    supported_auth_modes=self._get_supported_auth_modes(metadata.supported_auth_modes),
                    saveable_auth_modes=saveable_auth_modes,
                    not_saveable_reason=not_saveable_reason,
                    description=metadata.class_description or None,
                    server_credentials=[
                        ServerCredentialChoice(name=credential.name, endpoint=credential.endpoint)
                        for credential in server_credentials
                    ]
                    if self._registry.get_class(metadata.class_name).listed_credential_parameters
                    else [],
                )
            )
        return TargetTypeResponse(items=items)

    def _get_saveability(self, *, target_type: str) -> tuple[list[AuthMode], str | None]:
        """
        Return the auth modes a saved target of a type can use, and why it can't be saved when none can.

        Returns:
            tuple[list[AuthMode], str | None]: The saveable auth modes, and the reason when there are none.
        """
        if self._registry.is_temporary_class(target_type):
            return [], _launch_script_class_reason(target_type)
        try:
            auth_modes = check_saveable_class(self._registry.get_class(target_type))
        except SavedEntryRefusedError as error:
            return [], str(error)
        return self._get_supported_auth_modes(auth_modes), None

    async def build_from_source_async(self, spec: SourceInstanceSpec) -> PromptTarget:
        """
        Rebuild a private target and verify its saved effective identity.

        Overrides may set the target's caller path parameters, as on create, but not
        the upload folder, which only the server sets.

        Returns:
            PromptTarget: The caller-owned target.
        """
        source = self._registry.resolve_source(name=spec.source_name, identifier_hash=spec.source_hash)
        target = self._registry.recreate_instance(
            source=source,
            params=spec.params,
            external_input=True,
            allowed_path_params=type(source).caller_path_parameters,
        )
        if spec.effective_hash:
            legacy_capabilities = self._legacy_binding_capabilities.get(source)
            if legacy_capabilities is not None:
                target.apply_capabilities(capabilities=legacy_capabilities)
            if target.get_identifier().hash != spec.effective_hash:
                await release_component_async(target)
                raise ValueError("The saved target configuration cannot be reconstructed")
        return target

    async def resolve_binding_async(self, binding: TargetBinding) -> PromptTarget:
        """
        Rebuild an attack-owned target without changing its saved capabilities.

        Returns:
            PromptTarget: The caller-owned target.
        """
        source = self._registry.resolve_source(name=binding.source_name, identifier_hash=binding.source_hash)
        capabilities = binding.capabilities or self._legacy_binding_capabilities.get(source, source.capabilities)
        target = await self.build_from_source_async(binding.to_spec())
        target.apply_capabilities(capabilities=capabilities)
        return target

    def _has_required_params(self, *, target_type: str, parameters: tuple[Parameter, ...]) -> bool:
        """
        Return whether a target type can receive every required parameter when built from API input.

        Args:
            target_type (str): Registered target class name.
            parameters (tuple[Parameter, ...]): Constructor parameters derived by the registry.

        Returns:
            bool: False when a required parameter needs a Python object or a path callers may not set.
        """
        allowed_path_params = self._allowed_path_params(target_cls=self._registry.get_class(target_type))
        return all(
            parameter.is_allowed_external_input(allowed_path_params=allowed_path_params)
            for parameter in parameters
            if parameter.required
        )

    def _has_upload_directory(self, *, target_type: str, upload_directory: str | None) -> bool:
        """
        Return whether a target type uploads no local files or the server gives it an upload folder.

        Args:
            target_type (str): Registered target class name.
            upload_directory (str | None): Folder the server configured for uploads, if any.

        Returns:
            bool: False when the type uploads local files and the server configures no folder.
        """
        return upload_directory is not None or self._registry.get_class(target_type).upload_directory_parameter is None

    @staticmethod
    def _apply_upload_directory(
        *,
        target_type: str,
        target_cls: type[PromptTarget],
        params: dict[str, Any],
        upload_directory: str | None,
    ) -> None:
        """
        Give a target that uploads local files the server's upload folder.

        Args:
            target_type (str): Registered target class name.
            target_cls (type[PromptTarget]): Target class being created.
            params (dict[str, Any]): Constructor parameters, updated in place.
            upload_directory (str | None): Folder the server configured for uploads, if any.

        Raises:
            ValueError: If the caller supplied the upload folder, or the server configures none.
        """
        parameter_name = target_cls.upload_directory_parameter
        if parameter_name is None:
            return
        if parameter_name in params:
            raise ValueError(
                f"Parameter '{parameter_name}' of '{target_type}' is set by the server and cannot be supplied."
            )
        if upload_directory is None:
            raise ValueError(
                f"Target type '{target_type}' uploads files from the server. Set target_upload_directory in the "
                "server configuration to create it through the API, or register it in Python or with an initializer."
            )
        params[parameter_name] = upload_directory

    async def create_target_async(
        self,
        *,
        request: CreateTargetRequest,
        upload_directory: str | None = None,
        is_admin: bool = False,
        owner: SavedEntryOwner | None = None,
        recipes_source: str | None = None,
        server_credentials: Sequence[ServerCredential] = (),
    ) -> TargetInstance | UnregisteredTarget:
        """
        Create a new target instance from API request.

        Class discovery, strict parameter validation, scalar coercion, registry
        reference resolution, and construction are owned by the
        ``TargetRegistry``. Endpoint trust and identity token minting are owned
        by the target classes themselves. This service enforces the
        request-level contract: it rejects an ``auth_mode`` smuggled through
        ``params``, and for ``identity`` it confirms the target supports it and
        omits the api_key plus any registry-flagged identity-conflicting
        parameters so the target validates its own endpoint and authenticates
        itself. For ``none`` it likewise confirms support and omits the api_key.
        The request-level ``auth_mode`` is authoritative and is forwarded
        via ``get_auth_mode_parameters`` to targets that accept it, so the choice
        is explicit rather than inferred from a missing key. A target that uploads
        local files gets the server's upload folder, which callers cannot choose.
        GitHub Copilot requires an explicit or environment token rather than SDK
        login discovery in CoPyRIT. A target whose construction runs model
        repository code, or that would authenticate with this server's stored
        credentials, is built only for an administrator. The response is built
        before the target is registered, so a failed request leaves no registered
        target. With ``persist``, the target is saved so it is restored at every
        start; otherwise it is Temporary.

        Args:
            request: The create target request with type, params, and auth_mode.
            upload_directory: Folder the server lets API-created targets upload files from,
                or None when the server configures none.
            is_admin: Whether the caller may create a target that runs model repository code or
                authenticates with this server's stored credentials.
            owner: The signed-in user, recorded on a saved target, or None when sign-in is off.
            recipes_source: Where saved entries are stored, or None for the default folder.
            server_credentials: The server credentials an administrator lets users pick.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: If the target type is not registered, ``params`` carries an
                ``auth_mode`` that conflicts with the request-level choice, identity or
                no-key auth is requested but unsupported by the target type, or a target that
                uploads local files is requested with its upload folder or while
                the server configures none. Construction errors (unknown params,
                incompatible inner targets, unrecognized identity endpoints) are
                raised by the registry / target classes.
            PermissionError: If a non-administrator enables a target's model code execution, or
                creates a target that would authenticate with this server's stored credentials.
            SavedTargetRefusedError: If a target to save would hold or use a credential, or names a
                Temporary target.
            SavedTargetConflictError: If a target to save has the name of a registered or saved target.
            SavedTargetStorageError: If a target to save can't be written to storage.
        """
        if request.type not in self._registry:
            raise ValueError(
                f"Target type '{request.type}' not found. Available types: {self._registry.get_class_names()}"
            )

        target_cls = self._registry.get_class(request.type)
        params: dict[str, Any] = dict(request.params)

        # auth_mode is also a constructor parameter, so the registry would otherwise accept it
        # inside params as a second, competing channel that bypasses the checks below.
        params_auth_mode = params.get(_AUTH_MODE_PARAM)
        if params_auth_mode is not None and params_auth_mode != request.auth_mode:
            raise ValueError(
                f"Conflicting authentication modes: request auth_mode is '{request.auth_mode}' but "
                f"params['{_AUTH_MODE_PARAM}'] is '{params_auth_mode}'. Set the request-level auth_mode only."
            )

        if request.auth_mode == "identity":
            if "identity" not in target_cls.supported_auth_modes:
                raise ValueError(f"Target type '{request.type}' does not support identity-based authentication.")
            # Omitting the key alone is ambiguous — every auth resolver reads the api-key env var
            # when no key is passed — so also state the choice explicitly where the target accepts it.
            params.pop("api_key", None)
            # Omit any other parameter the registry metadata marks as conflicting with
            # identity-based auth (e.g. AzureBlobStorageTarget's sas_token), so a caller
            # can't silently override the selected auth mode by also supplying it.
            metadata = await asyncio.to_thread(self._registry.get_registered_class_metadata, request.type)
            if metadata is not None:
                for parameter in metadata.parameters:
                    if parameter.identity_conflicting:
                        params.pop(parameter.name, None)

        if request.auth_mode == "none":
            if "none" not in target_cls.supported_auth_modes:
                raise ValueError(f"Target type '{request.type}' does not support no-key authentication.")
            params.pop("api_key", None)

        params.update(target_cls.get_auth_mode_parameters(auth_mode=request.auth_mode))
        self._apply_upload_directory(
            target_type=request.type, target_cls=target_cls, params=params, upload_directory=upload_directory
        )

        if issubclass(target_cls, GitHubCopilotTarget):
            github_token = params.get("github_token")
            if github_token is None:
                github_token = default_values.get_non_required_value(
                    env_var_name=target_cls.GITHUB_TOKEN_ENVIRONMENT_VARIABLE
                )
            if not isinstance(github_token, str) or not github_token.strip():
                raise ValueError(
                    "GitHub Copilot requires a GitHub token in CoPyRIT. Enter GitHub Token or configure "
                    "GITHUB_TOKEN for this CoPyRIT instance through its environment or loaded .env/.env.local files."
                )

        permission_params = params
        if request.source is not None:
            source = self._registry.resolve_source(
                name=request.source.source_name, identifier_hash=request.source.source_hash
            )
            permission_params = {
                **self._registry.get_reconstruction_parameters(source),
                **request.source.params,
            }
        metadata = await asyncio.to_thread(self._registry.get_registered_class_metadata, request.type)
        if metadata is not None:
            missing: list[str] = []
            for parameter in self._project_target_parameters(target_type=request.type, parameters=metadata.parameters):
                value = permission_params.get(parameter.name)
                if parameter.required and (value is None or isinstance(value, str) and not value.strip()):
                    missing.append(parameter.name)
            if missing:
                raise ValueError(
                    f"Target type '{request.type}' requires explicit connection fields: {', '.join(missing)}. "
                    "Supply these fields explicitly, or select an environment-initialized registered target."
                )
        await self._enforce_code_execution_permission_async(
            target_type=request.type, params=permission_params, is_admin=is_admin
        )
        self._enforce_server_credential_permission(
            target_type=request.type, params=permission_params, is_admin=is_admin
        )
        listed: tuple[str, str] | None = None
        if request.server_credential is not None:
            try:
                listed = resolve_listed_credential(
                    cls=target_cls,
                    params=params,
                    name=request.server_credential,
                    server_credentials=server_credentials,
                )
            except SavedEntryRefusedError as error:
                raise ValueError(str(error)) from error
        if request.persist:
            params = await self._prepare_saved_params_async(
                target_type=request.type,
                target_cls=target_cls,
                params=params,
                auth_mode=request.auth_mode,
                listed_credential_parameter=listed[0] if listed is not None else None,
            )
        # The key reaches only the target being built; a saved recipe names the credential instead.
        build_params = {**params, listed[0]: listed[1]} if listed is not None else params

        # LEGACY COMPATIBILITY: Only API callers that leave out the name reach this fallback; the GUI sends one.
        # Remove it when the name becomes required for registered targets.
        target_registry_name = request.name or f"compat_{uuid.uuid4().hex}"
        if request.register:
            try:
                self._registry.instances.validate_name_available(target_registry_name)
            except RegistryNameTakenError as error:
                if request.persist:
                    raise SavedTargetConflictError(str(error)) from error
                raise
        if (
            request.source
            and type(
                self._registry.resolve_source(
                    name=request.source.source_name, identifier_hash=request.source.source_hash
                )
            )
            is not target_cls
        ):
            raise ValueError("The source target type does not match the requested type")
        target_obj = (
            await self.build_from_source_async(request.source)
            if request.source
            else self._registry.create_instance_from_external_input(
                request.type,
                params=build_params,
                allowed_path_params=self._allowed_path_params(target_cls=target_cls),
            )
        )
        if not request.register:
            try:
                return UnregisteredTarget(
                    identifier=TargetIdentifier.from_component_identifier(target_obj.get_identifier()),
                    capabilities=target_obj.capabilities,
                    generation_ranges=target_obj.generation_ranges,
                )
            finally:
                await release_component_async(target_obj)
        try:
            if request.persist:
                return await self._save_and_register_async(
                    name=target_registry_name,
                    type_name=request.type,
                    target_cls=target_cls,
                    target_obj=target_obj,
                    auth_mode=request.auth_mode,
                    params=params,
                    server_credential=request.server_credential,
                    owner=owner,
                    recipes_source=recipes_source,
                )
            target = self._build_instance_from_object(
                target_registry_name=target_registry_name, target_obj=target_obj, origin=RegistryEntryOrigin.TEMPORARY
            )
            self._registry.instances.register(target_obj, name=target_registry_name)
            return target
        except Exception:
            await release_component_async(target_obj)
            raise

    async def _prepare_saved_params_async(
        self,
        *,
        target_type: str,
        target_cls: type[PromptTarget],
        params: dict[str, Any],
        auth_mode: AuthMode,
        listed_credential_parameter: str | None = None,
    ) -> dict[str, Any]:
        """
        Turn off the server values a target to save would read, then refuse what it still can't hold.

        Returns:
            dict[str, Any]: The parameters to build and save the target with.

        Raises:
            SavedTargetRefusedError: If the target would hold or use a credential, its class was added by a scan's
                launch script, or it names a Temporary target.
            ValueError: If an address the target would use isn't a full http(s) or ws(s) address.
        """
        saved_params = dict(params)
        for name, turned_off in target_cls.server_fallback_parameters.items():
            if turned_off is not None and saved_params.get(name) in (None, ""):
                saved_params[name] = turned_off
        if self._registry.is_temporary_class(target_type):
            raise SavedTargetRefusedError(
                f"{_launch_script_class_reason(target_type)} It can still be created without saving it."
            )
        metadata = await asyncio.to_thread(self._registry.get_registered_class_metadata, target_type)
        if metadata is None:
            # Restore falls back to the class itself the same way, so saving never checks less than restoring.
            metadata = await asyncio.to_thread(self._registry.get_class_metadata, target_cls)
        parameters = metadata.parameters
        try:
            check_saved_entry_params(
                cls=target_cls,
                parameters=parameters,
                params=saved_params,
                auth_mode=auth_mode,
                listed_credential_parameter=listed_credential_parameter,
            )
        except SavedEntryRefusedError as error:
            # The address needs fixing; creating the target without saving it wouldn't make it usable.
            if error.reason is RestoreFailureReason.INVALID_RECIPE:
                raise ValueError(str(error)) from error
            raise SavedTargetRefusedError(f"{error} It can still be created without saving it.") from error
        self._refuse_temporary_references(parameters=parameters, params=saved_params)
        return saved_params

    def _refuse_temporary_references(self, *, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
        """
        Refuse a target to save that uses a Temporary target, which won't exist after a restart.

        Raises:
            SavedTargetRefusedError: If a target parameter names a Temporary target.
        """
        for parameter in parameters:
            if parameter.reference is None or parameter.reference.component_type is not ComponentType.TARGET:
                continue
            value = params.get(parameter.name)
            for name in value if isinstance(value, list) else [value]:
                entry = self._registry.instances.get_entry(name) if isinstance(name, str) else None
                if entry is not None and registry_entry_origin(entry.metadata) is RegistryEntryOrigin.TEMPORARY:
                    raise SavedTargetRefusedError(
                        f"Uses temporary target '{name}', which won't exist after a restart. "
                        "It can still be created without saving it."
                    )

    async def _save_and_register_async(
        self,
        *,
        name: str,
        type_name: str,
        target_cls: type[PromptTarget],
        target_obj: PromptTarget,
        auth_mode: AuthMode,
        params: dict[str, Any],
        server_credential: str | None,
        owner: SavedEntryOwner | None,
        recipes_source: str | None,
    ) -> TargetInstance:
        """
        Save the target's recipe, then register the target as a saved entry.

        The recipe is written only if no saved target has the name, so of two saves under one name
        only one succeeds. If registering then fails, the recipe is deleted again. The recipe names
        the class by the registry name the request used, which restore looks up, and keeps the auth
        mode apart from the parameters, as the create request does.

        Returns:
            TargetInstance: The saved target.

        Raises:
            SavedTargetConflictError: If a saved or registered target already has the name.
            SavedTargetStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """
        target = self._build_instance_from_object(
            target_registry_name=name, target_obj=target_obj, origin=RegistryEntryOrigin.SAVED
        )
        auth_parameters = target_cls.get_auth_mode_parameters(auth_mode=auth_mode)
        recipe = ComponentRecipe(
            class_module=target_cls.__module__,
            class_name=type_name,
            auth_mode=auth_mode,
            params={key: value for key, value in params.items() if key not in auth_parameters},
            server_credential=server_credential,
            owner=owner,
        )
        storage = RegistryRecipeStorage(kind=RegistryRecipeKind.TARGETS, source=recipes_source)
        try:
            await asyncio.to_thread(storage.create_recipe, name=name, recipe=recipe)
        except DocumentConflictError as error:
            raise SavedTargetConflictError(f"A saved target named '{name}' already exists.") from error
        except DocumentStorageUnavailableError as error:
            logger.error(
                "Saved target '%s' couldn't be written to %s (%s). If the write reached storage anyway, the "
                "target is restored at the next start.",
                name,
                storage.display_source,
                error.reason,
            )
            raise SavedTargetStorageError("Saved targets can't be written right now.") from error
        try:
            self._registry.instances.register(
                target_obj, name=name, metadata={SAVED_ENTRY_METADATA_KEY: RegistryRecipeKind.TARGETS.value}
            )
        except Exception as error:
            if not await self._delete_recipe_async(storage=storage, name=name):
                raise SavedTargetStorageError("Saved targets can't be written right now.") from error
            if isinstance(error, RegistryNameTakenError):
                raise SavedTargetConflictError(str(error)) from error
            raise
        return target

    @staticmethod
    async def _delete_recipe_async(*, storage: RegistryRecipeStorage, name: str) -> bool:
        """
        Delete a recipe whose target couldn't be registered.

        Returns:
            bool: False if the recipe couldn't be deleted, which is logged for an operator to fix.
        """
        try:
            await asyncio.to_thread(storage.delete_recipe, name)
        except Exception as error:
            logger.error(
                "Saved target '%s' couldn't be registered, and its saved recipe in %s couldn't be deleted (%s). "
                "Delete it there, or the target is restored at the next start.",
                name,
                storage.display_source,
                error.reason if isinstance(error, DocumentStorageUnavailableError) else type(error).__name__,
            )
            return False
        return True

    async def _enforce_code_execution_permission_async(
        self, *, target_type: str, params: dict[str, Any], is_admin: bool
    ) -> None:
        """
        Require administrator access when a target's model-code-execution parameter resolves true.

        A target names the boolean parameter that runs model repository code through
        ``code_execution_parameter``. The supplied value is coerced through the registry (so the
        textual boolean forms the catalog accepts are honored) and written back, so the permission
        check and the constructor agree and a value the registry cannot interpret as a boolean is
        rejected before construction rather than reaching the loader as a truthy non-boolean.

        Args:
            target_type (str): Registered target class name.
            params (dict[str, Any]): Constructor parameters, updated in place with the coerced value.
            is_admin (bool): Whether the caller may enable model code execution.

        Raises:
            ValueError: If the parameter value cannot be coerced to a boolean, or the target names a
                model-code-execution parameter that the registry does not expose (so the check would
                otherwise be skipped silently).
            PermissionError: If the parameter resolves true and the caller is not an administrator.
        """
        parameter_name = self._registry.get_class(target_type).code_execution_parameter
        if parameter_name is None:
            return
        metadata = await asyncio.to_thread(self._registry.get_registered_class_metadata, target_type)
        parameter = next((p for p in (metadata.parameters if metadata else ()) if p.name == parameter_name), None)
        if parameter is None:
            raise ValueError(
                f"Target type '{target_type}' names '{parameter_name}' as its model-code-execution parameter, "
                "but the registry does not expose it, so the administrator requirement cannot be enforced."
            )
        if parameter_name in params:
            params[parameter_name] = parameter.coerce_value(params[parameter_name])
            enabled = bool(params[parameter_name])
        else:
            enabled = bool(parameter.default)
        if enabled and not is_admin:
            raise PermissionError(
                f"Creating '{target_type}' with '{parameter_name}' enabled runs model repository code and "
                "requires administrator access."
            )

    def _enforce_server_credential_permission(
        self, *, target_type: str, params: dict[str, Any], is_admin: bool
    ) -> None:
        """
        Require administrator access when a target would authenticate with the server's own credentials.

        A target names the parameter a caller supplies to authenticate with their own credential
        through ``server_credential_parameter`` (e.g. ``"sas_token"``). When the caller does not
        supply it, the target falls back to the server's stored credentials (an ambient managed
        identity or a server-configured token), so a non-administrator could otherwise point those
        credentials at a resource of their choice. The check runs after identity-mode normalization
        has removed any conflicting credential, so an identity-mode request is covered too.

        Args:
            target_type (str): Registered target class name.
            params (dict[str, Any]): Constructor parameters after auth-mode normalization.
            is_admin (bool): Whether the caller may use the server's stored credentials.

        Raises:
            PermissionError: If the caller supplied no credential and is not an administrator.
        """
        parameter_name = self._registry.get_class(target_type).server_credential_parameter
        if parameter_name is None:
            return
        if not params.get(parameter_name) and not is_admin:
            raise PermissionError(
                f"Creating '{target_type}' without '{parameter_name}' authenticates with this server's stored "
                f"credentials and requires administrator access. Create it with 'api_key' authentication and your "
                f"own '{parameter_name}', or ask an administrator."
            )


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
