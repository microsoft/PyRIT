# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared core logic for PyRIT Frontends.

This module contains all the business logic for:
- Loading and discovering scenarios
- Running scenarios
- Formatting output
- Managing initialization scripts

Both pyrit_scan and pyrit_shell use these functions.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, Optional

from pyrit.cli._cli_args import ARG_HELP as ARG_HELP
from pyrit.cli._cli_args import AZURE_SQL as AZURE_SQL
from pyrit.cli._cli_args import IN_MEMORY as IN_MEMORY
from pyrit.cli._cli_args import SQLITE as SQLITE
from pyrit.cli._cli_args import _argparse_validator as _argparse_validator
from pyrit.cli._cli_args import _parse_initializer_arg as _parse_initializer_arg
from pyrit.cli._cli_args import add_common_arguments as add_common_arguments
from pyrit.cli._cli_args import non_negative_int as non_negative_int
from pyrit.cli._cli_args import parse_list_targets_arguments as parse_list_targets_arguments
from pyrit.cli._cli_args import parse_memory_labels as parse_memory_labels
from pyrit.cli._cli_args import parse_run_arguments as parse_run_arguments
from pyrit.cli._cli_args import positive_int as positive_int
from pyrit.cli._cli_args import resolve_env_files as resolve_env_files
from pyrit.cli._cli_args import resolve_env_files_argparse as resolve_env_files_argparse
from pyrit.cli._cli_args import validate_database as validate_database
from pyrit.cli._cli_args import validate_database_argparse as validate_database_argparse
from pyrit.cli._cli_args import validate_integer as validate_integer
from pyrit.cli._cli_args import validate_log_level as validate_log_level
from pyrit.cli._cli_args import validate_log_level_argparse as validate_log_level_argparse
from pyrit.registry import InitializerRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.setup import ConfigurationLoader, initialize_pyrit_async
from pyrit.setup.configuration_loader import _MEMORY_DB_TYPE_MAP

try:
    import termcolor

    HAS_TERMCOLOR = True
except ImportError:
    HAS_TERMCOLOR = False

    # Create a dummy termcolor module for fallback
    class termcolor:  # type: ignore[no-redef]  # noqa: N801
        """Dummy termcolor fallback for colored printing if termcolor is not installed."""

        @staticmethod
        def cprint(text: str, color: str | None = None, attrs: list[Any] | None = None) -> None:
            """Print text without color."""
            print(text)


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pyrit.models.scenario_result import ScenarioResult
    from pyrit.registry import (
        InitializerMetadata,
        ScenarioMetadata,
    )

logger = logging.getLogger(__name__)


