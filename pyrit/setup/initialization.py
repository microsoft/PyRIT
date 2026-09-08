# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import logging
import pathlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, get_args

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

_load_environment_files = load_environment_files


async def _execute_initializers_async(
    *,
    initializers: Sequence["PyRITInitializer"],
    raise_on_initializer_error: bool,
) -> None:
    """
    Execute PyRITInitializer instances in the order provided.

    Initializers are executed in the order they appear in the sequence.

    Args:
        initializers: Sequence of PyRITInitializer instances to execute.
        raise_on_initializer_error: Whether to raise when an initializer fails. If False,
            log the failure and continue with the remaining initializers.

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

        except Exception:
            logger.exception("Error executing initializer %s", type(initializer).__name__)
            if raise_on_initializer_error:
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
    raise_on_initializer_error: bool = True,
    **memory_instance_kwargs: Any,
) -> None:
    """
    Initialize PyRIT with the provided memory instance and loads environment files.

    Args:
        memory_db_type (MemoryDatabaseType): The MemoryDatabaseType string literal which indicates the memory
            instance to use for central memory. Options include "InMemory", "SQLite", and "AzureSQL".
        initialization_scripts (Sequence[str | pathlib.Path] | None): Optional sequence of local Python script paths
            that define PyRITInitializer subclasses. Every initializer subclass defined in each file is loaded and
            executed. Loading is handled by the InitializerRegistry.
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
        raise_on_initializer_error (bool): If True, raise when loading or executing an initializer fails.
            If False, log each failure and continue with the remaining initializers. Defaults to True.
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
        script_paths = [pathlib.Path(script_path) for script_path in initialization_scripts]
        for script_path in script_paths:
            try:
                script_initializers = registry.create_from_script_paths(script_paths=[script_path])
                all_initializers.extend(script_initializers)
            except Exception:
                logger.exception("Error loading initializers from script %s", script_path)
                if raise_on_initializer_error:
                    raise

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
        await _execute_initializers_async(
            initializers=all_initializers,
            raise_on_initializer_error=raise_on_initializer_error,
        )
