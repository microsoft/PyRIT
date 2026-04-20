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

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

try:
    import termcolor

    HAS_TERMCOLOR = True
except ImportError:
    HAS_TERMCOLOR = False

    # Create a dummy termcolor module for fallback
    class termcolor:  # type: ignore
        """Dummy termcolor fallback for colored printing if termcolor is not installed."""

        @staticmethod
        def cprint(text: str, color: str = None, attrs: list = None) -> None:  # type: ignore
            """Print text without color."""
            print(text)


if TYPE_CHECKING:
    from pyrit.models.scenario_result import ScenarioResult
    from pyrit.registry import (
        InitializerMetadata,
        InitializerRegistry,
        ScenarioMetadata,
        ScenarioRegistry,
    )

logger = logging.getLogger(__name__)

# Database type constants
IN_MEMORY = "InMemory"
SQLITE = "SQLite"
AZURE_SQL = "AzureSQL"


class FrontendCore:
    """
    Shared context for PyRIT operations.

    This object holds all the registries and configuration needed to run
    scenarios. It can be created once (for shell) or per-command (for CLI).
    """

    def __init__(
        self,
        *,
        database: str = SQLITE,
        initialization_scripts: Optional[list[Path]] = None,
        initializer_names: Optional[list[str]] = None,
        env_files: Optional[list[Path]] = None,
        log_level: str = "WARNING",
    ):
        """
        Initialize PyRIT context.

        Args:
            database: Database type (InMemory, SQLite, or AzureSQL).
            initialization_scripts: Optional list of initialization script paths.
            initializer_names: Optional list of built-in initializer names to run.
            env_files: Optional list of environment file paths to load in order.
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Defaults to WARNING.

        Raises:
            ValueError: If database or log_level are invalid.
        """
        # Validate inputs
        self._database = validate_database(database=database)
        self._initialization_scripts = initialization_scripts
        self._initializer_names = initializer_names
        self._env_files = env_files
        self._log_level = validate_log_level(log_level=log_level)

        # Lazy-loaded registries
        self._scenario_registry: Optional[ScenarioRegistry] = None
        self._initializer_registry: Optional[InitializerRegistry] = None
        self._initialized = False

        # Configure logging
        logging.basicConfig(level=getattr(logging, self._log_level))

    async def initialize_async(self) -> None:
        """Initialize PyRIT and load registries (heavy operation)."""
        if self._initialized:
            return

        from pyrit.registry import InitializerRegistry, ScenarioRegistry
        from pyrit.setup import initialize_pyrit_async

        # Initialize PyRIT without initializers (they run per-scenario)
        await initialize_pyrit_async(
            memory_db_type=self._database,
            initialization_scripts=None,
            initializers=None,
            env_files=self._env_files,
        )

        # Load registries (use singleton pattern for shared access)
        self._scenario_registry = ScenarioRegistry.get_registry_singleton()
        if self._initialization_scripts:
            print("Discovering user scenarios...")
            sys.stdout.flush()
            self._scenario_registry.discover_user_scenarios()

        self._initializer_registry = InitializerRegistry()

        self._initialized = True

    @property
    def scenario_registry(self) -> "ScenarioRegistry":
        """
        Get the scenario registry. Must call await initialize_async() first.

        Raises:
            RuntimeError: If initialize_async() has not been called.
        """
        if not self._initialized:
            raise RuntimeError(
                "FrontendCore not initialized. Call 'await context.initialize_async()' before accessing registries."
            )
        assert self._scenario_registry is not None
        return self._scenario_registry

    @property
    def initializer_registry(self) -> "InitializerRegistry":
        """
        Get the initializer registry. Must call await initialize_async() first.

        Raises:
            RuntimeError: If initialize_async() has not been called.
        """
        if not self._initialized:
            raise RuntimeError(
                "FrontendCore not initialized. Call 'await context.initialize_async()' before accessing registries."
            )
        assert self._initializer_registry is not None
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
    *, context: FrontendCore, discovery_path: Optional[Path] = None
) -> "Sequence[InitializerMetadata]":
    """
    List metadata for all available initializers.

    Args:
        context: PyRIT context with loaded registries.
        discovery_path: Optional path to discover initializers from.

    Returns:
        Sequence of initializer metadata dictionaries describing each initializer class.
    """
    if discovery_path:
        from pyrit.registry import InitializerRegistry

        registry = InitializerRegistry(discovery_path=discovery_path)
        return registry.list_metadata()

    if not context._initialized:
        await context.initialize_async()
    return context.initializer_registry.list_metadata()