class FrontendCore:
    """
    Shared context for PyRIT operations.

    This object holds all the registries and configuration needed to run
    scenarios. It can be created once (for shell) or per-command (for CLI).
    """

    def __init__(
        self,
        *,
        config_file: Optional[Path] = None,
        database: Optional[str] = None,
        initialization_scripts: Optional[list[Path]] = None,
        initializer_names: Optional[list[Any]] = None,
        env_files: Optional[list[Path]] = None,
        log_level: Optional[int] = None,
    ):
        """
        Initialize PyRIT context.

        Configuration is loaded in the following order (later values override earlier):
        1. Default config file (~/.pyrit/.pyrit_conf) if it exists
        2. Explicit config_file argument if provided
        3. Individual CLI arguments (database, initializers, etc.)

        Args:
            config_file: Optional path to a YAML-formatted configuration file.
                The file uses .pyrit_conf extension but is YAML format.
            database: Database type (InMemory, SQLite, or AzureSQL).
            initialization_scripts: Optional list of initialization script paths.
            initializer_names: Optional list of initializer entries. Each entry can be
                a string name (e.g., "simple") or a dict with 'name' and optional 'args'
                (e.g., {"name": "target", "args": {"tags": "default,scorer"}}).
            env_files: Optional list of environment file paths to load in order.
            log_level: Logging level constant (e.g., logging.WARNING). Defaults to logging.WARNING.

        Raises:
            ValueError: If database is invalid, or if config file is invalid.
            FileNotFoundError: If an explicitly specified config_file does not exist.
        """
        # Use provided log level or default to WARNING
        self._log_level = log_level if log_level is not None else logging.WARNING

        # Load configuration using ConfigurationLoader.load_with_overrides
        try:
            config = ConfigurationLoader.load_with_overrides(
                config_file=config_file,
                memory_db_type=database,
                initializers=initializer_names,
                initialization_scripts=[str(p) for p in initialization_scripts] if initialization_scripts else None,
                env_files=[str(p) for p in env_files] if env_files else None,
            )
        except ValueError as e:
            # Re-raise with user-friendly message for CLI users
            error_msg = str(e)
            if "memory_db_type" in error_msg:
                raise ValueError(
                    f"Invalid database type '{database}'. Must be one of: InMemory, SQLite, AzureSQL"
                ) from e
            raise

        # Extract values from config for internal use
        # Use canonical mapping from configuration_loader
        self._database = _MEMORY_DB_TYPE_MAP[config.memory_db_type]
        self._initialization_scripts = config._resolve_initialization_scripts()
        self._initializer_configs = config._initializer_configs if config._initializer_configs else None
        self._env_files = config._resolve_env_files()
        self._operator = config.operator
        self._operation = config.operation

        # Lazy-loaded registries
        self._scenario_registry: Optional[ScenarioRegistry] = None
        self._initializer_registry: Optional[InitializerRegistry] = None
        self._initialized = False

        # Configure logging
        logging.basicConfig(level=self._log_level)

    async def initialize_async(self) -> None:
        """
        Initialize PyRIT and load registries (heavy operation).

        Sets up memory and loads scenario/initializer registries.
        Initializers are NOT run here — they are run separately
        (per-scenario in pyrit_scan, or up-front in pyrit_backend).
        """
        if self._initialized:
            return

        # Initialize PyRIT without initializers (they run separately)
        await initialize_pyrit_async(
            memory_db_type=self._database,
            initialization_scripts=None,
            initializers=None,
            env_files=self._env_files,
        )
        # Mark that initial env loading has been printed
        self._silent_reinit = True

        # Load registries (use singleton pattern for shared access)
        self._scenario_registry = ScenarioRegistry.get_registry_singleton()
        if self._initialization_scripts:
            print("Discovering user scenarios...")
            sys.stdout.flush()
            self._scenario_registry.discover_user_scenarios()

        self._initializer_registry = InitializerRegistry()

        self._initialized = True

    def with_overrides(
        self,
        *,
        initializer_names: Optional[list[Any]] = None,
        initialization_scripts: Optional[list[Path]] = None,
        log_level: Optional[int] = None,
    ) -> FrontendCore:
        """
        Create a derived FrontendCore with per-command overrides.

        Copies inherited state (database, env_files, operator, operation, config)
        from this instance and applies the given overrides. Shares registries
        with the parent to avoid redundant re-discovery and skips re-reading
        config files.

        Args:
            initializer_names (Optional[list[Any]]): Per-command initializer overrides.
                Each entry can be a string name or a dict with 'name' and optional 'args'.
                None keeps the parent's value.
            initialization_scripts (Optional[list[Path]]): Per-command script overrides.
                None keeps the parent's value.
            log_level (Optional[int]): Per-command log level override.
                None keeps the parent's value.

        Returns:
            FrontendCore: A new context ready for use, without re-reading config files.
        """
        derived = object.__new__(FrontendCore)

        # Inherit from parent
        derived._database = self._database
        derived._env_files = self._env_files
        derived._operator = self._operator
        derived._operation = self._operation

        # Apply overrides or inherit
        derived._log_level = log_level if log_level is not None else self._log_level

        if initializer_names is not None:
            loader = ConfigurationLoader.from_dict({"initializers": initializer_names})
            derived._initializer_configs = loader._initializer_configs
        else:
            derived._initializer_configs = self._initializer_configs

        if initialization_scripts is not None:
            derived._initialization_scripts = initialization_scripts
        else:
            derived._initialization_scripts = self._initialization_scripts

        # Share registries (singletons, no need to re-discover)
        derived._scenario_registry = self._scenario_registry
        derived._initializer_registry = self._initializer_registry
        derived._initialized = True
        derived._silent_reinit = True

        return derived

    @property
    def scenario_registry(self) -> ScenarioRegistry:
        """
        Get the scenario registry. Must call await initialize_async() first.

        Raises:
            RuntimeError: If initialize_async() has not been called.
            ValueError: If the scenario registry is not initialized.
        """
        if not self._initialized:
            raise RuntimeError(
                "FrontendCore not initialized. Call 'await context.initialize_async()' before accessing registries."
            )
        if self._scenario_registry is None:
            raise ValueError("self._scenario_registry is not initialized")
        return self._scenario_registry

    @property
    def initializer_registry(self) -> InitializerRegistry:
        """
        Get the initializer registry. Must call await initialize_async() first.

        Raises:
            RuntimeError: If initialize_async() has not been called.
            ValueError: If the initializer registry is not initialized.
        """
        if not self._initialized:
            raise RuntimeError(
                "FrontendCore not initialized. Call 'await context.initialize_async()' before accessing registries."
            )
        if self._initializer_registry is None:
            raise ValueError("self._initializer_registry is not initialized")
        return self._initializer_registry


