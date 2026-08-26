# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import asyncio
import logging
import pathlib
import tempfile
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, get_args
from urllib.parse import parse_qs, urlparse

from pyrit.common.apply_defaults import reset_default_values
from pyrit.common.random_context import configure_random_seed
from pyrit.memory import AzureSQLMemory, CentralMemory, MemoryInterface, SQLiteMemory
from pyrit.setup.environment_loading import (
    load_environment_async,
    load_environment_files,
    validate_env_akv_strict,
)

if TYPE_CHECKING:
    from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)

IN_MEMORY = "InMemory"
SQLITE = "SQLite"
AZURE_SQL = "AzureSQL"
MemoryDatabaseType = Literal["InMemory", "SQLite", "AzureSQL"]

_AZURE_BLOB_HOST_SUFFIXES = (
    ".blob.core.windows.net",
    ".blob.core.chinacloudapi.cn",
    ".blob.core.usgovcloudapi.net",
    ".blob.core.cloudapi.de",
)

_load_environment_files = load_environment_files


def is_azure_blob_script_uri(value: str) -> bool:
    """Return whether a value identifies an Azure Blob Storage object."""
    parsed_uri = urlparse(value)
    hostname = parsed_uri.hostname or ""
    return (
        parsed_uri.scheme == "https"
        and any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in _AZURE_BLOB_HOST_SUFFIXES)
        and parsed_uri.username is None
        and parsed_uri.password is None
        and parsed_uri.port is None
        and len(parsed_uri.path.strip("/").split("/")) >= 2
    )


def _download_initialization_script(*, source: str, destination: pathlib.Path) -> None:
    """
    Download an Azure Blob initialization script to a temporary path.

    Raises:
        ValueError: If the Blob URI does not identify a Python file.
    """
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    parsed_uri = urlparse(source)
    if pathlib.PurePosixPath(parsed_uri.path).suffix != ".py":
        raise ValueError(f"Initialization script must be a Python file (.py): {parsed_uri.path}")

    if "sig" in parse_qs(parsed_uri.query):
        with BlobClient.from_blob_url(blob_url=source) as client:
            destination.write_bytes(client.download_blob().readall())
        return

    with DefaultAzureCredential() as credential:
        with BlobClient.from_blob_url(blob_url=source, credential=credential) as client:
            destination.write_bytes(client.download_blob().readall())


async def _materialize_initialization_scripts_async(
    *, script_sources: Sequence[str | pathlib.Path], destination: pathlib.Path
) -> list[pathlib.Path]:
    """
    Resolve local paths and download Azure Blob scripts into a temporary directory.

    Returns:
        list[pathlib.Path]: Local paths suitable for registry loading.
    """
    resolved: list[pathlib.Path] = []
    for index, source in enumerate(script_sources):
        source_str = str(source)
        if not is_azure_blob_script_uri(source_str):
            resolved.append(pathlib.Path(source))
            continue

        blob_name = pathlib.PurePosixPath(urlparse(source_str).path).name
        temporary_path = destination / f"{index}_{blob_name}"
        await asyncio.to_thread(_download_initialization_script, source=source_str, destination=temporary_path)
        resolved.append(temporary_path)

    return resolved


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
    seed: int | None = None,
    **memory_instance_kwargs: Any,
) -> None:
    """
    Initialize PyRIT with the provided memory instance and loads environment files.

    Args:
        memory_db_type (MemoryDatabaseType): The MemoryDatabaseType string literal which indicates the memory
            instance to use for central memory. Options include "InMemory", "SQLite", and "AzureSQL".
        initialization_scripts (Sequence[str | pathlib.Path] | None): Optional sequence of local Python script paths
            or Azure Blob URIs that define PyRITInitializer subclasses. Every initializer subclass defined in each
            file is loaded and executed. Loading is handled by the InitializerRegistry. Blob URIs may include a SAS;
            otherwise, authentication uses DefaultAzureCredential.
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
            in order. Ordinary files fill missing process values; files named ``.env.local`` override.
            If omitted, PyRIT auto-discovers supported ``.env`` and ``.env.local`` files.
        env_akv_ref (Sequence[str] | None): Optional zero-or-one-item sequence containing an Azure Key Vault
            URL whose secret value is a bootstrap dotenv document. The document fills missing process values
            and supports complete-value references to scalar secrets. Requires ``azure-keyvault-secrets``.
        env_akv_strict (bool): If True, reject malformed bootstrap entries and Key Vault reference
            syntax. If False, warn and skip those entries. Operational Key Vault failures always raise.
        silent (bool): If True, suppresses print statements about environment file loading and
            schema migration. Defaults to False.
        seed (int | None): Optional root seed for deterministic converter operations. Converters derive
            independent named child streams automatically. Initialize PyRIT before constructing components
            whose defaults are selected randomly. This does not control remote model output.
        **memory_instance_kwargs (Any | None): Additional keyword arguments to pass to the memory instance.

    Raises:
        TypeError: If ``env_akv_strict`` is not a bool or seed is not an int or None.
        ValueError: If an unsupported memory_db_type is provided or env_files contains non-existent files.
    """
    validate_env_akv_strict(env_akv_strict=env_akv_strict)
    configure_random_seed(seed=seed)
    await load_environment_async(
        env_akv_ref=env_akv_ref,
        env_files=env_files,
        env_akv_strict=env_akv_strict,
        silent=silent,
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
        with tempfile.TemporaryDirectory(prefix="pyrit-initializers-") as temporary_directory:
            script_paths = await _materialize_initialization_scripts_async(
                script_sources=initialization_scripts,
                destination=pathlib.Path(temporary_directory),
            )
            script_initializers = registry.create_from_script_paths(script_paths=script_paths)
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