async def run_scenario_async(
    *,
    scenario_name: str,
    context: FrontendCore,
    scenario_strategies: Optional[list[str]] = None,
    max_concurrency: Optional[int] = None,
    max_retries: Optional[int] = None,
    memory_labels: Optional[dict[str, str]] = None,
    dataset_names: Optional[list[str]] = None,
    max_dataset_size: Optional[int] = None,
    print_summary: bool = True,
) -> "ScenarioResult":
    """
    Run a scenario by name.

    Args:
        scenario_name: Name of the scenario to run.
        context: PyRIT context with loaded registries.
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
        ValueError: If scenario not found or fails to run.

    Note:
        Initializers from PyRITContext will be run before the scenario executes.
    """
    from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
    from pyrit.setup import initialize_pyrit_async

    # Ensure context is initialized first (loads registries)
    # This must happen BEFORE we run initializers to avoid double-initialization
    if not context._initialized:
        await context.initialize_async()

    # Run initializers before scenario
    initializer_instances = None
    if context._initializer_names:
        print(f"Running {len(context._initializer_names)} initializer(s)...")
        sys.stdout.flush()

        initializer_instances = []

        for name in context._initializer_names:
            initializer_class = context.initializer_registry.get_class(name)
            initializer_instances.append(initializer_class())

    # Re-initialize PyRIT with the scenario-specific initializers
    # This resets memory and applies initializer defaults
    await initialize_pyrit_async(
        memory_db_type=context._database,
        initialization_scripts=context._initialization_scripts,
        initializers=initializer_instances,
        env_files=context._env_files,
    )

    # Get scenario class
    scenario_class = context.scenario_registry.get_class(scenario_name)

    if scenario_class is None:
        available = ", ".join(context.scenario_registry.get_names())
        raise ValueError(f"Scenario '{scenario_name}' not found.\nAvailable scenarios: {available}")

    # Build initialization kwargs (these go to initialize_async, not __init__)
    init_kwargs: dict[str, Any] = {}

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
        from pyrit.scenario import DatasetConfiguration

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
    _print_header(text=scenario_metadata.snake_class_name)
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


def format_initializer_metadata(*, initializer_metadata: "InitializerMetadata") -> None:
    """
    Print formatted information about an initializer class.

    Args:
        initializer_metadata: Dataclass containing initializer metadata.
    """
    _print_header(text=initializer_metadata.snake_class_name)
    print(f"    Class: {initializer_metadata.class_name}")
    print(f"    Name: {initializer_metadata.display_name}")
    print(f"    Execution Order: {initializer_metadata.execution_order}")

    if initializer_metadata.required_env_vars:
        print("    Required Environment Variables:")
        for env_var in initializer_metadata.required_env_vars:
            print(f"      - {env_var}")
    else:
        print("    Required Environment Variables: None")

    if initializer_metadata.class_description:
        print("    Description:")
        print(_format_wrapped_text(text=initializer_metadata.class_description, indent="      "))


def validate_database(*, database: str) -> str:
    """
    Validate database type.

    Args:
        database: Database type string.

    Returns:
        Validated database type.

    Raises:
        ValueError: If database type is invalid.
    """
    valid_databases = [IN_MEMORY, SQLITE, AZURE_SQL]
    if database not in valid_databases:
        raise ValueError(f"Invalid database type: {database}. Must be one of: {', '.join(valid_databases)}")
    return database


def validate_log_level(*, log_level: str) -> str:
    """
    Validate log level.

    Args:
        log_level: Log level string (case-insensitive).

    Returns:
        Validated log level in uppercase.

    Raises:
        ValueError: If log level is invalid.
    """
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    level_upper = log_level.upper()
    if level_upper not in valid_levels:
        raise ValueError(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels)}")
    return level_upper


def validate_integer(value: str, *, name: str = "value", min_value: Optional[int] = None) -> int:
    """
    Validate and parse an integer value.

    Note: The 'value' parameter is positional (not keyword-only) to allow use with
    argparse lambdas like: lambda v: validate_integer(v, min_value=1).
    This is an exception to the PyRIT style guide for argparse compatibility.

    Args:
        value: String value to parse.
        name: Parameter name for error messages. Defaults to "value".
        min_value: Optional minimum value constraint.

    Returns:
        Parsed integer.

    Raises:
        ValueError: If value is not a valid integer or violates constraints.
    """
    # Reject boolean types explicitly (int(True) == 1, int(False) == 0)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer string, got boolean: {value}")

    # Ensure value is a string
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}: {value}")

    # Strip whitespace and validate it looks like an integer
    value = value.strip()
    if not value:
        raise ValueError(f"{name} cannot be empty")

    try:
        int_value = int(value)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{name} must be an integer, got: {value}") from e

    if min_value is not None and int_value < min_value:
        raise ValueError(f"{name} must be at least {min_value}, got: {int_value}")

    return int_value


