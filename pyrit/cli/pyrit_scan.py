# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT CLI - Command-line interface for running security scenarios.

This module provides the main entry point for the pyrit_scan command.
"""

import argparse
import asyncio
import logging
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Any, Optional

from pyrit.cli import frontend_core
from pyrit.common.parameter import Parameter, coerce_bool, coerce_scalar
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.core import Scenario

# Scenario-declared parameters land in the parsed Namespace under this prefix
# so they can be extracted unambiguously without comparing against a separate
# list of built-in flag names (avoids drift if a built-in flag is added).
_SCENARIO_DEST_PREFIX = "scenario__"

_DESCRIPTION = """PyRIT Scanner - Run security scenarios against AI systems

Examples:
  # List available scenarios, initializers, and targets
  pyrit_scan --list-scenarios
  pyrit_scan --list-initializers
  pyrit_scan --list-targets --initializers target

  # Run a scenario with a target and initializers
  pyrit_scan foundry.red_team_agent --target my_target --initializers target load_default_datasets

  # Run with a configuration file (recommended for complex setups)
  pyrit_scan foundry.red_team_agent --target my_target --config-file ./my_config.yaml

  # Run with custom initialization scripts
  pyrit_scan garak.encoding --target my_target --initialization-scripts ./my_config.py

  # Run specific strategies or options
  pyrit_scan foundry.red_team_agent --target my_target --strategies base64 rot13 --initializers target
  pyrit_scan foundry.red_team_agent --target my_target --initializers target --max-concurrency 10 --max-retries 3
