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
import dataclasses
import inspect
import json
import logging
import shlex
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


# ---------------------------------------------------------------------------
# Shell argument specification
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ArgSpec:
    """
    Declarative specification for a single shell-mode CLI argument.

    Each instance describes one CLI flag (or set of aliases) and how its
    value(s) should be collected and validated. A list of ``_ArgSpec`` objects
    is passed to ``_parse_shell_arguments`` which handles the actual parsing
    loop. Adding a new flag only requires defining a new ``_ArgSpec``
    constant, not editing any parsing logic.

    Attributes:
        flags: CLI flag strings that trigger this argument (e.g., ``["--strategies", "-s"]``).
        result_key: Key name in the returned dict (e.g., ``"scenario_strategies"``).
        multi_value: If True, collect values until the next flag.
            If False, consume exactly one value.
        parser: Optional callable to transform each raw string value.
            Applied per-item for multi-value args, or to the single value otherwise.
    """

    flags: list[str]
    result_key: str
    multi_value: bool = False
    parser: Callable[[str], Any] | None = None


_INITIALIZERS_ARG = _ArgSpec(
    flags=["--initializers"],
    result_key="initializers",
    multi_value=True,
    parser=_parse_initializer_arg,
)
_INIT_SCRIPTS_ARG = _ArgSpec(
    flags=["--initialization-scripts"],
    result_key="initialization_scripts",
    multi_value=True,
)

_STRATEGIES_ARG = _ArgSpec(
    flags=["--strategies", "-s"],
    result_key="scenario_strategies",
    multi_value=True,
)
_MAX_CONCURRENCY_ARG = _ArgSpec(
    flags=["--max-concurrency"],
    result_key="max_concurrency",
    parser=lambda v: validate_integer(v, name="--max-concurrency", min_value=1),
)
_MAX_RETRIES_ARG = _ArgSpec(
    flags=["--max-retries"],
    result_key="max_retries",
    parser=lambda v: validate_integer(v, name="--max-retries", min_value=0),
)
_MEMORY_LABELS_ARG = _ArgSpec(
    flags=["--memory-labels"],
    result_key="memory_labels",
    parser=parse_memory_labels,
)
_LOG_LEVEL_ARG = _ArgSpec(
    flags=["--log-level"],
    result_key="log_level",
    parser=lambda v: validate_log_level(log_level=v),
)
_DATASET_NAMES_ARG = _ArgSpec(
    flags=["--dataset-names"],
    result_key="dataset_names",
    multi_value=True,
)
_MAX_DATASET_SIZE_ARG = _ArgSpec(
    flags=["--max-dataset-size"],
    result_key="max_dataset_size",
    parser=lambda v: validate_integer(v, name="--max-dataset-size", min_value=1),
)
_TARGET_ARG = _ArgSpec(
    flags=["--target"],
    result_key="target",
)

_RUN_ARG_SPECS: list[_ArgSpec] = [
    _INITIALIZERS_ARG,
    _INIT_SCRIPTS_ARG,
    _STRATEGIES_ARG,
    _MAX_CONCURRENCY_ARG,
    _MAX_RETRIES_ARG,
    _MEMORY_LABELS_ARG,
    _LOG_LEVEL_ARG,
    _DATASET_NAMES_ARG,
    _MAX_DATASET_SIZE_ARG,
    _TARGET_ARG,
]

_LIST_TARGETS_ARG_SPECS: list[_ArgSpec] = [
    _INITIALIZERS_ARG,
    _INIT_SCRIPTS_ARG,
]


# ---------------------------------------------------------------------------
# Generic shell argument parser
# ---------------------------------------------------------------------------


def _parse_shell_arguments(*, parts: list[str], arg_specs: list[_ArgSpec]) -> dict[str, Any]:
    """
    Parse a list of shell tokens against a set of argument specifications.

    Each ``_ArgSpec`` in *arg_specs* declares how its flag(s) should be handled
    (multi-value collection vs. single-value consumption) and what validation
    or transformation to apply.

    Args:
        parts: Token list (already split on whitespace, positional args removed).
        arg_specs: Argument specifications that this command accepts.

    Returns:
        Dictionary mapping each spec's ``result_key`` to its parsed value,
        defaulting to ``None`` for arguments not present in *parts*.

    Raises:
        ValueError: On unknown flags or missing values.
    """
    # Build lookup: flag string → spec
    flag_to_spec: dict[str, _ArgSpec] = {}
    for spec in arg_specs:
        for flag in spec.flags:
            flag_to_spec[flag] = spec

    # Initialise result with None defaults
    result: dict[str, Any] = {spec.result_key: None for spec in arg_specs}

    i = 0
    while i < len(parts):
        token = parts[i]
        matched_spec: _ArgSpec | None = flag_to_spec.get(token)

        if matched_spec is None:
            valid = sorted(flag_to_spec.keys())
            raise ValueError(f"Unknown argument: {token}. Valid arguments: {', '.join(valid)}")

        i += 1

        if matched_spec.multi_value:
            values: list[Any] = []
            # Collect values until the next flag (whether valid or invalid)
            while i < len(parts) and not (parts[i].startswith("--") or parts[i] in flag_to_spec):
                item = matched_spec.parser(parts[i]) if matched_spec.parser else parts[i]
                values.append(item)
                i += 1
            if len(values) == 0:
                raise ValueError(f"{matched_spec.flags[0]} requires at least one value")
            result[matched_spec.result_key] = values
        else:
            if i >= len(parts):
                raise ValueError(f"{matched_spec.flags[0]} requires a value")
            raw = parts[i]
            result[matched_spec.result_key] = matched_spec.parser(raw) if matched_spec.parser else raw
            i += 1

    return result


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
    parts = shlex.split(args_string)

    if not parts:
        raise ValueError("No scenario name provided")

    result = _parse_shell_arguments(parts=parts[1:], arg_specs=_RUN_ARG_SPECS)
    result["scenario_name"] = parts[0]
    return result


def parse_list_targets_arguments(*, args_string: str) -> dict[str, Any]:
    """
    Parse list-targets command arguments from a string (for shell mode).

    Args:
        args_string: Space-separated argument string (e.g., "--initializers target").

    Returns:
        Dictionary with parsed arguments:
            - initializers: Optional[list[str | dict[str, Any]]]
            - initialization_scripts: Optional[list[str]]

    Raises:
        ValueError: If parsing or validation fails.
    """
    parts = shlex.split(args_string)
    return _parse_shell_arguments(parts=parts, arg_specs=_LIST_TARGETS_ARG_SPECS)


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