def _argparse_validator(validator_func: Callable[..., Any]) -> Callable[[Any], Any]:
    """
    Adapt a validator to argparse by converting ValueError to ArgumentTypeError.

    This decorator adapts our keyword-only validators for use with argparse's type= parameter.
    It handles two challenges:

    1. Exception Translation: argparse expects ArgumentTypeError, but our validators raise
       ValueError. This decorator catches ValueError and re-raises as ArgumentTypeError.

    2. Keyword-Only Parameters: PyRIT validators use keyword-only parameters (e.g.,
       validate_database(*, database: str)), but argparse's type= passes a positional argument.
       This decorator inspects the function signature and calls the validator with the correct
       keyword argument name.

    This pattern allows us to:
    - Keep validators as pure functions with proper type hints
    - Follow PyRIT style guide (keyword-only parameters)
    - Reuse the same validation logic in both argparse and non-argparse contexts

    Args:
        validator_func: Function that raises ValueError on invalid input.
            Must have at least one parameter (can be keyword-only).

    Returns:
        Wrapped function that:
        - Accepts a single positional argument (for argparse compatibility)
        - Calls validator_func with the correct keyword argument
        - Raises ArgumentTypeError instead of ValueError

    Raises:
        ValueError: If validator_func has no parameters.
    """
    import inspect

    # Get the first parameter name from the function signature
    sig = inspect.signature(validator_func)
    params = list(sig.parameters.keys())
    if not params:
        raise ValueError(f"Validator function {validator_func.__name__} must have at least one parameter")
    first_param = params[0]

    def wrapper(value: Any) -> Any:
        import argparse as ap

        try:
            # Call with keyword argument to support keyword-only parameters
            return validator_func(**{first_param: value})
        except ValueError as e:
            raise ap.ArgumentTypeError(str(e)) from e

    # Preserve function metadata for better debugging
    wrapper.__name__ = getattr(validator_func, "__name__", "argparse_validator")
    wrapper.__doc__ = getattr(validator_func, "__doc__", None)
    return wrapper


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
    from pyrit.registry import InitializerRegistry

    return InitializerRegistry.resolve_script_paths(script_paths=script_paths)


def resolve_env_files(*, env_file_paths: list[str]) -> list[Path]:
    """
    Resolve environment file paths to absolute Path objects.

    Args:
        env_file_paths: List of environment file path strings.

    Returns:
        List of resolved Path objects.

    Raises:
        ValueError: If any path does not exist.
    """
    resolved_paths = []
    for path_str in env_file_paths:
        path = Path(path_str).resolve()
        if not path.exists():
            raise ValueError(f"Environment file not found: {path}")
        resolved_paths.append(path)
    return resolved_paths


# Argparse-compatible validators
#
# These wrappers adapt our core validators (which use keyword-only parameters and raise
# ValueError) for use with argparse's type= parameter (which passes positional arguments
# and expects ArgumentTypeError).
#
# Pattern:
#   - Use core validators (validate_database, validate_log_level, etc.) in regular code
#   - Use these _argparse versions ONLY in parser.add_argument(..., type=...)
#
# The lambda wrappers for validate_integer are necessary because we need to partially
# apply the min_value parameter while still allowing the decorator to work correctly.
validate_database_argparse = _argparse_validator(validate_database)
validate_log_level_argparse = _argparse_validator(validate_log_level)
positive_int = _argparse_validator(lambda v: validate_integer(v, min_value=1))
non_negative_int = _argparse_validator(lambda v: validate_integer(v, min_value=0))
resolve_env_files_argparse = _argparse_validator(resolve_env_files)


def parse_memory_labels(json_string: str) -> dict[str, str]:
    """
    Parse memory labels from a JSON string.

    Args:
        json_string: JSON string containing label key-value pairs.

    Returns:
        Dictionary of labels.

    Raises:
        ValueError: If JSON is invalid or contains non-string values.
    """
    try:
        labels = json.loads(json_string)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for memory labels: {e}") from e

    if not isinstance(labels, dict):
        raise ValueError("Memory labels must be a JSON object (dictionary)")

    # Validate all keys and values are strings
    for key, value in labels.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"All label keys and values must be strings. Got: {key}={value}")

    return labels


def get_default_initializer_discovery_path() -> Path:
    """
    Get the default path for discovering initializers.

    Returns:
        Path to the scenarios initializers directory.
    """
    PYRIT_PATH = Path(__file__).parent.parent.resolve()
    return PYRIT_PATH / "setup" / "initializers" / "scenarios"


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


async def print_initializers_list_async(*, context: FrontendCore, discovery_path: Optional[Path] = None) -> int:
    """
    Print a formatted list of all available initializers.

    Args:
        context: PyRIT context with loaded registries.
        discovery_path: Optional path to discover initializers from.

    Returns:
        Exit code (0 for success).
    """
    initializers = await list_initializers_async(context=context, discovery_path=discovery_path)

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