"""


def _build_base_parser(*, add_help: bool = True) -> ArgumentParser:
    """
    Build the ``pyrit_scan`` argparse parser with the built-in (non-scenario) flags.

    Defined as a helper so the same flag definitions can be reused across the
    two-pass parsing flow that adds scenario-declared parameters dynamically:
    pass 1 builds the parser with ``add_help=False`` to identify the scenario
    name without firing on ``--help``; pass 2 builds the parser again with
    ``add_help=True`` and augments it with scenario-specific flags before
    parsing for real.

    Args:
        add_help (bool): Whether the parser should register the standard
            ``-h``/``--help`` action. Defaults to True.

    Returns:
        ArgumentParser: The parser with all built-in flags registered.
    """
    parser = ArgumentParser(
        prog="pyrit_scan",
        description=_DESCRIPTION,
        formatter_class=RawDescriptionHelpFormatter,
        add_help=add_help,
    )

    parser.add_argument(
        "--config-file",
        type=Path,
        help=frontend_core.ARG_HELP["config_file"],
    )

    parser.add_argument(
        "--log-level",
        type=frontend_core.validate_log_level_argparse,
        default=logging.WARNING,
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)",
    )

    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios and exit",
    )

    parser.add_argument(
        "--list-initializers",
        action="store_true",
        help="List all available scenario initializers and exit",
    )

    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="List all available targets from the TargetRegistry and exit. "
        "Requires initializers that register targets (e.g., --initializers target)",
    )

    parser.add_argument(
        "scenario_name",
        type=str,
        nargs="?",
        help="Name of the scenario to run",
    )

    parser.add_argument(
        "--initializers",
        type=frontend_core._parse_initializer_arg,
        nargs="+",
        help=frontend_core.ARG_HELP["initializers"],
    )

    parser.add_argument(
        "--initialization-scripts",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["initialization_scripts"],
    )

    parser.add_argument(
        "--strategies",
        "-s",
        type=str,
        nargs="+",
        dest="scenario_strategies",
        help=frontend_core.ARG_HELP["scenario_strategies"],
    )

    parser.add_argument(
        "--max-concurrency",
        type=frontend_core.positive_int,
        help=frontend_core.ARG_HELP["max_concurrency"],
    )

    parser.add_argument(
        "--max-retries",
        type=frontend_core.non_negative_int,
        help=frontend_core.ARG_HELP["max_retries"],
    )

    parser.add_argument(
        "--memory-labels",
        type=str,
        help=frontend_core.ARG_HELP["memory_labels"],
    )

    parser.add_argument(
        "--dataset-names",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["dataset_names"],
    )

    parser.add_argument(
        "--max-dataset-size",
        type=frontend_core.positive_int,
        help=frontend_core.ARG_HELP["max_dataset_size"],
    )

    parser.add_argument(
        "--target",
        type=str,
        help=frontend_core.ARG_HELP["target"],
    )

    return parser


def parse_args(args: Optional[list[str]] = None) -> Namespace:
    """
    Parse command-line arguments for the PyRIT scanner using a two-pass flow.

    Pass 1 builds the parser with ``add_help=False`` and uses
    ``parse_known_args`` to identify the positional scenario name without
    failing on unknown scenario-specific flags. If a registered built-in
    scenario is identified, its declared :class:`Parameter` list is added
    to the pass-2 parser as additional flags (kebab-cased, namespaced under
    ``scenario__<name>`` in the resulting Namespace, with type coercion via
    ``pyrit.common.parameter`` helpers and ``default=argparse.SUPPRESS`` so
    unset flags do not appear in the result).

    Pass 2 uses the augmented parser to do the real parse, so argparse
    handles typo errors, ``--help``, and per-flag type coercion uniformly.

    Args:
        args (Optional[list[str]]): Argument list (``sys.argv[1:]`` when None).

    Returns:
        Namespace: Parsed command-line arguments.
    """
    pass1_parser = _build_base_parser(add_help=False)
    parsed_pass1, _ = pass1_parser.parse_known_args(args)

    scenario_class = _resolve_scenario_class(parsed_pass1.scenario_name)

    pass2_parser = _build_base_parser(add_help=True)
    if scenario_class is not None:
        _add_scenario_params(parser=pass2_parser, declared=scenario_class.supported_parameters())

    return pass2_parser.parse_args(args)


def _resolve_scenario_class(scenario_name: Optional[str]) -> Optional[type[Scenario]]:
    """
    Look up a scenario class by name from the built-in registry.

    Returns ``None`` when the name is missing or unknown so that pass 2 can
    surface its own ``--help`` / list-mode / "scenario not found" UX without
    this helper short-circuiting it. v1 only resolves built-in scenarios;
    user-defined scenarios discovered through ``--initialization-scripts``
    are not augmented at parse time (the scripts are not executed here).

    Args:
        scenario_name (Optional[str]): Positional scenario name from pass 1.

    Returns:
        Optional[type[Scenario]]: The scenario class, or None.
    """
    if not scenario_name:
        return None
    registry = ScenarioRegistry.get_registry_singleton()
    try:
        return registry.get_class(scenario_name)
    except KeyError:
        return None


def _add_scenario_params(*, parser: ArgumentParser, declared: list[Parameter]) -> None:
    """
    Add scenario-declared parameters to ``parser`` as CLI flags.

    Each parameter becomes an argparse argument named ``--{kebab-case-name}``
    with ``dest=scenario__<name>``, ``default=argparse.SUPPRESS`` so absent
    flags do not appear in the parsed Namespace, and type/choices/nargs
    derived from the ``Parameter`` declaration. Coercion routes through
    the shared helpers in ``pyrit.common.parameter`` so the same
    bool/scalar handling applies whether the value enters through CLI,
    YAML, or programmatic ``set_params_from_args``.

    Args:
        parser (ArgumentParser): Parser to extend.
        declared (list[Parameter]): Scenario's declared parameters.

    Raises:
        ValueError: If a scenario-derived flag collides with a built-in flag.
    """
    existing_flags = {action_str for action in parser._actions for action_str in action.option_strings}
    for param in declared:
        flag = f"--{param.name.replace('_', '-')}"
        if flag in existing_flags:
            raise ValueError(
                f"Scenario parameter '{param.name}' collides with built-in flag {flag!r}. "
                f"Rename the parameter to avoid the collision."
            )
        kwargs: dict[str, Any] = {
            "dest": f"{_SCENARIO_DEST_PREFIX}{param.name}",
            "default": argparse.SUPPRESS,
            "help": param.description,
        }
        type_callable = _argparse_type_for(param=param)
        if type_callable is not None:
            kwargs["type"] = type_callable
        if _is_list_param(param.param_type):
            kwargs["nargs"] = "+"
        if param.choices is not None:
            kwargs["choices"] = list(param.choices)
        parser.add_argument(flag, **kwargs)


def _argparse_type_for(*, param: Parameter) -> Optional[Any]:
    """
    Return an argparse-compatible ``type=`` callable for a declared parameter.

    Maps each supported ``param_type`` to a coercion function that takes the
    raw string from argparse and returns the typed value. ``None``,
    ``str``, and list element types are returned as plain ``str`` (or None
    to skip type coercion); argparse handles those uniformly.

    Args:
        param (Parameter): The scenario-declared parameter.

    Returns:
        Optional[Any]: A callable suitable for argparse's ``type=`` keyword,
            or None if no coercion is needed (str / raw passthrough).
    """
    param_type = param.param_type
    if param_type is None or param_type is str:
        return None
    if param_type is bool:
        return lambda raw: coerce_bool(param_name=param.name, raw_value=raw)
    if param_type is int:
        return lambda raw: coerce_scalar(param_name=param.name, scalar_type=int, raw_value=raw)
    if param_type is float:
        return lambda raw: coerce_scalar(param_name=param.name, scalar_type=float, raw_value=raw)
    if _is_list_param(param_type):
        # nargs='+' applies type per element; v1 only supports list[str], so str(...)
        return str
    return None


def _is_list_param(param_type: Any) -> bool:
    """Return True when ``param_type`` is a parameterized list generic (e.g. ``list[str]``)."""
    from typing import get_origin

    return get_origin(param_type) is list


def _extract_scenario_args(*, parsed: Namespace) -> dict[str, Any]:
    """
    Extract scenario-declared parameter values from a parsed Namespace.

    Relies on the ``scenario__<name>`` dest namespacing established in
    ``_add_scenario_params`` so extraction does not depend on knowing
    the full set of built-in flag names.

    Args:
        parsed (Namespace): Result of ``ArgumentParser.parse_args``.

    Returns:
        dict[str, Any]: Map of original parameter name to coerced value.
            Empty when the scenario declares no parameters or the user
            supplied none.
    """
    return {
        key.removeprefix(_SCENARIO_DEST_PREFIX): value
        for key, value in vars(parsed).items()
        if key.startswith(_SCENARIO_DEST_PREFIX)
    }


def main(args: Optional[list[str]] = None) -> int:
    """
    Start the PyRIT scanner CLI.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    print("Starting PyRIT...")
    sys.stdout.flush()

    try:
        parsed_args = parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # Handle list commands (don't need full context)
    if parsed_args.list_scenarios:
        # Simple context just for listing
        initialization_scripts = None
        if parsed_args.initialization_scripts:
            try:
                initialization_scripts = frontend_core.resolve_initialization_scripts(
                    script_paths=parsed_args.initialization_scripts
                )
            except FileNotFoundError as e:
                print(f"Error: {e}")
                return 1

        context = frontend_core.FrontendCore(
            config_file=parsed_args.config_file,
            initialization_scripts=initialization_scripts,
            log_level=parsed_args.log_level,
        )

        return asyncio.run(frontend_core.print_scenarios_list_async(context=context))

    if parsed_args.list_initializers:
        context = frontend_core.FrontendCore(
            config_file=parsed_args.config_file,
            log_level=parsed_args.log_level,
        )
        return asyncio.run(frontend_core.print_initializers_list_async(context=context))

    if parsed_args.list_targets:
        # Need initializers or initialization scripts to populate the target registry
        initialization_scripts = None
        if parsed_args.initialization_scripts:
            try:
                initialization_scripts = frontend_core.resolve_initialization_scripts(
                    script_paths=parsed_args.initialization_scripts
                )
            except FileNotFoundError as e:
                print(f"Error: {e}")
                return 1

        context = frontend_core.FrontendCore(
            config_file=parsed_args.config_file,
            initialization_scripts=initialization_scripts,
            initializer_names=parsed_args.initializers,
            log_level=parsed_args.log_level,
        )
        return asyncio.run(frontend_core.print_targets_list_async(context=context))

    # Verify scenario was provided
    if not parsed_args.scenario_name:
        print("Error: No scenario specified. Use --help for usage information.")
        return 1

    # Run scenario
    try:
        # Collect initialization scripts
        initialization_scripts = None
        if parsed_args.initialization_scripts:
            initialization_scripts = frontend_core.resolve_initialization_scripts(
                script_paths=parsed_args.initialization_scripts
            )

        # Create context with initializers
        context = frontend_core.FrontendCore(
            config_file=parsed_args.config_file,
            initialization_scripts=initialization_scripts,
            initializer_names=parsed_args.initializers,
            log_level=parsed_args.log_level,
        )

        # Parse memory labels if provided
        memory_labels = None
        if parsed_args.memory_labels:
            memory_labels = frontend_core.parse_memory_labels(json_string=parsed_args.memory_labels)

        # Extract scenario-declared parameter values from the parsed Namespace
        # (those land under the scenario__ dest prefix during pass 2 augmentation).
        scenario_args = _extract_scenario_args(parsed=parsed_args)

        # Run scenario
        asyncio.run(
            frontend_core.run_scenario_async(
                scenario_name=parsed_args.scenario_name,
                context=context,
                target_name=parsed_args.target,
                scenario_strategies=parsed_args.scenario_strategies,
                max_concurrency=parsed_args.max_concurrency,
                max_retries=parsed_args.max_retries,
                memory_labels=memory_labels,
                dataset_names=parsed_args.dataset_names,
                max_dataset_size=parsed_args.max_dataset_size,
                scenario_args=scenario_args,
            )
        )
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