async def list_scenarios_async(*, context: FrontendCore) -> list[ScenarioMetadata]:
    """
    List metadata for all available scenarios.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        List of scenario metadata dictionaries describing each scenario class.
    """
    if not context._initialized:
        await context.initialize_async()
    return context.scenario_registry.list_metadata()


async def list_initializers_async(
    *,
    context: FrontendCore,
) -> Sequence[InitializerMetadata]:
    """
    List metadata for all available initializers.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        Sequence of initializer metadata dictionaries describing each initializer class.
    """
    if not context._initialized:
        await context.initialize_async()
    return context.initializer_registry.list_metadata()


async def list_targets_async(
    *,
    context: FrontendCore,
) -> list[str]:
    """
    List available target names from the TargetRegistry.

    Since targets are registered by initializers, this function requires initializers
    to have been run first. Configure initializers on the FrontendCore context
    (via initializer_names or initialization_scripts) before calling this function.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        Sorted list of registered target names.
    """
    if not context._initialized:
        await context.initialize_async()

    # Run initializers and/or initialization scripts to populate the target registry
    if context._initializer_configs or context._initialization_scripts:
        initializer_instances = []
        if context._initializer_configs:
            for config in context._initializer_configs:
                initializer_class = context.initializer_registry.get_class(config.name)
                instance = initializer_class()
                if config.args:
                    instance.set_params_from_args(args=config.args)
                initializer_instances.append(instance)

        await initialize_pyrit_async(
            memory_db_type=context._database,
            initialization_scripts=context._initialization_scripts,
            initializers=initializer_instances or None,
            env_files=context._env_files,
            silent=getattr(context, "_silent_reinit", False),
        )

    target_registry = TargetRegistry.get_registry_singleton()
    return target_registry.get_names()