# Shared argument help text
ARG_HELP = {
    "initializers": "Built-in initializer names to run before the scenario (e.g., openai_objective_target)",
    "initialization_scripts": "Paths to custom Python initialization scripts to run before the scenario",
    "env_files": "Paths to environment files to load in order (e.g., .env.production .env.local). Later files "
    "override earlier ones.",
    "scenario_strategies": "List of strategy names to run (e.g., base64 rot13)",
    "max_concurrency": "Maximum number of concurrent attack executions (must be >= 1)",
    "max_retries": "Maximum number of automatic retries on exception (must be >= 0)",
    "memory_labels": 'Additional labels as JSON string (e.g., \'{"experiment": "test1"}\')',
    "database": "Database type to use for memory storage",
    "log_level": "Logging level",
    "dataset_names": "List of dataset names to use instead of scenario defaults (e.g., harmbench advbench). "
    "Creates a new dataset config; fetches all items unless --max-dataset-size is also specified",
    "max_dataset_size": "Maximum number of items to use from the dataset (must be >= 1). "
    "Limits new datasets if --dataset-names provided, otherwise overrides scenario's default limit",
}


def parse_run_arguments(*, args_string: str) -> dict[str, Any]:
    """
    Parse run command arguments from a string (for shell mode).

    Args:
        args_string: Space-separated argument string (e.g., "scenario_name --initializers foo --strategies bar").

    Returns:
        Dictionary with parsed arguments:
            - scenario_name: str
            - initializers: Optional[list[str]]
            - initialization_scripts: Optional[list[str]]
            - scenario_strategies: Optional[list[str]]
            - max_concurrency: Optional[int]
            - max_retries: Optional[int]
            - memory_labels: Optional[dict[str, str]]
            - database: Optional[str]
            - log_level: Optional[str]
            - dataset_names: Optional[list[str]]
            - max_dataset_size: Optional[int]

    Raises:
        ValueError: If parsing or validation fails.
    """
    parts = args_string.split()

    if not parts:
        raise ValueError("No scenario name provided")

    result: dict[str, Any] = {
        "scenario_name": parts[0],
        "initializers": None,
        "initialization_scripts": None,
        "env_files": None,
        "scenario_strategies": None,
        "max_concurrency": None,
        "max_retries": None,
        "memory_labels": None,
        "database": None,
        "log_level": None,
        "dataset_names": None,
        "max_dataset_size": None,
    }

    i = 1
    while i < len(parts):
        if parts[i] == "--initializers":
            # Collect initializers until next flag
            result["initializers"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["initializers"].append(parts[i])
                i += 1
        elif parts[i] == "--initialization-scripts":
            # Collect script paths until next flag
            result["initialization_scripts"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["initialization_scripts"].append(parts[i])
                i += 1
        elif parts[i] == "--env-files":
            # Collect env file paths until next flag
            result["env_files"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["env_files"].append(parts[i])
                i += 1
        elif parts[i] in ("--strategies", "-s"):
            # Collect strategies until next flag
            result["scenario_strategies"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--") and parts[i] != "-s":
                result["scenario_strategies"].append(parts[i])
                i += 1
        elif parts[i] == "--max-concurrency":
            i += 1
            if i >= len(parts):
                raise ValueError("--max-concurrency requires a value")
            result["max_concurrency"] = validate_integer(parts[i], name="--max-concurrency", min_value=1)
            i += 1
        elif parts[i] == "--max-retries":
            i += 1
            if i >= len(parts):
                raise ValueError("--max-retries requires a value")
            result["max_retries"] = validate_integer(parts[i], name="--max-retries", min_value=0)
            i += 1
        elif parts[i] == "--memory-labels":
            i += 1
            if i >= len(parts):
                raise ValueError("--memory-labels requires a value")
            result["memory_labels"] = parse_memory_labels(parts[i])
            i += 1
        elif parts[i] == "--database":
            i += 1
            if i >= len(parts):
                raise ValueError("--database requires a value")
            result["database"] = validate_database(database=parts[i])
            i += 1
        elif parts[i] == "--log-level":
            i += 1
            if i >= len(parts):
                raise ValueError("--log-level requires a value")
            result["log_level"] = validate_log_level(log_level=parts[i])
            i += 1
        elif parts[i] == "--dataset-names":
            # Collect dataset names until next flag
            result["dataset_names"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["dataset_names"].append(parts[i])
                i += 1
        elif parts[i] == "--max-dataset-size":
            i += 1
            if i >= len(parts):
                raise ValueError("--max-dataset-size requires a value")
            result["max_dataset_size"] = validate_integer(parts[i], name="--max-dataset-size", min_value=1)
            i += 1
        else:
            logger.warning(f"Unknown argument: {parts[i]}")
            i += 1

    return result
