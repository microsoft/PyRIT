# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT CLI - Command-line interface for running security scenarios.

This module provides the main entry point for the pyrit_scan command.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, get_origin

from pyrit.cli._cli_args import (
    ARG_HELP,
    _parse_initializer_arg,
    merge_config_scenario_args,
    non_negative_int,
    positive_int,
    validate_log_level_argparse,
)

if TYPE_CHECKING:
    from pyrit.common.parameter import Parameter
    from pyrit.scenario.core import Scenario

# Namespacing prefix for scenario-declared params on the parsed Namespace.
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

    Reused across the two-pass flow: pass 1 calls with ``add_help=False`` to
    identify the scenario name; pass 2 calls with ``add_help=True`` and adds
    scenario-declared params on top.

    Args:
        add_help (bool): Whether to register the standard ``-h``/``--help``
            action. Defaults to True.

    Returns:
        ArgumentParser: Parser with all built-in flags registered.
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
        help=ARG_HELP["config_file"],
    )

    parser.add_argument(
        "--log-level",
        type=validate_log_level_argparse,
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
        type=_parse_initializer_arg,
        nargs="+",
        help=ARG_HELP["initializers"],
    )

    parser.add_argument(
        "--initialization-scripts",
        type=str,
        nargs="+",
        help=ARG_HELP["initialization_scripts"],
    )

    parser.add_argument(
        "--strategies",
        "-s",
        type=str,
        nargs="+",
        dest="scenario_strategies",
        help=ARG_HELP["scenario_strategies"],
    )

    parser.add_argument(
        "--max-concurrency",
        type=positive_int,
        help=ARG_HELP["max_concurrency"],
    )

    parser.add_argument(
        "--max-retries",
        type=non_negative_int,
        help=ARG_HELP["max_retries"],
    )

    parser.add_argument(
        "--memory-labels",
        type=str,
        help=ARG_HELP["memory_labels"],
    )

    parser.add_argument(
        "--dataset-names",
        type=str,
        nargs="+",
        help=ARG_HELP["dataset_names"],
    )

    parser.add_argument(
        "--max-dataset-size",
        type=positive_int,
        help=ARG_HELP["max_dataset_size"],
    )

    parser.add_argument(
        "--target",
        type=str,
        help=ARG_HELP["target"],
    )

    return parser


def parse_args(args: Optional[list[str]] = None) -> Namespace:
    """
    Parse command-line arguments using a two-pass flow.

    Pass 1 identifies the scenario name with ``parse_known_args`` so unknown
    scenario flags don't fail. Pass 2 parses for real, with the resolved
    scenario's declared params added as namespaced flags.

    The scenario name may come from the CLI positional or, as a fallback, from
    the ``scenario.name`` block in ``--config-file`` (or the default config
    file). This mirrors the runtime behavior in ``main()`` so config-only
    scenario names can still expose their declared CLI flags.

    The CLI positional is only trusted when it resolves to a known scenario.
    Pass 1 doesn't yet know about scenario-declared flags, so ``parse_known_args``
    can greedily consume an unknown flag's value (e.g. the ``"7"`` in
    ``--max-turns 7``) as the positional. When that happens the positional won't
    resolve, and we fall back to the config peek.

    Args:
        args (Optional[list[str]]): Argument list (``sys.argv[1:]`` when None).

    Returns:
        Namespace: Parsed command-line arguments.
    """
    pass1_parser = _build_base_parser(add_help=False)
    parsed_pass1, _ = pass1_parser.parse_known_args(args)

    scenario_class = _resolve_scenario_class(parsed_pass1.scenario_name)
    if scenario_class is None:
        fallback_name = _peek_scenario_name_from_config(config_file=parsed_pass1.config_file)
        scenario_class = _resolve_scenario_class(fallback_name)

    pass2_parser = _build_base_parser(add_help=True)
    if scenario_class is not None:
        _add_scenario_params(parser=pass2_parser, declared=scenario_class.supported_parameters())

    return pass2_parser.parse_args(args)


def _peek_scenario_name_from_config(*, config_file: Optional[Path]) -> Optional[str]:
    """
    Best-effort lookup of the scenario name in layered config (default + explicit).

    Pass 1 of ``parse_args`` needs the scenario name to register that scenario's
    declared parameters as flags. Failures are swallowed: if the YAML is missing
    or malformed, return ``None`` and let ``main`` surface the canonical error.

    Args:
        config_file (Optional[Path]): Path from ``--config-file``.

    Returns:
        Optional[str]: The scenario name, or ``None`` if not configured / unavailable.
    """
    from pyrit.common.path import DEFAULT_CONFIG_PATH
    from pyrit.setup.configuration_loader import ConfigurationLoader

    paths: list[Path] = []
    if DEFAULT_CONFIG_PATH.exists():
        paths.append(DEFAULT_CONFIG_PATH)
    if config_file is not None and config_file.exists():
        paths.append(config_file)

    name: Optional[str] = None
    for path in paths:
        try:
            loaded = ConfigurationLoader.from_yaml_file(path)
        except Exception:
            continue
        if loaded.scenario_config is not None:
            name = loaded.scenario_config.name
    return name


