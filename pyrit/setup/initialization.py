# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import asyncio
import io
import logging
import os
import pathlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, get_args

import dotenv
from dotenv.parser import parse_stream

from pyrit.common import path
from pyrit.common.apply_defaults import reset_default_values
from pyrit.memory import AzureSQLMemory, CentralMemory, MemoryInterface, SQLiteMemory

if TYPE_CHECKING:
    from azure.keyvault.secrets.aio import SecretClient

    from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)

IN_MEMORY = "InMemory"
SQLITE = "SQLite"
AZURE_SQL = "AzureSQL"
MemoryDatabaseType = Literal["InMemory", "SQLite", "AzureSQL"]

_AKV_REFERENCE_PREFIXES = frozenset({"akv", "kv", "azure_key_vault", "env_akv_ref"})


def _load_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool = False,
    include_default_base: bool = True,
) -> bool:
    """
    Load environment files in the order they are provided.
    Later files override values from earlier files.

    Args:
        env_files: Optional sequence of environment file paths. If None, loads default
            .env and .env.local from PyRIT home directory (only if they exist).
        silent: If True, suppresses print statements about environment file loading.
            Defaults to False.
        include_default_base: If False and env_files is None, skips the default
            .env file while still loading .env.local. Defaults to True.

    Returns:
        True if at least one environment file was loaded, otherwise False.

    Raises:
        ValueError: If any provided env_files do not exist.
    """
    # Validate env_files exist if they were provided
    if env_files is not None:
        if not silent:
            _print_msg(f"Loading custom environment files: {[str(f) for f in env_files]}", quiet=silent, log=True)
        for env_file in env_files:
            if not env_file.exists():
                raise ValueError(f"Environment file not found: {env_file}")

    # By default load .env and .env.local from home directory of the package
    else:
        default_files = []
        base_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
        local_file = path.CONFIGURATION_DIRECTORY_PATH / ".env.local"

        if include_default_base and base_file.exists():
            default_files.append(base_file)
        if local_file.exists():
            default_files.append(local_file)

        if not silent:
            if default_files:
                _print_msg(
                    f"Found default environment files: {[str(f) for f in default_files]}", quiet=silent, log=True
                )
            else:
                _print_msg(
                    "No default environment files found.",
                    quiet=silent,
                    log=True,
                )

        env_files = default_files

    for env_file in env_files:
        dotenv.load_dotenv(env_file, override=True, interpolate=True)
        if not silent:
            _print_msg(f"Loaded environment file: {env_file}", quiet=silent, log=True)

    return bool(env_files)


def _print_msg(message: str, quiet: bool, log: bool) -> None:
    """
    Print a standard initialization message unless quiet is True.

    Args:
        message (str): The message to print and/or log.
        quiet (bool): If True, suppresses the initialization message.
        log (bool): If True, logs the message using the logger.
    """
    if not quiet:
        print(message)
    if log:
        logger.info(message)


def _warn_about_akv_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool = False,
) -> None:
    """Warn when local environment files coexist with an AKV environment source."""
    base_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
    local_file = path.CONFIGURATION_DIRECTORY_PATH / ".env.local"
    messages: list[str] = []

    if base_file.exists():
        messages.append(f"{base_file} exists and will be ignored because Key Vault supplies the base environment")

    if local_file.exists():
        if env_files is None:
            messages.append(f"{local_file} will load after Key Vault and override matching values")
        else:
            messages.append(f"{local_file} exists but will be ignored because env_files was explicitly configured")

    if env_files:
        messages.append(f"explicit env_files will load after Key Vault and override matching values: {list(env_files)}")

    if not messages:
        return

    message = (
        "env_akv_ref is configured, but local environment files were also found:\n- "
        + "\n- ".join(messages)
        + "\nConfirm that this precedence is intentional."
    )
    if not silent:
        print(f"WARNING: {message}")
    logger.warning(message)


