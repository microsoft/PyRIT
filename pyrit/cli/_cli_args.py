# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight shared CLI argument definitions for PyRIT frontends.

This module contains constants, validators, help text, and argument parsers
that are shared between ``pyrit_shell``, ``pyrit_scan``, and other CLI entry
points.  It intentionally avoids heavy imports (no ``pyrit.scenario``,
``pyrit.registry``, ``pyrit.setup``, etc.) so it can be loaded quickly for
argument parsing before the full runtime is initialised.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Database type constants
# ---------------------------------------------------------------------------
IN_MEMORY = "InMemory"
SQLITE = "SQLite"
AZURE_SQL = "AzureSQL"


# ---------------------------------------------------------------------------
# Pure validators
# ---------------------------------------------------------------------------


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


def validate_log_level(*, log_level: str) -> int:
    """
    Validate log level and convert to logging constant.

    Args:
        log_level: Log level string (case-insensitive).

    Returns:
        Validated log level as logging constant (e.g., logging.WARNING).

    Raises:
        ValueError: If log level is invalid.
    """
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    level_upper = log_level.upper()
    if level_upper not in valid_levels:
        raise ValueError(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels)}")
    level_value: int = getattr(logging, level_upper)
    return level_value


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


# ---------------------------------------------------------------------------
# Argparse adapter
# ---------------------------------------------------------------------------


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
    # Get the first parameter name from the function signature
    sig = inspect.signature(validator_func)
    params = list(sig.parameters.keys())
    if not params:
        raise ValueError(f"Validator function {validator_func.__name__} must have at least one parameter")
    first_param = params[0]

    def wrapper(value: Any) -> Any:
        try:
            # Call with keyword argument to support keyword-only parameters
            return validator_func(**{first_param: value})
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e)) from e

    # Preserve function metadata for better debugging
    wrapper.__name__ = getattr(validator_func, "__name__", "argparse_validator")
    wrapper.__doc__ = getattr(validator_func, "__doc__", None)
    return wrapper


# ---------------------------------------------------------------------------
# Path / env-file helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
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
# ---------------------------------------------------------------------------
validate_database_argparse = _argparse_validator(validate_database)
validate_log_level_argparse = _argparse_validator(validate_log_level)
positive_int = _argparse_validator(lambda v: validate_integer(v, min_value=1))
non_negative_int = _argparse_validator(lambda v: validate_integer(v, min_value=0))
resolve_env_files_argparse = _argparse_validator(resolve_env_files)


# ---------------------------------------------------------------------------
# Memory label / argument parsing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Shared argument help text
# ---------------------------------------------------------------------------
ARG_HELP = {
    "config_file": (
        "Path to a YAML configuration file. Allows specifying database, initializers (with args), "
        "initialization scripts, and env files. CLI arguments override config file values. "
        "If not specified, ~/.pyrit/.pyrit_conf is loaded if it exists."
    ),
    "initializers": (
        "Built-in initializer names to run before the scenario. "
        "Supports optional params with name:key=val syntax "
        "(e.g., target:tags=default,scorer dataset:mode=strict)"
    ),
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
    "target": "Name of a registered target from the TargetRegistry to use as the objective target. "
    "Targets are registered by initializers (e.g., 'target' initializer). "
    "Use --list-targets to see available target names after initializers have run",
}


# ---------------------------------------------------------------------------
# Initializer argument parsing
# ---------------------------------------------------------------------------


def _parse_initializer_arg(arg: str) -> str | dict[str, Any]:
    """
    Parse an initializer CLI argument into a string or dict for ConfigurationLoader.

    Supports two formats:
    - Simple name: "simple" → "simple"
    - Name with params: "target:tags=default,scorer" → {"name": "target", "args": {"tags": ["default", "scorer"]}}

    For multiple params on one initializer, separate with semicolons: "name:key1=val1;key2=val2"
    For multiple initializers with params, space-separate them: "target:tags=a,b dataset:mode=strict"

    Args:
        arg: The CLI argument string.

    Returns:
        str | dict[str, Any]: A plain name string, or a dict with 'name' and 'args' keys.

    Raises:
        ValueError: If the argument format is invalid.
    """
    if ":" not in arg:
        return arg

    name, params_str = arg.split(":", 1)
    if not name:
        raise ValueError(f"Invalid initializer argument '{arg}': missing name before ':'")

    args: dict[str, list[str]] = {}
    for pair in params_str.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid initializer parameter '{pair}' in '{arg}': expected key=value format")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid initializer parameter in '{arg}': empty key")
        args[key] = [v.strip() for v in value.split(",")]

    if args:
        return {"name": name, "args": args}
    return name


def parse_run_arguments(*, args_string: str) -> dict[str, Any]:
    """
    Parse run command arguments from a string (for shell mode).

    Args:
        args_string: Space-separated argument string (e.g., "scenario_name --initializers foo --strategies bar").

    Returns:
        Dictionary with parsed arguments:
            - scenario_name: str
            - initializers: Optional[list[str | dict[str, Any]]]
            - initialization_scripts: Optional[list[str]]
            - scenario_strategies: Optional[list[str]]
            - max_concurrency: Optional[int]
            - max_retries: Optional[int]
            - memory_labels: Optional[dict[str, str]]
            - database: Optional[str]
            - log_level: Optional[int]
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
        "scenario_strategies": None,
        "max_concurrency": None,
        "max_retries": None,
        "memory_labels": None,
        "log_level": None,
        "dataset_names": None,
        "max_dataset_size": None,
        "target": None,
    }

    i = 1
    while i < len(parts):
        if parts[i] == "--initializers":
            # Collect initializers until next flag, parsing name:key=val syntax
            result["initializers"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["initializers"].append(_parse_initializer_arg(parts[i]))
                i += 1
        elif parts[i] == "--initialization-scripts":
            # Collect script paths until next flag
            result["initialization_scripts"] = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                result["initialization_scripts"].append(parts[i])
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
        elif parts[i] == "--target":
            i += 1
            if i >= len(parts):
                raise ValueError("--target requires a value")
            result["target"] = parts[i]
            i += 1
        else:
            raise ValueError(f"Unknown argument: {parts[i]}")

    return result


# ---------------------------------------------------------------------------
# Shared argparse builder
# ---------------------------------------------------------------------------


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments shared between pyrit_shell and pyrit_scan."""
    parser.add_argument("--config-file", type=Path, help=ARG_HELP["config_file"])
    parser.add_argument(
        "--log-level",
        type=validate_log_level_argparse,
        default=logging.WARNING,
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)",
    )


# Module-level logger (stdlib only — no heavy deps)
_logger = logging.getLogger(__name__)