def _resolve_scenario_class(scenario_name: Optional[str]) -> Optional[type[Scenario]]:
    """
    Look up a built-in scenario class by name. Returns None if missing or unknown.

    v1 limitation: user-defined scenarios from ``--initialization-scripts``
    are not augmented at parse time.

    Args:
        scenario_name (Optional[str]): Positional scenario name from pass 1.

    Returns:
        Optional[type[Scenario]]: The scenario class, or None.
    """
    if not scenario_name:
        return None
    from pyrit.registry import ScenarioRegistry

    registry = ScenarioRegistry.get_registry_singleton()
    try:
        return registry.get_class(scenario_name)
    except KeyError:
        return None


def _add_scenario_params(*, parser: ArgumentParser, declared: list[Parameter]) -> None:
    """
    Add scenario-declared parameters to ``parser`` as ``--kebab-case`` flags.

    Each flag uses ``dest=scenario__<name>``, ``default=argparse.SUPPRESS``,
    and a coercion ``type=`` from ``pyrit.common.parameter``.

    Args:
        parser (ArgumentParser): Parser to extend.
        declared (list[Parameter]): Scenario's declared parameters.

    Raises:
        ValueError: If a scenario-derived flag collides with a built-in flag or
            with another scenario param that normalizes to the same kebab form.
    """
    # Seed from existing flags so we catch built-in collisions; grow as we add.
    seen_flags: set[str] = set(parser._option_string_actions.keys())
    for param in declared:
        flag = f"--{param.name.replace('_', '-')}"
        if flag in seen_flags:
            raise ValueError(
                f"Scenario parameter '{param.name}' collides with an existing flag {flag!r}. "
                f"This is either a built-in CLI flag or another scenario parameter that "
                f"normalizes to the same kebab-case form. Rename the parameter."
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
        seen_flags.add(flag)


def _argparse_type_for(*, param: Parameter) -> Optional[Any]:
    """
    Map a ``Parameter`` to an argparse ``type=`` callable, or ``None`` for str/raw.

    For list params, ``None`` is correct because ``nargs='+'`` collects strings;
    list element validation happens via ``coerce_list`` at scenario-set time.

    Args:
        param (Parameter): The scenario-declared parameter.

    Returns:
        Optional[Any]: Coercion callable, or ``None`` if no coercion is needed.
    """
    from pyrit.common.parameter import coerce_value

    param_type = param.param_type
    if param_type is None or param_type is str or _is_list_param(param_type):
        return None
    return lambda raw: coerce_value(param=param, raw_value=raw)


def _is_list_param(param_type: Any) -> bool:
    """Return True when ``param_type`` is a parameterized list generic (e.g. ``list[str]``)."""
    return get_origin(param_type) is list


def _extract_scenario_args(*, parsed: Namespace) -> dict[str, Any]:
    """
    Pull scenario-declared parameter values out of a parsed Namespace.

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
    try:
        parsed_args = parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    print("Starting PyRIT...")
    sys.stdout.flush()

    # Defer the heavy import until after arg parsing so --help is instant.
    from pyrit.cli import frontend_core

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

    # Run scenario (verify scenario name from CLI positional or config block)
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

        # Resolve the effective scenario name: CLI positional wins, config falls through.
        config_scenario = context._scenario_config
        effective_scenario_name = parsed_args.scenario_name or (config_scenario.name if config_scenario else None)
        if not effective_scenario_name:
            print("Error: No scenario specified. Provide one positionally or via the config file's `scenario:` block.")
            return 1

        # Parse memory labels if provided
        memory_labels = None
        if parsed_args.memory_labels:
            memory_labels = frontend_core.parse_memory_labels(json_string=parsed_args.memory_labels)

        # Merge scenario args (CLI wins per-key over config args).
        merged_scenario_args = merge_config_scenario_args(
            config_scenario=config_scenario,
            effective_scenario_name=effective_scenario_name,
            cli_args=_extract_scenario_args(parsed=parsed_args),
        )

        # Run scenario
        asyncio.run(
            frontend_core.run_scenario_async(
                scenario_name=effective_scenario_name,
                context=context,
                target_name=parsed_args.target,
                scenario_strategies=parsed_args.scenario_strategies,
                max_concurrency=parsed_args.max_concurrency,
                max_retries=parsed_args.max_retries,
                memory_labels=memory_labels,
                dataset_names=parsed_args.dataset_names,
                max_dataset_size=parsed_args.max_dataset_size,
                scenario_args=merged_scenario_args,
            )
        )
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