def _parse_akv_secret_url(secret_url: str) -> tuple[str, str, str | None]:
    """
    Parse an AKV secret URL into vault URL, secret name, and optional version.

    Args:
        secret_url (str): Full AKV secret URL in the format
            ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.

    Returns:
        tuple[str, str, str | None]: (vault_url, secret_name, secret_version)

    Raises:
        ValueError: If the URL does not match the expected format.
    """
    parts = secret_url.split("/secrets/")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid AKV secret URL: '{secret_url}'. "
            "Expected format: https://{{vault}}.vault.azure.net/secrets/{{name}}[/{{version}}]"
        )
    vault_url = parts[0]
    name_parts = parts[1].rstrip("/").split("/")
    secret_name = name_parts[0]
    secret_version = name_parts[1] if len(name_parts) > 1 else None
    return vault_url, secret_name, secret_version


def _validate_dotenv_document(
    document: str,
    *,
    strict: bool = True,
    silent: bool = False,
) -> str:
    """
    Validate that every dotenv binding uses ``NAME=VALUE`` syntax.

    Args:
        document (str): The dotenv document to validate.
        strict (bool): If True, reject any invalid entry. If False, warn and
            allow python-dotenv to skip invalid entries. Defaults to True.
        silent (bool): If True, suppress the console warning. Defaults to False.

    Returns:
        str: The original document, or a sanitized document when strict is False.

    Raises:
        ValueError: If strict is True and the document contains invalid entries.
    """
    bindings = list(parse_stream(io.StringIO(document)))
    malformed_lines = [str(binding.original.line) for binding in bindings if binding.error]
    valueless_names = [binding.key for binding in bindings if binding.key is not None and binding.value is None]
    issues: list[str] = []
    if malformed_lines:
        issues.append("malformed entries at lines: " + ", ".join(malformed_lines))
    if valueless_names:
        issues.append("variables without values: " + ", ".join(valueless_names))
    if not issues:
        return document

    details = "; ".join(issues)
    if strict:
        raise ValueError("AKV environment document contains " + details)

    message = "AKV environment document contains invalid entries that will be skipped: " + details
    if not silent:
        print(f"WARNING: {message}")
    logger.warning(message)
    return "".join(
        binding.original.string
        for binding in bindings
        if not binding.error and not (binding.key is not None and binding.value is None)
    )


async def _load_env_from_akv_async(
    *,
    secret_url: str,
    strict: bool = True,
    silent: bool = False,
) -> None:
    """
    Load environment variables from an Azure Key Vault secret.

    The secret URL identifies the bootstrap environment document. Values in
    that document may directly reference scalar secrets in the same vault.

    Authentication uses ``DefaultAzureCredential``, which silently tries managed
    identity, Azure CLI, VS Code credentials, etc., and falls back to interactive
    browser authentication when running locally.

    Args:
        secret_url (str): AKV secret URL in the format
            ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.
        strict (bool): If True, reject malformed or valueless dotenv entries.
            If False, warn and skip those entries. Defaults to True.
        silent (bool): If True, suppresses print statements. Defaults to False.

    Raises:
        ImportError: If ``azure-keyvault-secrets`` is not installed.
        ValueError: If the root URL is malformed or the bootstrap environment
            document cannot be fully resolved.
    """
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient

    _print_msg(f"Loading environment from AKV secret: {secret_url}", quiet=silent, log=True)
    vault_url, secret_name, secret_version = _parse_akv_secret_url(secret_url)
    ambient_environment = dict(os.environ)
    async with DefaultAzureCredential() as credential:
        async with SecretClient(vault_url=vault_url, credential=credential) as client:
            secret = await client.get_secret(secret_name, version=secret_version)

            if not secret.value:
                raise ValueError(f"AKV environment secret has no value: {secret_url}")

            validated_document = _validate_dotenv_document(secret.value, strict=strict, silent=silent)
            parsed_environment = dotenv.dotenv_values(stream=io.StringIO(validated_document), interpolate=True)
            if not parsed_environment:
                raise ValueError(f"AKV environment secret contains no environment entries: {secret_url}")

            resolved_secrets: dict[str, str] = {}
            resolved_environment: dict[str, str] = {}
            for variable_name, value in parsed_environment.items():
                if value is None:
                    continue
                resolved_environment[variable_name] = await _resolve_environment_value_async(
                    value=value,
                    variable_name=variable_name,
                    secret_client=client,
                    vault_url=vault_url,
                    ambient_environment=ambient_environment,
                    resolved_secrets=resolved_secrets,
                )

    os.environ.update(resolved_environment)

    _print_msg(f"Loaded environment from AKV secret: {secret_url}", quiet=silent, log=True)


