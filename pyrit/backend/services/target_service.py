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
from functools import lru_cache
from typing import Any

from pyrit.auth import CallerCredentialStore, create_caller_credential_provider
from pyrit.backend.mappers import target_object_to_instance
from pyrit.backend.models import (
    CreateTargetRequest,
    PaginationInfo,
    TargetListResponse,
)
from pyrit.backend.models.targets import (
    TargetTypeEntry,
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.services.component_lifecycle import release_component_async
from pyrit.common import AUTH_MODES, REQUIRED_VALUE, AuthMode, default_values
from pyrit.models import Parameter, TargetIdentifier
from pyrit.models.catalog import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec, TargetBinding
from pyrit.prompt_target import GitHubCopilotTarget, PromptTarget
from pyrit.registry import RegistryEntry, TargetRegistry

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


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth for class discovery,
    parameter coercion, reference resolution, and construction. Endpoint
    validation remains owned by the target classes.

    A registered target created with ``requires_caller_credential`` holds no secret. A
    submitted key is stored for its caller, and the target gets a provider that returns the
    current caller's own credential on every request, so every other caller must set their
    own before using it. Targets created without that option stay shared.
    """

    CREATED_BY_METADATA_KEY = "created_by"
    CALLER_CREDENTIAL_ID_METADATA_KEY = "caller_credential_id"

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()
        self._credentials = get_caller_credential_store()

    def _build_instance_from_object(self, *, target_registry_name: str, target_obj: Any) -> TargetInstance:
        """
        Build a TargetInstance from a registry object.

        Returns:
            TargetInstance with metadata derived from the object.
        """
        return target_object_to_instance(target_registry_name, target_obj)

    def _build_instance_from_entry(self, *, entry: RegistryEntry[PromptTarget], caller: str | None) -> TargetInstance:
        """
        Build a TargetInstance that also reports the caller's credential and management access.

        Returns:
            TargetInstance: The registered target as seen by the caller.
        """
        instance = self._build_instance_from_object(target_registry_name=entry.name, target_obj=entry.instance)
        return self._with_caller_access(instance=instance, metadata=entry.metadata, caller=caller)

    def _with_caller_access(
        self, *, instance: TargetInstance, metadata: dict[str, Any], caller: str | None
    ) -> TargetInstance:
        """
        Add the caller's credential and management access to a target description.

        Returns:
            TargetInstance: A copy with caller-specific access fields set.
        """
        credential_id = metadata.get(self.CALLER_CREDENTIAL_ID_METADATA_KEY)
        configured = (
            caller is not None
            and credential_id is not None
            and self._credentials.has_credential(principal=caller, credential_id=credential_id)
        )
        return instance.model_copy(
            update={
                "requires_caller_credential": credential_id is not None,
                "caller_credential_configured": configured,
                "can_delete": caller is not None and metadata.get(self.CREATED_BY_METADATA_KEY) == caller,
            }
        )

    async def list_targets_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        caller: str | None = None,
    ) -> TargetListResponse:
        """
        List all target instances with pagination.

        Args:
            limit: Maximum items to return.
            cursor: Pagination cursor (target_registry_name to start after).
            caller: Principal of the requesting caller, used to report their credential and
                management access. Defaults to None.

        Returns:
            TargetListResponse containing paginated targets.
        """
        items = [
            self._build_instance_from_entry(entry=entry, caller=caller)
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

    async def get_target_async(self, *, target_registry_name: str, caller: str | None = None) -> TargetInstance | None:
        """
        Get a target instance by registry name.

        Args:
            target_registry_name: The registry name of the target.
            caller: Principal of the requesting caller, used to report their credential and
                management access. Defaults to None.

        Returns:
            TargetInstance if found, None otherwise.
        """
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return None
        return self._build_instance_from_entry(entry=entry, caller=caller)

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.instances.get(target_registry_name)

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

    async def list_target_types_async(self, *, upload_directory: str | None = None) -> TargetTypeResponse:
        """
        List all available target types from the target class registry.

        Returns every target that external callers can build, with the
        constructor parameters they may supply, each described in the form callers
        send it, and the auth modes it supports, all projected from the registry's
        ``TargetMetadata``; targets that need a Python object or a path callers may not
        set for a required parameter are left out, and so are targets that upload local
        files when the server has no upload folder. Deciding which entries to surface to a
        user is a presentation concern owned by the caller (e.g. the frontend),
        not this service.

        Args:
            upload_directory (str | None): Folder the server lets API-created targets upload files
                from, or None when the server configures none.

        Returns:
            TargetTypeResponse containing all available target classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items: list[TargetTypeEntry] = [
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
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
            if self._has_required_params(target_type=metadata.class_name, parameters=metadata.parameters)
            and self._has_upload_directory(target_type=metadata.class_name, upload_directory=upload_directory)
        ]
        return TargetTypeResponse(items=items)

    async def build_from_source_async(
        self, spec: SourceInstanceSpec, *, server_params: dict[str, object] | None = None
    ) -> PromptTarget:
        """
        Rebuild a private target and verify its saved effective identity.

        Overrides may set the target's caller path parameters, as on create, but not
        the upload folder, which only the server sets.

        Args:
            spec: The source and its external overrides.
            server_params: Constructor arguments the server chooses, such as a caller credential
                provider, which replace both source inputs and overrides. Defaults to None.

        Returns:
            PromptTarget: The caller-owned target.
        """
        source = self._registry.resolve_source(name=spec.source_name, identifier_hash=spec.source_hash)
        target = self._registry.recreate_instance(
            source=source,
            params=spec.params,
            external_input=True,
            allowed_path_params=type(source).caller_path_parameters,
            server_params=server_params,
        )
        if spec.effective_hash and target.get_identifier().hash != spec.effective_hash:
            await release_component_async(target)
            raise ValueError("The saved target configuration cannot be reconstructed")
        return target

    async def resolve_binding_async(self, binding: TargetBinding) -> PromptTarget:
        """
        Rebuild an attack-owned configuration from its current source.

        Returns:
            PromptTarget: The caller-owned target.
        """
        return await self.build_from_source_async(binding.to_spec())

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
        caller: str | None = None,
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
        credentials, is built only for an administrator. A target created with
        ``requires_caller_credential`` keeps no submitted key: the key is stored for
        ``caller`` and the target resolves each caller's own credential per request.
        Without that option, a submitted key stays on the shared target.
        The response is built before the target is registered, so a failed request
        leaves no registered target or stored credential.

        Args:
            request: The create target request with type, params, and auth_mode.
            upload_directory: Folder the server lets API-created targets upload files from,
                or None when the server configures none.
            is_admin: Whether the caller may create a target that runs model repository code or
                authenticates with this server's stored credentials.
            caller: Principal of the requesting caller, recorded as the target's creator and, with
                ``requires_caller_credential``, as the owner of a submitted credential. Defaults to None.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: If the target type is not registered, ``params`` carries an
                ``auth_mode`` that conflicts with the request-level choice, identity or
                no-key auth is requested but unsupported by the target type, a target that
                uploads local files is requested with its upload folder or while
                the server configures none, or ``requires_caller_credential`` is requested
                for an unsupported target, auth mode, or unregistered target. Construction
                errors (unknown params, incompatible inner targets, unrecognized identity
                endpoints) are raised by the registry / target classes.
            PermissionError: If a non-administrator enables a target's model code execution,
                creates a target that would authenticate with this server's stored credentials,
                or requests caller credentials without a verified caller identity.
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
        await self._enforce_code_execution_permission_async(
            target_type=request.type, params=permission_params, is_admin=is_admin
        )
        self._enforce_server_credential_permission(
            target_type=request.type, params=permission_params, is_admin=is_admin
        )
        source_spec = request.source
        if source_spec is not None and request.requires_caller_credential:
            if _AUTH_MODE_PARAM in source_spec.params:
                raise ValueError("A personal copy always uses caller credentials; omit auth_mode from source.params.")
            source_spec = source_spec.model_copy(update={"params": dict(source_spec.params)})
        caller_credential = self._take_caller_credential(
            target_cls=target_cls,
            request=request,
            params=source_spec.params if source_spec is not None else params,
            caller=caller,
        )

        # LEGACY COMPATIBILITY: The current configuration UI omits the name.
        # Remove this generated fallback after that UI sends an explicit name.
        target_registry_name = request.name or f"compat_{uuid.uuid4().hex}"
        if request.register:
            self._registry.instances.validate_name_available(target_registry_name)
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
        credential_id = uuid.uuid4().hex if request.requires_caller_credential else None
        server_params = self._caller_credential_server_params(
            target_cls=target_cls, credential_id=credential_id, target_registry_name=target_registry_name
        )
        target_obj = (
            await self.build_from_source_async(source_spec, server_params=server_params)
            if source_spec
            else self._registry.create_instance_from_external_input(
                request.type,
                params=params,
                allowed_path_params=self._allowed_path_params(target_cls=target_cls),
                server_params=server_params,
            )
        )
        if not request.register:
            try:
                return UnregisteredTarget(
                    identifier=TargetIdentifier.from_component_identifier(target_obj.get_identifier()),
                    capabilities=target_obj.capabilities,
                )
            finally:
                await release_component_async(target_obj)
        metadata: dict[str, Any] = {}
        if caller is not None:
            metadata[self.CREATED_BY_METADATA_KEY] = caller
        if credential_id is not None:
            metadata[self.CALLER_CREDENTIAL_ID_METADATA_KEY] = credential_id
        try:
            target = self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)
            self._registry.instances.register(target_obj, name=target_registry_name, metadata=metadata)
        except Exception:
            await release_component_async(target_obj)
            raise
        if caller_credential is not None and caller is not None and credential_id is not None:
            self._credentials.set_credential(
                principal=caller, credential_id=credential_id, credential=caller_credential
            )
        return self._with_caller_access(instance=target, metadata=metadata, caller=caller)

    async def set_caller_credential_async(
        self, *, target_registry_name: str, caller: str | None, credential: str
    ) -> TargetInstance | None:
        """
        Set or replace the caller's own credential for a target.

        The target and its configuration are unchanged. The new credential applies to the
        caller's next request through the target, including requests of work already running.

        Args:
            target_registry_name: The registry name of the target.
            caller: Principal of the requesting caller.
            credential: The caller's credential.

        Returns:
            TargetInstance | None: The target as seen by the caller, or None if it is not registered.

        Raises:
            ValueError: If the target does not accept caller credentials or the credential is empty.
            PermissionError: If the request has no verified caller identity.
        """
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return None
        principal = self._require_caller(caller)
        self._credentials.set_credential(
            principal=principal, credential_id=self._caller_credential_id(entry), credential=credential
        )
        return self._build_instance_from_entry(entry=entry, caller=principal)

    async def remove_caller_credential_async(self, *, target_registry_name: str, caller: str | None) -> bool:
        """
        Remove the caller's own credential for a target.

        Args:
            target_registry_name: The registry name of the target.
            caller: Principal of the requesting caller.

        Returns:
            bool: False if the target is not registered; otherwise True, whether or not the caller
                had set a credential.

        Raises:
            ValueError: If the target does not accept caller credentials.
            PermissionError: If the request has no verified caller identity.
        """
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return False
        self._credentials.remove_credential(
            principal=self._require_caller(caller), credential_id=self._caller_credential_id(entry)
        )
        return True

    async def delete_target_async(self, *, target_registry_name: str, caller: str | None) -> bool:
        """
        Unregister a target its caller created through the API, and forget all callers' credentials for it.

        The API admission barrier requires an idle runtime and blocks new work until cleanup
        finishes. Direct service callers must provide the same exclusive ownership.

        Args:
            target_registry_name: The registry name of the target.
            caller: Principal of the requesting caller.

        Returns:
            bool: True if the target was deleted, False if it is not registered.

        Raises:
            PermissionError: If the caller did not create the target through the API.
        """
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return False
        if caller is None or entry.metadata.get(self.CREATED_BY_METADATA_KEY) != caller:
            raise PermissionError("Only the caller who created this target through the API can delete it.")
        await release_component_async(entry.instance)
        if self._registry.instances.unregister(target_registry_name, expected_entry=entry) is None:
            return False
        credential_id = entry.metadata.get(self.CALLER_CREDENTIAL_ID_METADATA_KEY)
        if credential_id is not None:
            self._credentials.remove_credential_id(credential_id)
        return True

    @staticmethod
    def _require_caller(caller: str | None) -> str:
        """
        Require a verified caller identity for caller-owned credentials.

        Returns:
            str: The caller principal.

        Raises:
            PermissionError: If there is no verified caller.
        """
        if caller is None:
            raise PermissionError("A verified caller identity is required to manage caller credentials.")
        return caller

    def _caller_credential_id(self, entry: RegistryEntry[PromptTarget]) -> str:
        """
        Return the credential slot of a target that accepts caller credentials.

        Returns:
            str: The target's credential ID.

        Raises:
            ValueError: If the target was not registered with caller credentials.
        """
        credential_id = entry.metadata.get(self.CALLER_CREDENTIAL_ID_METADATA_KEY)
        if credential_id is None:
            raise ValueError(
                f"Target '{entry.name}' is shared and does not use caller credentials. Only targets created with "
                "requires_caller_credential accept per-caller credentials."
            )
        return str(credential_id)

    @staticmethod
    def _take_caller_credential(
        *,
        target_cls: type[PromptTarget],
        request: CreateTargetRequest,
        params: dict[str, Any],
        caller: str | None,
    ) -> str | None:
        """
        Validate an opt-in to caller credentials and remove the key the target will resolve per caller.

        Returns:
            str | None: The creator's submitted credential, or None when the request does not opt in
                or submits no key.

        Raises:
            ValueError: If the target type, auth mode, or registration does not support caller
                credentials, or the submitted key is not a nonempty string.
            PermissionError: If the request has no verified caller identity.
        """
        if not request.requires_caller_credential:
            return None
        parameter_name = target_cls.caller_credential_parameter
        if parameter_name is None:
            raise ValueError(f"Target type '{request.type}' does not support requires_caller_credential.")
        if request.auth_mode != "api_key":
            raise ValueError("requires_caller_credential requires auth_mode 'api_key'.")
        if not request.register:
            raise ValueError("requires_caller_credential applies only to registered targets.")
        if caller is None:
            raise PermissionError("A verified caller identity is required to create a target with caller credentials.")
        credential = params.pop(parameter_name, None)
        if credential is None:
            return None
        if not isinstance(credential, str) or not credential.strip():
            raise ValueError(f"'{parameter_name}' must be a nonempty string.")
        return credential

    def _caller_credential_server_params(
        self, *, target_cls: type[PromptTarget], credential_id: str | None, target_registry_name: str
    ) -> dict[str, object] | None:
        """
        Build the server-side provider that resolves each caller's credential for a new target.

        The provider replaces any credential the target would otherwise inherit, such as a personal
        copy's source key or managed identity, and the target always authenticates with the key it
        returns.

        Returns:
            dict[str, object] | None: Constructor arguments with the provider, or None without a credential.
        """
        parameter_name = target_cls.caller_credential_parameter
        if credential_id is None or parameter_name is None:
            return None
        provider = create_caller_credential_provider(
            store=self._credentials, credential_id=credential_id, component_name=target_registry_name
        )
        return {**target_cls.get_auth_mode_parameters(auth_mode="api_key"), parameter_name: provider}

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
def get_caller_credential_store() -> CallerCredentialStore:
    """
    Get the process-memory store of callers' target credentials.

    Returns:
        The singleton CallerCredentialStore.
    """
    return CallerCredentialStore()


def clear_caller_credentials() -> None:
    """Forget every caller's target credentials, such as when registered targets are replaced."""
    if get_caller_credential_store.cache_info().currsize:
        get_caller_credential_store().clear()


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