async def run_scenario_async(
    *,
    scenario_name: str,
    context: FrontendCore,
    target_name: str | None = None,
    scenario_strategies: Optional[list[str]] = None,
    max_concurrency: Optional[int] = None,
    max_retries: Optional[int] = None,
    memory_labels: Optional[dict[str, str]] = None,
    dataset_names: Optional[list[str]] = None,
    max_dataset_size: Optional[int] = None,
    print_summary: bool = True,
) -> ScenarioResult:
    """
    Run a scenario by name.

    Args:
        scenario_name: Name of the scenario to run.
        context: PyRIT context with loaded registries.
        target_name: Name of a registered target from the TargetRegistry to use as the
            objective target. Targets are registered by initializers (e.g., the 'target'
            initializer). Use --list-targets to see available names after initializers run.
        scenario_strategies: Optional list of strategy names.
        max_concurrency: Max concurrent operations.
        max_retries: Max retry attempts.
        memory_labels: Labels to attach to memory entries.
        dataset_names: Optional list of dataset names to use instead of scenario defaults.
            If provided, creates a new dataset configuration (fetches all items unless
            max_dataset_size is also specified).
        max_dataset_size: Optional maximum number of items to use from the dataset.
            If dataset_names is provided, limits items from the new datasets.
            If only max_dataset_size is provided, overrides the scenario's default limit.
        print_summary: Whether to print the summary after execution. Defaults to True.

    Returns:
        ScenarioResult: The result of the scenario execution.

    Raises:
        ValueError: If scenario not found, target not found, or fails to run.

    Note:
        Initializers from PyRITContext will be run before the scenario executes.
    """
    # Ensure context is initialized first (loads registries)
    # This must happen BEFORE we run initializers to avoid double-initialization
    if not context._initialized:
        await context.initialize_async()

    # Run initializers before scenario
    initializer_instances = None
    if context._initializer_configs:
        print(f"Running {len(context._initializer_configs)} initializer(s)...")
        sys.stdout.flush()

        initializer_instances = []

        for config in context._initializer_configs:
            initializer_class = context.initializer_registry.get_class(config.name)
            instance = initializer_class()
            if config.args:
                instance.set_params_from_args(args=config.args)
            initializer_instances.append(instance)

    # Re-initialize PyRIT with the scenario-specific initializers
    # This resets memory and applies initializer defaults
    await initialize_pyrit_async(
        memory_db_type=context._database,
        initialization_scripts=context._initialization_scripts,
        initializers=initializer_instances,
        env_files=context._env_files,
        silent=getattr(context, "_silent_reinit", False),
    )

    # Resolve objective target from TargetRegistry
    if target_name is not None:
        target_registry = TargetRegistry.get_registry_singleton()
        objective_target = target_registry.get_instance_by_name(target_name)
        if objective_target is None:
            available_names = target_registry.get_names()
            if not available_names:
                raise ValueError(
                    f"Target '{target_name}' not found. The target registry is empty.\n"
                    "Targets are registered by initializers. Make sure to include an initializer "
                    "that registers targets (e.g., --initializers target)."
                )
            raise ValueError(
                f"Target '{target_name}' not found in registry.\nAvailable targets: {', '.join(available_names)}"
            )
    else:
        objective_target = None

    # Get scenario class
    scenario_class = context.scenario_registry.get_class(scenario_name)

    if scenario_class is None:
        available = ", ".join(context.scenario_registry.get_names())
        raise ValueError(f"Scenario '{scenario_name}' not found.\nAvailable scenarios: {available}")

    # Build initialization kwargs (these go to initialize_async, not __init__)
    init_kwargs: dict[str, Any] = {}

    if objective_target is not None:
        init_kwargs["objective_target"] = objective_target

    if scenario_strategies:
        strategy_class = scenario_class.get_strategy_class()
        strategy_enums = []
        for name in scenario_strategies:
            try:
                strategy_enums.append(strategy_class(name))
            except ValueError:
                available_strategies = [s.value for s in strategy_class]
                raise ValueError(
                    f"Strategy '{name}' not found for scenario '{scenario_name}'. "
                    f"Available: {', '.join(available_strategies)}"
                ) from None
        init_kwargs["scenario_strategies"] = strategy_enums

    if max_concurrency is not None:
        init_kwargs["max_concurrency"] = max_concurrency
    if max_retries is not None:
        init_kwargs["max_retries"] = max_retries
    if memory_labels is not None:
        init_kwargs["memory_labels"] = memory_labels

    # Build dataset_config based on CLI args:
    # - No args: scenario uses its default_dataset_config()
    # - dataset_names only: new config with those datasets, fetches all items
    # - dataset_names + max_dataset_size: new config with limited items
    # - max_dataset_size only: default datasets with overridden limit
    if dataset_names:
        # User specified dataset names - create new config (fetches all unless max_dataset_size set)
        init_kwargs["dataset_config"] = DatasetConfiguration(
            dataset_names=dataset_names,
            max_dataset_size=max_dataset_size,
        )
    elif max_dataset_size is not None:
        # User only specified max_dataset_size - override default config's limit
        default_config = scenario_class.default_dataset_config()
        default_config.max_dataset_size = max_dataset_size
        init_kwargs["dataset_config"] = default_config

    # Instantiate and run
    print(f"\nRunning scenario: {scenario_name}")
    sys.stdout.flush()

    # Scenarios here are a concrete subclass
    # Runtime parameters are passed to initialize_async()
    scenario = scenario_class()  # type: ignore[call-arg]
    await scenario.initialize_async(**init_kwargs)
    result = await scenario.run_async()

    # Print results if requested
    if print_summary:
        printer = ConsoleScenarioResultPrinter()
        await printer.print_summary_async(result)

    return result