def _parse_environment_value_reference(value: str) -> tuple[str, str] | None:
    """
    Parse an exact whole-value environment or Key Vault reference.

    Returns:
        The normalized reference type and target, or None for a literal value.
    """
    prefix, separator, target = value.partition(":")
    if not separator:
        return None
    if prefix == "env":
        return "env", target.strip()
    if prefix in _AKV_REFERENCE_PREFIXES:
        return "akv", target.strip()
    if prefix == "literal":
        return "literal", target
    return None


def _validate_akv_secret_name(*, secret_name: str, variable_name: str) -> None:
    if not secret_name or len(secret_name) > 127 or any(not char.isalnum() and char != "-" for char in secret_name):
        raise ValueError(
            f"Invalid same-vault secret name '{secret_name}' referenced by environment variable '{variable_name}'. "
            "Secret names must contain only letters, numbers, and hyphens."
        )


def _resolve_akv_secret_reference(
    *,
    target: str,
    variable_name: str,
    vault_url: str,
) -> tuple[str, str | None, str]:
    """
    Resolve a same-vault secret name or full secret URI.

    Args:
        target (str): A secret name or full Key Vault secret URI.
        variable_name (str): The environment variable receiving the secret.
        vault_url (str): The bootstrap document's vault URL.

    Returns:
        tuple[str, str | None, str]: Secret name, optional version, and cache key.

    Raises:
        ValueError: If the target is invalid or references another vault.
    """
    secret_name = target
    secret_version: str | None = None
    if target.casefold().startswith("https://"):
        referenced_vault_url, secret_name, secret_version = _parse_akv_secret_url(target)
        if referenced_vault_url.rstrip("/").casefold() != vault_url.rstrip("/").casefold():
            raise ValueError(
                f"Cross-vault AKV reference for environment variable '{variable_name}' is not supported. "
                f"Expected vault '{vault_url}', got '{referenced_vault_url}'."
            )

    _validate_akv_secret_name(secret_name=secret_name, variable_name=variable_name)
    cache_key = f"{secret_name.casefold()}|{secret_version or ''}"
    return secret_name, secret_version, cache_key


async def _resolve_environment_value_async(
    *,
    value: str,
    variable_name: str,
    secret_client: "SecretClient",
    vault_url: str,
    ambient_environment: dict[str, str],
    resolved_secrets: dict[str, str],
) -> str:
    """
    Resolve one value from the bootstrap environment document.

    Args:
        value (str): The parsed bootstrap value.
        variable_name (str): The environment variable receiving the resolved value.
        secret_client (SecretClient): The client for the bootstrap document's vault.
        vault_url (str): The bootstrap document's vault URL.
        ambient_environment (dict[str, str]): Snapshot used for ``env:`` references.
        resolved_secrets (dict[str, str]): Same-vault scalar cache keyed by secret name.

    Returns:
        str: The literal, ambient, or same-vault scalar value.

    Raises:
        ValueError: If a reference is empty or cannot resolve to a value.
    """
    reference = _parse_environment_value_reference(value)
    if reference is None:
        return value

    reference_type, target = reference
    if reference_type == "literal":
        return target
    if not target:
        raise ValueError(f"Empty {reference_type} reference for environment variable '{variable_name}'.")

    if reference_type == "env":
        if target not in ambient_environment:
            raise ValueError(
                f"Environment variable '{target}' referenced by '{variable_name}' "
                "is not set in the ambient environment."
            )
        return ambient_environment[target]

    secret_name, secret_version, secret_cache_key = _resolve_akv_secret_reference(
        target=target,
        variable_name=variable_name,
        vault_url=vault_url,
    )
    if secret_cache_key in resolved_secrets:
        return resolved_secrets[secret_cache_key]

    secret = await secret_client.get_secret(secret_name, version=secret_version)
    if secret.value is None:
        raise ValueError(
            f"AKV secret '{secret_name}' referenced by environment variable '{variable_name}' has no value."
        )
    resolved_secrets[secret_cache_key] = secret.value
    return secret.value


