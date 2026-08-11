# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import io
import logging
import pathlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, get_args

import dotenv

from pyrit.common import path
from pyrit.common.apply_defaults import reset_default_values
from pyrit.memory import AzureSQLMemory, CentralMemory, MemoryInterface, SQLiteMemory

if TYPE_CHECKING:
    from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)

IN_MEMORY = "InMemory"
SQLITE = "SQLite"
AZURE_SQL = "AzureSQL"
MemoryDatabaseType = Literal["InMemory", "SQLite", "AzureSQL"]


def _load_environment_files(env_files: Sequence[pathlib.Path] | None, *, silent: bool = False) -> None:
    """
    Load environment files in the order they are provided.
    Later files override values from earlier files.

    Args:
        env_files: Optional sequence of environment file paths. If None, loads default
            .env and .env.local from PyRIT home directory (only if they exist).
        silent: If True, suppresses print statements about environment file loading.
            Defaults to False.

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

        if base_file.exists():
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
                    "No default environment files found. Using system environment variables only.",
                    quiet=silent,
                    log=True,
                )

        env_files = default_files

    for env_file in env_files:
        dotenv.load_dotenv(env_file, override=True, interpolate=True)
        if not silent:
            _print_msg(f"Loaded environment file: {env_file}", quiet=silent, log=True)


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


async def _load_env_from_akv_async(*, secret_urls: Sequence[str], silent: bool = False) -> None:
    """
    Load environment variables from Azure Key Vault secrets.

    Each secret's value is treated as the full contents of a ``.env`` file and
    parsed accordingly. Later secrets override values from earlier ones.

    Authentication uses ``DefaultAzureCredential``, which silently tries managed
    identity, Azure CLI, VS Code credentials, etc., and falls back to interactive
    browser authentication when running locally.

    Args:
        secret_urls (Sequence[str]): Sequence of AKV secret URLs to load, each in
            the format ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.
        silent (bool): If True, suppresses print statements. Defaults to False.

    Raises:
        ImportError: If ``azure-keyvault-secrets`` is not installed.
        ValueError: If a secret URL is malformed.
    """
    if not secret_urls:
        return
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient

    credential = DefaultAzureCredential()
    for secret_url in secret_urls:
        _print_msg(f"Loading environment from AKV secret: {secret_url}", quiet=silent, log=True)
        vault_url, secret_name, secret_version = _parse_akv_secret_url(secret_url)
        client = SecretClient(vault_url=vault_url, credential=credential)
        secret = await client.get_secret(secret_name, version=secret_version)
        if secret.value:
            dotenv.load_dotenv(stream=io.StringIO(secret.value), override=True)
            _print_msg(f"Loaded environment from AKV secret: {secret_url}", quiet=silent, log=True)


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
            Each secret's value must be the full contents of a .env file. Loaded before ``env_files``
            so local files take precedence over AKV. Requires ``azure-keyvault-secrets``.
        silent (bool): If True, suppresses print statements about environment file loading and
            schema migration. Defaults to False.
        **memory_instance_kwargs (Any | None): Additional keyword arguments to pass to the memory instance.

    Raises:
        ValueError: If an unsupported memory_db_type is provided or if env_files contains non-existent files.
    """
    if env_akv_ref:
        await _load_env_from_akv_async(secret_urls=env_akv_ref, silent=silent)

    _load_environment_files(env_files=env_files, silent=silent)

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