def _format_wrapped_text(*, text: str, indent: str, width: int = 78) -> str:
    """
    Format text with word wrapping.

    Args:
        text: Text to wrap.
        indent: Indentation string for wrapped lines.
        width: Maximum line width. Defaults to 78.

    Returns:
        Formatted text with line breaks.
    """
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        if not current_line:
            current_line = word
        elif len(current_line) + len(word) + 1 + len(indent) <= width:
            current_line += " " + word
        else:
            lines.append(indent + current_line)
            current_line = word

    if current_line:
        lines.append(indent + current_line)

    return "\n".join(lines)


def _print_header(*, text: str) -> None:
    """
    Print a colored header if termcolor is available.

    Args:
        text: Header text to print.
    """
    if HAS_TERMCOLOR:
        termcolor.cprint(f"\n  {text}", "cyan", attrs=["bold"])
    else:
        print(f"\n  {text}")


def format_scenario_metadata(*, scenario_metadata: ScenarioMetadata) -> None:
    """
    Print formatted information about a scenario class.

    Args:
        scenario_metadata: Dataclass containing scenario metadata.
    """
    _print_header(text=scenario_metadata.registry_name)
    print(f"    Class: {scenario_metadata.class_name}")

    description = scenario_metadata.class_description
    if description:
        print("    Description:")
        print(_format_wrapped_text(text=description, indent="      "))

    if scenario_metadata.aggregate_strategies:
        agg_strategies = scenario_metadata.aggregate_strategies
        print("    Aggregate Strategies:")
        formatted = _format_wrapped_text(text=", ".join(agg_strategies), indent="      - ")
        print(formatted)

    if scenario_metadata.all_strategies:
        strategies = scenario_metadata.all_strategies
        print(f"    Available Strategies ({len(strategies)}):")
        formatted = _format_wrapped_text(text=", ".join(strategies), indent="      ")
        print(formatted)

    if scenario_metadata.default_strategy:
        print(f"    Default Strategy: {scenario_metadata.default_strategy}")

    if scenario_metadata.default_datasets:
        datasets = scenario_metadata.default_datasets
        max_size = scenario_metadata.max_dataset_size
        if datasets:
            size_suffix = f", max {max_size} per dataset" if max_size else ""
            print(f"    Default Datasets ({len(datasets)}{size_suffix}):")
            formatted = _format_wrapped_text(text=", ".join(datasets), indent="      ")
            print(formatted)
        else:
            print("    Default Datasets: None")