async def _execute_initializers_async(*, initializers: Sequence["PyRITInitializer"]) -> None:
    """
    Execute PyRITInitializer instances in the order provided.

    Initializers are executed in the order they appear in the sequence.

    Args:
        initializers: Sequence of PyRITInitializer instances to execute.

    Raises:
        ValueError: If an initializer is not a PyRITInitializer instance.
        Exception: If an initializer's validation or initialization fails.
    """
    # Import here to avoid circular imports
    from pyrit.setup.pyrit_initializer import PyRITInitializer

    # Validate all initializers first
    for initializer in initializers:
        if not isinstance(initializer, PyRITInitializer):
            raise ValueError(
                f"All initializers must be PyRITInitializer instances. Got {type(initializer).__name__}: {initializer}"
            )

    for initializer in initializers:
        logger.info(f"Executing initializer: {type(initializer).__name__}")
        logger.debug(f"Description: {initializer.description}")

        try:
            # Validate first
            initializer.validate()

            # Then initialize with tracking to capture what was configured
            await initializer.initialize_with_tracking_async()

            logger.debug(f"Successfully executed initializer: {type(initializer).__name__}")

        except Exception as e:
            logger.error(f"Error executing initializer {type(initializer).__name__}: {e}")
            raise


async def initialize_pyrit_async(
    memory_db_type: MemoryDatabaseType | str,
    *,
    initialization_scripts: Sequence[str | pathlib.Path] | None = None,
    initializers: Sequence["PyRITInitializer"] | None = None,
    load_defaults: bool = True,
    env_files: Sequence[pathlib.Path] | None = None,
    env_akv_ref: Sequence[str] | None = None,
    env_akv_strict: bool = True,
    silent: bool = False,
    **memory_instance_kwargs: Any,
) -> None:
    """
    Initialize PyRIT with the provided memory instance and loads environment files.

    Args:
        memory_db_type (MemoryDatabaseType): The MemoryDatabaseType string literal which indicates the memory
            instance to use for central memory. Options include "InMemory", "SQLite", and "AzureSQL".
        initialization_scripts (Sequence[str | pathlib.Path] | None): Optional sequence of Python script paths
            that define PyRITInitializer subclasses. Every initializer subclass defined in each file is
            loaded and executed. Loading is handled by the InitializerRegistry.
        initializers (Sequence[PyRITInitializer] | None): Optional sequence of PyRITInitializer instances
            to execute directly. These provide type-safe, validated configuration with clear documentation.
        load_defaults (bool): If True (default) AND the caller supplies neither ``initializers`` nor
            ``initialization_scripts``, a default initializer set is run so a bare
            ``initialize_pyrit_async(...)`` yields a usable environment: the core attack-technique catalog
            (``TechniqueInitializer``, populating the AttackTechniqueRegistry) plus the available default
            targets (``TargetInitializer``, registering whatever endpoints are configured via env vars).
            Supplying any initializer or script means the caller owns setup, so the defaults are skipped;
            set this to False to also skip them on a bare call (e.g. to start from an empty state). Only the
            ``core`` techniques and ``default`` targets are loaded — ``extra`` / per-source technique groups
            and ``scorer`` target variants remain opt-in.
        env_files (Sequence[pathlib.Path] | None): Optional sequence of environment file paths to load
            in order. If not provided, will load default .env and .env.local files from PyRIT home if they exist.
            All paths must be valid pathlib.Path objects.
        env_akv_ref (Sequence[str] | None): Optional sequence of Azure Key Vault secret URLs to load.
            The first secret's value must contain the bootstrap .env document; additional URLs are ignored.
            Loaded before ``env_files`` so local files take precedence over AKV. Requires
            ``azure-keyvault-secrets``.
        env_akv_strict (bool): If True, reject malformed or valueless entries in the Key Vault
            bootstrap document. If False, warn and skip those entries. Defaults to True.
        silent (bool): If True, suppresses print statements about environment file loading and
            schema migration. Defaults to False.
        **memory_instance_kwargs (Any | None): Additional keyword arguments to pass to the memory instance.

    Raises:
        ValueError: If an unsupported memory_db_type is provided, env_files contains non-existent files,
            or neither env_akv_ref nor an environment file is available.
    """
    if env_akv_ref:
        await asyncio.to_thread(
            _warn_about_akv_environment_files,
            env_files=env_files,
            silent=silent,
        )
        if len(env_akv_ref) > 1:
            _print_msg(
                "Multiple env_akv_ref values were provided; using the first as the root environment document.",
                quiet=silent,
                log=True,
            )
        await _load_env_from_akv_async(
            secret_url=env_akv_ref[0],
            strict=env_akv_strict,
            silent=silent,
        )

        await asyncio.to_thread(
            _load_environment_files,
            env_files=env_files,
            silent=silent,
            include_default_base=False,
        )
    else:
        loaded_local_file = await asyncio.to_thread(
            _load_environment_files,
            env_files=env_files,
            silent=silent,
        )
        if not loaded_local_file:
            raise ValueError(
                "No environment source found. Configure env_akv_ref or provide at least one .env or .env.local file."
            )

    # Reset all default values before executing initialization scripts
    # This ensures a clean state for each initialization
    reset_default_values()

    # Set up memory BEFORE executing initialization scripts
    # This is critical because initialization scripts may instantiate objects
    # (like prompt targets) that require central memory to be initialized
    memory: MemoryInterface

    if memory_db_type == IN_MEMORY:
        logger.info("Using in-memory SQLite database.")
        memory = SQLiteMemory(db_path=":memory:", silent=silent, **memory_instance_kwargs)  # type: ignore[ty:invalid-assignment]
    elif memory_db_type == SQLITE:
        logger.info("Using persistent SQLite database.")
        memory = SQLiteMemory(silent=silent, **memory_instance_kwargs)  # type: ignore[ty:invalid-assignment]
    elif memory_db_type == AZURE_SQL:
        logger.info("Using AzureSQL database.")
        memory = AzureSQLMemory(silent=silent, **memory_instance_kwargs)  # type: ignore[ty:invalid-assignment]
    else:
        raise ValueError(
            f"Memory database type '{memory_db_type}' is not a supported type {get_args(MemoryDatabaseType)}"
        )

    CentralMemory.set_memory_instance(memory)

    # Combine directly provided initializers with those loaded from scripts.
    all_initializers: list[PyRITInitializer] = list(initializers) if initializers else []

    # Load additional initializers from scripts — the registry owns turning
    # external script files into initializer instances.
    if initialization_scripts:
        from pyrit.registry import InitializerRegistry

        registry = InitializerRegistry.get_registry_singleton()
        script_initializers = registry.create_from_script_paths(script_paths=initialization_scripts)
        all_initializers.extend(script_initializers)

    # When the caller supplies nothing, fall back to the default initializer set so a
    # bare initialize_pyrit_async(...) yields a usable environment (core techniques +
    # available default targets). Supplying any initializer/script means the caller owns
    # setup, so defaults are skipped; load_defaults=False skips them even on a bare call.
    if load_defaults and not all_initializers:
        from pyrit.setup.initializers.targets import TargetInitializer
        from pyrit.setup.initializers.techniques import TechniqueInitializer

        all_initializers = [TechniqueInitializer(), TargetInitializer()]

    # Execute all initializers in order
    if all_initializers:
        await _execute_initializers_async(initializers=all_initializers)