def format_initializer_metadata(*, initializer_metadata: InitializerMetadata) -> None:
    """
    Print formatted information about an initializer class.

    Args:
        initializer_metadata: Dataclass containing initializer metadata.
    """
    _print_header(text=initializer_metadata.registry_name)
    print(f"    Class: {initializer_metadata.class_name}")
    print(f"    Name: {initializer_metadata.display_name}")
    print(f"    Execution Order: {initializer_metadata.execution_order}")

    if initializer_metadata.required_env_vars:
        print("    Required Environment Variables:")
        for env_var in initializer_metadata.required_env_vars:
            print(f"      - {env_var}")
    else:
        print("    Required Environment Variables: None")

    if initializer_metadata.supported_parameters:
        print("    Supported Parameters:")
        for param_name, param_desc, param_required, param_default in initializer_metadata.supported_parameters:
            req_str = " (required)" if param_required else ""
            default_str = f" [default: {param_default}]" if param_default else ""
            print(f"      - {param_name}{req_str}{default_str}: {param_desc}")

    if initializer_metadata.class_description:
        print("    Description:")
        print(_format_wrapped_text(text=initializer_metadata.class_description, indent="      "))


def resolve_initialization_scripts(script_paths: list[str]) -> list[Path]:
    """
    Resolve initialization script paths.

    Args:
        script_paths: List of script path strings.

    Returns:
        List of resolved Path objects.

    Raises:
        FileNotFoundError: If a script path does not exist.
    """
    return InitializerRegistry.resolve_script_paths(script_paths=script_paths)


async def print_scenarios_list_async(*, context: FrontendCore) -> int:
    """
    Print a formatted list of all available scenarios.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        Exit code (0 for success).
    """
    scenarios = await list_scenarios_async(context=context)

    if not scenarios:
        print("No scenarios found.")
        return 0

    print("\nAvailable Scenarios:")
    print("=" * 80)
    for scenario_metadata in scenarios:
        format_scenario_metadata(scenario_metadata=scenario_metadata)
    print("\n" + "=" * 80)
    print(f"\nTotal scenarios: {len(scenarios)}")
    return 0


async def print_initializers_list_async(*, context: FrontendCore) -> int:
    """
    Print a formatted list of all available initializers.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        Exit code (0 for success).
    """
    initializers = await list_initializers_async(context=context)

    if not initializers:
        print("No initializers found.")
        return 0

    print("\nAvailable Initializers:")
    print("=" * 80)
    for initializer_metadata in initializers:
        format_initializer_metadata(initializer_metadata=initializer_metadata)
    print("\n" + "=" * 80)
    print(f"\nTotal initializers: {len(initializers)}")
    return 0


async def print_targets_list_async(*, context: FrontendCore) -> int:
    """
    Print a formatted list of all available targets from the TargetRegistry.

    Targets are registered by initializers, so this requires initializers to run first.
    If no targets are found, prints a hint about using the 'target' initializer.

    Args:
        context: PyRIT context with loaded registries.

    Returns:
        Exit code (0 for success).
    """
    target_names = await list_targets_async(context=context)

    if not target_names:
        print("\nNo targets found in registry.")
        print(
            "\nTargets are registered by initializers. Include an initializer that registers "
            "targets, for example:\n  --initializers target\n"
        )
        return 0

    target_registry = TargetRegistry.get_registry_singleton()

    print("\nRegistered Targets:")
    print("=" * 80)
    for name in target_names:
        target = target_registry.get_instance_by_name(name)
        if target is None:
            print(f"  {name}")
            continue

        model = target._underlying_model or target._model_name or ""
        endpoint = target._endpoint or ""
        class_name = type(target).__name__

        _print_header(text=name)
        print(f"    Class: {class_name}")
        if model:
            print(f"    Model: {model}")
        if endpoint:
            print(f"    Endpoint: {endpoint}")
    print("\n" + "=" * 80)
    print(f"\nTotal targets: {len(target_names)}")
    return 0
