# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT CLI - Command-line interface for running security scenarios.

This module provides the main entry point for the pyrit_scan command.
It is a thin REST client that talks to the PyRIT backend server over HTTP.
No heavy pyrit imports — all operations go through the REST API.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args, get_origin

from pyrit.cli._cli_args import (
    ARG_HELP,
    _parse_initializer_arg,
    build_parameters_from_api,
    non_negative_int,
    positive_int,
    validate_log_level_argparse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models.catalog import (
        RegisteredScenario,
        RunScenarioRequest,
        ScenarioParameterSummary,
        ScenarioRunSummary,
    )
    from pyrit.models.parameter import Parameter


def _print_cli_exception(*, exc: BaseException) -> None:
    """
    Print a user-facing error line for an exception that bubbled out of the CLI.

    Surfaces the exception class (so callers can tell ``ReadTimeout`` apart from
    ``HTTPStatusError``) and dumps the traceback when log-level is ``DEBUG``.
    Adds a specific hint for ``httpx.ReadTimeout`` since that case usually means
    the server is taking longer than ``--request-timeout`` to respond and the
    default bare ``str(exc)`` is empty.

    Args:
        exc (BaseException): The exception caught by the CLI.
    """
    import traceback

    try:
        import httpx

        is_read_timeout = isinstance(exc, httpx.ReadTimeout)
    except Exception:
        is_read_timeout = False

    cls_name = type(exc).__name__
    detail = str(exc) or repr(exc)

    if is_read_timeout:
        print(
            "\nError (ReadTimeout): server did not respond in time. "
            "Pass '--request-timeout <seconds>' to wait longer, or check the "
            "server logs for a blocked event loop."
        )
    else:
        print(f"\nError ({cls_name}): {detail}")

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        traceback.print_exception(type(exc), exc, exc.__traceback__)


_DESCRIPTION = """PyRIT Scanner - Run AI security scenarios from the command line.

Requires a running PyRIT backend server. Use --start-server to launch one,
or connect to an existing server with --server-url.

Examples:
  # Start the backend server
  pyrit_scan --start-server

  # List scenarios, initializers, or targets
  pyrit_scan --list-scenarios
  pyrit_scan --list-initializers
  pyrit_scan --list-targets

  # Run single-turn cyber attacks against a target
  pyrit_scan airt.cyber --target openai_chat --strategies single_turn

  # Run rapid response with specific datasets and concurrency
  pyrit_scan airt.rapid_response --target openai_chat
    --strategies role_play --dataset-names airt_hate
    --max-dataset-size 5 --max-concurrency 4

  # Run multi-turn red team agent with labels for tracking
  pyrit_scan airt.red_team_agent --target openai_chat
    --strategies crescendo
    --memory-labels '{"experiment":"baseline"}'

  # Register a custom initializer from a Python script
  pyrit_scan --add-initializer ./my_custom_init.py

  # Connect to a remote server
  pyrit_scan --server-url http://remote:8000 --list-scenarios

  # Stop the server
  pyrit_scan --stop-server
"""


def _build_base_parser(*, add_help: bool = True) -> ArgumentParser:
    """
    Build the ``pyrit_scan`` argparse parser with the built-in (non-scenario) flags.

    Args:
        add_help (bool): Whether to register the ``-h``/``--help`` action.

    Returns:
        ArgumentParser: Parser with all built-in flags registered.
    """
    parser = ArgumentParser(
        prog="pyrit_scan",
        description=_DESCRIPTION,
        formatter_class=RawDescriptionHelpFormatter,
        add_help=add_help,
    )

    # -- Server management --
    server_group = parser.add_argument_group("server")
    server_group.add_argument(
        "--server-url",
        type=str,
        help="URL of the PyRIT backend server (default: http://localhost:8000)",
    )
    server_group.add_argument(
        "--start-server",
        action="store_true",
        help="Start a local backend server if one is not already running",
    )
    server_group.add_argument(
        "--stop-server",
        action="store_true",
        help="Stop the backend server and exit",
    )
    server_group.add_argument(
        "--config-file",
        type=Path,
        help=ARG_HELP["config_file"],
    )
    server_group.add_argument(
        "--log-level",
        type=validate_log_level_argparse,
        default=logging.WARNING,
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)",
    )
    server_group.add_argument(
        "--request-timeout",
        type=float,
        default=None,
        help=(
            "HTTP read timeout in seconds for non-polling server requests "
            "(catalog/results/cancel/etc). Defaults to 60. Polling a live "
            "scenario run always waits indefinitely regardless of this value."
        ),
    )

    # -- Discovery --
    discovery_group = parser.add_argument_group("discovery")
    discovery_group.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios and exit",
    )
    discovery_group.add_argument(
        "--list-initializers",
        action="store_true",
        help="List all available initializers and exit",
    )
    discovery_group.add_argument(
        "--list-targets",
        action="store_true",
        help="List all available targets and exit",
    )
    discovery_group.add_argument(
        "--add-initializer",
        type=str,
        nargs="+",
        metavar="FILE",
        help="Register initializer(s) from Python script file(s) and exit",
    )

    # -- Scenario run --
    run_group = parser.add_argument_group("scenario run")
    run_group.add_argument(
        "scenario_name",
        type=str,
        nargs="?",
        help="Name of the scenario to run",
    )
    run_group.add_argument(
        "--target",
        type=str,
        help=ARG_HELP["target"],
    )
    run_group.add_argument(
        "--initializers",
        type=_parse_initializer_arg,
        nargs="+",
        help=ARG_HELP["initializers"],
    )
    run_group.add_argument(
        "--strategies",
        "-s",
        type=str,
        nargs="+",
        dest="scenario_strategies",
        help=ARG_HELP["scenario_strategies"],
    )
    run_group.add_argument(
        "--max-concurrency",
        type=positive_int,
        help=ARG_HELP["max_concurrency"],
    )
    run_group.add_argument(
        "--max-retries",
        type=non_negative_int,
        help=ARG_HELP["max_retries"],
    )
    run_group.add_argument(
        "--memory-labels",
        type=str,
        help=ARG_HELP["memory_labels"],
    )
    run_group.add_argument(
        "--dataset-names",
        type=str,
        nargs="+",
        help=ARG_HELP["dataset_names"],
    )
    run_group.add_argument(
        "--max-dataset-size",
        type=positive_int,
        help=ARG_HELP["max_dataset_size"],
    )

    return parser


# Namespacing prefix for scenario-declared params on the parsed Namespace.
_SCENARIO_DEST_PREFIX = "scenario__"


def _scenario_value_coercer(*, name: str, annotation: Any) -> Callable[[Any], Any] | None:
    """
    Build an argparse ``type=`` callable that coerces a single CLI token through
    ``Parameter.coerce_value`` — the same coercion the shell and backend use.

    Returns ``None`` when no coercion is needed (a plain ``str`` or an untyped
    passthrough). Coercion/validation failures (including ``Literal`` choice
    membership) are re-raised as ``argparse.ArgumentTypeError`` so argparse renders
    them as a clean CLI error.

    Args:
        name: Scenario parameter name (used for the flag in error messages).
        annotation: Scalar element type to coerce to (e.g. ``int``, ``bool``, or
            ``Literal[...]`` for choices), or ``None`` / ``str`` for passthrough.

    Returns:
        Callable[[Any], Any] | None: The coercer, or ``None`` for passthrough.
    """
    if annotation is None or annotation is str:
        return None

    from pyrit.models.parameter import Parameter

    element_param = Parameter(name=name, description="", param_type=annotation)

    def _coerce(raw: Any) -> Any:
        try:
            return element_param.coerce_value(raw)
        except (ValueError, TypeError) as exc:
            raise argparse.ArgumentTypeError(f"--{name.replace('_', '-')}: invalid value {raw!r} ({exc})") from exc

    return _coerce


def _scenario_param_kwargs(*, parameter: Parameter) -> dict[str, Any]:
    """
    Build argparse ``add_argument`` kwargs for a scenario-declared ``Parameter``.

    List params get ``nargs='+'`` and coerce per element; scalar params coerce the
    single token. All coercion — including ``Literal`` choice membership — routes
    through ``Parameter.coerce_value`` so scan, the shell, and the backend agree on
    accepted values.

    Args:
        parameter: Scenario parameter built from the catalog payload via
            ``build_parameters_from_api``.

    Returns:
        dict[str, Any]: kwargs ready to pass to ``ArgumentParser.add_argument``.
    """
    kwargs: dict[str, Any] = {
        "dest": f"{_SCENARIO_DEST_PREFIX}{parameter.name}",
        "default": argparse.SUPPRESS,
        "help": parameter.description,
    }
    param_type = parameter.param_type
    element_type: Any
    if get_origin(param_type) is list:
        type_args = get_args(param_type)
        element_type = type_args[0] if type_args else str
        kwargs["nargs"] = "+"
    else:
        element_type = param_type

    coercer = _scenario_value_coercer(name=parameter.name, annotation=element_type)
    if coercer is not None:
        kwargs["type"] = coercer
    return kwargs


def _add_scenario_params_from_api(*, parser: ArgumentParser, params: list[ScenarioParameterSummary]) -> None:
    """
    Add scenario-declared parameters as CLI flags.

    Catalog payloads are converted to ``Parameter`` objects via
    ``build_parameters_from_api`` (shared with the shell) so type coercion and
    choice handling stay consistent across entry points.

    Args:
        parser: Parser to extend.
        params: Scenario-declared parameters from ``GET /api/scenarios/catalog/{name}``.
    """
    seen_flags: set[str] = set(parser._option_string_actions.keys())
    for parameter in build_parameters_from_api(api_params=params) or []:
        flag = f"--{parameter.name.replace('_', '-')}"
        if flag in seen_flags:
            continue
        parser.add_argument(flag, **_scenario_param_kwargs(parameter=parameter))
        seen_flags.add(flag)


def _extract_scenario_args(*, parsed: Namespace) -> dict[str, Any]:
    """
    Pull scenario-declared parameter values out of a parsed Namespace.

    Args:
        parsed: Result of ``ArgumentParser.parse_args``.

    Returns:
        dict[str, Any]: Map of original parameter name to value.
    """
    return {
        key.removeprefix(_SCENARIO_DEST_PREFIX): value
        for key, value in vars(parsed).items()
        if key.startswith(_SCENARIO_DEST_PREFIX)
    }


def parse_args(args: list[str] | None = None) -> Namespace:
    """
    Parse command-line arguments (pass 1 — tolerant of scenario-declared flags).

    Pass 1 uses ``parse_known_args`` so scenario-specific flags (e.g.
    ``--max-turns 7``) don't cause an error before we've had a chance to
    fetch the scenario's declared parameters from the server. The unknown
    leftovers are stashed on the returned Namespace as ``_unknown_args``
    so ``_reparse_with_scenario_params`` can detect truly unknown flags
    when no scenario was specified.

    Args:
        args: Argument list (``sys.argv[1:]`` when None).

    Returns:
        Namespace: Parsed command-line arguments.
    """
    parser = _build_base_parser(add_help=True)
    parsed, unknown = parser.parse_known_args(args)
    parsed._unknown_args = unknown
    parsed._raw_args = list(args) if args is not None else list(sys.argv[1:])
    return parsed


async def _resolve_server_url_async(*, parsed_args: Namespace) -> str | None:
    """
    Determine the server URL and ensure it is reachable.

    Resolution order:
    1. ``--server-url`` CLI flag
    2. ``server.url`` from config file
    3. Default ``http://localhost:8000``

    If ``--start-server`` is set and the server is not healthy, launches
    a local ``pyrit_backend`` subprocess.

    Returns:
        str | None: The server base URL, or ``None`` if unreachable.
    """
    from pyrit.cli._config_reader import DEFAULT_SERVER_URL, read_server_url
    from pyrit.cli._server_launcher import ServerLauncher

    base_url = parsed_args.server_url
    if base_url is None:
        base_url = read_server_url(config_file=parsed_args.config_file) or DEFAULT_SERVER_URL

    # Probe existing server
    if await ServerLauncher.probe_health_async(base_url=base_url):
        return base_url

    # Auto-start if requested
    if parsed_args.start_server:
        # The launcher can only bind localhost:8000. If the user explicitly
        # configured a different URL we can't honor it — refuse rather than
        # silently start a server the user can't reach.
        if base_url != DEFAULT_SERVER_URL:
            print(
                f"Error: cannot --start-server because the configured server URL ({base_url}) "
                f"does not match the launcher default ({DEFAULT_SERVER_URL}). "
                "Either remove --server-url / the server.url config entry, "
                "or start the backend manually with `pyrit_backend --host ... --port ...`.",
                file=sys.stderr,
            )
            return None
        launcher = ServerLauncher()
        try:
            return await launcher.start_async(config_file=parsed_args.config_file)
        except RuntimeError as exc:
            print(f"Error: {exc}")
            return None

    return None


def _is_command_specified(*, parsed_args: Namespace) -> bool:
    """
    Return True if the user supplied any actionable command flag (besides
    ``--start-server`` / ``--stop-server``).

    Returns:
        bool: ``True`` if at least one actionable command flag was provided.
    """
    return bool(
        parsed_args.list_scenarios
        or parsed_args.list_initializers
        or parsed_args.list_targets
        or parsed_args.add_initializer
        or parsed_args.scenario_name
    )


def _resolve_configured_server_url(*, parsed_args: Namespace) -> str:
    """
    Resolve the effective server URL (without probing).

    Returns:
        str: The configured server URL, falling back to the built-in default.
    """
    from pyrit.cli._config_reader import DEFAULT_SERVER_URL, read_server_url

    return parsed_args.server_url or read_server_url(config_file=parsed_args.config_file) or DEFAULT_SERVER_URL


async def _handle_stop_server_async(*, parsed_args: Namespace) -> int:
    """
    Handle ``--stop-server``: probe, then terminate the listening process.

    Returns:
        int: Exit code (always ``0``).
    """
    from urllib.parse import urlparse

    from pyrit.cli._server_launcher import ServerLauncher, stop_server_on_port

    base_url = _resolve_configured_server_url(parsed_args=parsed_args)
    if not await ServerLauncher.probe_health_async(base_url=base_url):
        print(f"No server running at {base_url}.")
        return 0

    port = urlparse(base_url).port or 8000
    if stop_server_on_port(port=port):
        print(f"Server on port {port} stopped.")
    else:
        print(f"Server at {base_url} is running but could not identify the process.")
        print(f"Find and kill it manually: look for a process listening on port {port}.")
    return 0


async def _handle_list_commands_async(*, client: Any, parsed_args: Namespace) -> int | None:
    """
    Dispatch ``--list-*`` flags.

    Returns:
        int | None: Exit code if a flag was handled, else ``None``.
    """
    from pyrit.cli import _output

    if parsed_args.list_scenarios:
        scenarios = await client.list_scenarios_async()
        _output.print_scenario_list(items=scenarios)
        return 0
    if parsed_args.list_initializers:
        initializers = await client.list_initializers_async()
        _output.print_initializer_list(items=initializers)
        return 0
    if parsed_args.list_targets:
        targets = await client.list_targets_async()
        _output.print_target_list(items=targets)
        return 0
    return None


async def _handle_add_initializer_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Handle ``--add-initializer``: upload one or more scripts to the server.

    Returns:
        int: Exit code (``0`` on success, ``1`` on failure).
    """
    from pyrit.cli.api_client import ServerNotAvailableError

    for script_path_str in parsed_args.add_initializer:
        script_path = Path(script_path_str).resolve()
        if not script_path.exists():
            print(f"Error: File not found: {script_path}")
            return 1
        try:
            script_content = script_path.read_text()
            await client.register_initializer_async(
                name=script_path.stem,
                script_content=script_content,
            )
            print(f"Registered initializer '{script_path.stem}' from {script_path}")
        except ServerNotAvailableError as exc:
            print(f"Error: {exc}")
            return 1
    return 0


def _reparse_with_scenario_params(
    *, parsed_args: Namespace, supported_params: list[ScenarioParameterSummary]
) -> Namespace | None:
    """
    Re-parse the original args with scenario-declared flags added to the base parser.

    The original argument list is read from ``parsed_args._raw_args`` (populated
    by ``parse_args``). If no scenario-declared parameters are supplied but
    pass 1 left unknown args behind, surface the error now via strict re-parse.

    Returns:
        Namespace | None: The re-parsed Namespace, or ``None`` on argparse ``SystemExit``.
    """
    raw_args: list[str] = getattr(parsed_args, "_raw_args", sys.argv[1:] if len(sys.argv) > 1 else [])

    if not supported_params:
        unknown = getattr(parsed_args, "_unknown_args", None)
        if not unknown:
            return parsed_args
        # Re-parse strictly so argparse prints the standard "unrecognized arguments" error
        strict_parser = _build_base_parser(add_help=True)
        try:
            return strict_parser.parse_args(raw_args)
        except SystemExit:
            return None

    pass2_parser = _build_base_parser(add_help=True)
    _add_scenario_params_from_api(parser=pass2_parser, params=supported_params)
    try:
        return pass2_parser.parse_args(raw_args)
    except SystemExit:
        return None


def _build_run_request(*, parsed_args: Namespace, scenario_name: str) -> RunScenarioRequest:
    """
    Build the ``RunScenarioRequest`` typed object from parsed CLI args.

    Returns:
        RunScenarioRequest: The typed request payload to send to ``POST /api/scenarios/runs``.
    """
    from pyrit.cli._cli_args import parse_memory_labels
    from pyrit.models.catalog import RunScenarioRequest

    kwargs: dict[str, Any] = {
        "scenario_name": scenario_name,
        "target_name": parsed_args.target or "",
    }

    if parsed_args.initializers:
        init_names: list[str] = []
        init_args: dict[str, dict[str, Any]] = {}
        for entry in parsed_args.initializers:
            if isinstance(entry, str):
                init_names.append(entry)
            elif isinstance(entry, dict):
                name = entry["name"]
                init_names.append(name)
                if entry.get("args"):
                    init_args[name] = entry["args"]
        kwargs["initializers"] = init_names
        if init_args:
            kwargs["initializer_args"] = init_args

    if parsed_args.scenario_strategies:
        kwargs["strategies"] = parsed_args.scenario_strategies
    if parsed_args.max_concurrency is not None:
        kwargs["max_concurrency"] = parsed_args.max_concurrency
    if parsed_args.max_retries is not None:
        kwargs["max_retries"] = parsed_args.max_retries
    if parsed_args.dataset_names:
        kwargs["dataset_names"] = parsed_args.dataset_names
    if parsed_args.max_dataset_size is not None:
        kwargs["max_dataset_size"] = parsed_args.max_dataset_size
    if parsed_args.memory_labels:
        kwargs["labels"] = parse_memory_labels(json_string=parsed_args.memory_labels)

    scenario_params = _extract_scenario_args(parsed=parsed_args)
    if scenario_params:
        kwargs["scenario_params"] = scenario_params

    return RunScenarioRequest(**kwargs)


async def _poll_until_terminal_async(
    *,
    client: Any,
    scenario_result_id: str,
    total_strategies: int,
) -> ScenarioRunSummary:
    """
    Poll the server until the run reaches a terminal status.

    Returns:
        ScenarioRunSummary: The final run summary.
    """
    from pyrit.cli import _output
    from pyrit.models import ScenarioRunState

    terminal_states = {ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED}

    while True:
        run = await client.get_scenario_run_async(scenario_result_id=scenario_result_id)
        _output.print_scenario_run_progress(run=run, total_strategies=total_strategies)
        if run.status in terminal_states:
            return run
        await asyncio.sleep(0.5)


async def _run_scenario_async(
    *,
    client: Any,
    parsed_args: Namespace,
    scenario_meta: RegisteredScenario,
) -> int:
    """
    Start a scenario run, poll for completion, and print results.

    Returns:
        int: Exit code (``0`` if the run completed successfully, ``1`` otherwise).
    """
    from pyrit.cli import _output
    from pyrit.models import ScenarioRunState

    scenario_name = parsed_args.scenario_name
    request = _build_run_request(parsed_args=parsed_args, scenario_name=scenario_name)

    total_strategies = len(request.strategies or scenario_meta.all_strategies or [])
    print(f"\nRunning scenario: {scenario_name}")
    sys.stdout.flush()

    try:
        run = await client.start_scenario_run_async(request=request)
    except Exception as exc:
        print(f"Error starting scenario: {exc}")
        return 1

    scenario_result_id = run.scenario_result_id

    try:
        run = await _poll_until_terminal_async(
            client=client,
            scenario_result_id=scenario_result_id,
            total_strategies=total_strategies,
        )
    except KeyboardInterrupt:
        print("\n\nCancelling scenario run...")
        try:
            await client.cancel_scenario_run_async(scenario_result_id=scenario_result_id)
            print("Scenario run cancelled.")
        except Exception:
            print("Warning: could not cancel scenario run on server.")
        return 1

    if run.status == ScenarioRunState.COMPLETED:
        try:
            detail = await client.get_scenario_run_results_async(scenario_result_id=scenario_result_id)
            await _output.print_scenario_result_async(result=detail)
        except Exception as exc:
            print(
                "\nERROR: The scenario completed, but its detailed results could not be "
                "retrieved or parsed from the server."
            )
            _print_cli_exception(exc=exc)
            _output.print_scenario_run_summary(run=run)
            return 1
        return 0

    _output.print_scenario_run_summary(run=run)
    return 1


async def _dispatch_with_client_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Dispatch list/add-initializer/scenario-run commands once a client is open.

    Returns:
        int: Exit code from the dispatched command.
    """
    list_result = await _handle_list_commands_async(client=client, parsed_args=parsed_args)
    if list_result is not None:
        return list_result

    if parsed_args.add_initializer:
        return await _handle_add_initializer_async(client=client, parsed_args=parsed_args)

    scenario_name = parsed_args.scenario_name
    if not scenario_name:
        print("Error: No scenario specified. Provide one positionally or use --list-scenarios.")
        return 1

    scenario_meta = await client.get_scenario_async(scenario_name=scenario_name)
    if scenario_meta is None:
        print(f"Error: Scenario '{scenario_name}' not found on server.")
        scenarios = await client.list_scenarios_async()
        names = [s.scenario_name for s in scenarios]
        if names:
            print(f"Available scenarios: {', '.join(names)}")
        return 1

    reparsed = _reparse_with_scenario_params(
        parsed_args=parsed_args,
        supported_params=scenario_meta.supported_parameters,
    )
    if reparsed is None:
        return 1
    parsed_args = reparsed

    return await _run_scenario_async(client=client, parsed_args=parsed_args, scenario_meta=scenario_meta)


async def _run_async(*, parsed_args: Namespace) -> int:
    """
    Core async logic for pyrit_scan.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    from pyrit.cli import _output
    from pyrit.cli.api_client import PyRITApiClient, ServerNotAvailableError

    if parsed_args.stop_server:
        return await _handle_stop_server_async(parsed_args=parsed_args)

    if not (parsed_args.start_server or _is_command_specified(parsed_args=parsed_args)):
        _build_base_parser().print_help()
        return 0

    base_url_result = await _resolve_server_url_async(parsed_args=parsed_args)
    if base_url_result is None:
        attempted = _resolve_configured_server_url(parsed_args=parsed_args)
        _output.print_error_with_hint(
            message=f"Server not available at {attempted}",
            hint="Use '--start-server' to launch a local backend, or pass '--server-url <url>'.",
        )
        return 1

    # --start-server with no other command: just confirm and exit
    if not _is_command_specified(parsed_args=parsed_args):
        print(f"Server is running at {base_url_result}")
        return 0

    try:
        async with PyRITApiClient(
            base_url=base_url_result,
            request_timeout=getattr(parsed_args, "request_timeout", None),
        ) as client:
            return await _dispatch_with_client_async(client=client, parsed_args=parsed_args)
    except ServerNotAvailableError as exc:
        _output.print_error_with_hint(
            message=str(exc),
            hint="Use '--start-server' to launch a local backend, or pass '--server-url <url>'.",
        )
        return 1
    except Exception as exc:
        _print_cli_exception(exc=exc)
        return 1


def main(args: list[str] | None = None) -> int:
    """
    Start the PyRIT scanner CLI.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    try:
        parsed_args = parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # If there are leftover unknown flags AND no scenario was specified,
    # there's no chance for pass 2 to recognize them - fail loudly now.
    unknown = getattr(parsed_args, "_unknown_args", [])
    if unknown and not parsed_args.scenario_name:
        strict_parser = _build_base_parser(add_help=True)
        try:
            strict_parser.parse_args(parsed_args._raw_args)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1

    logging.basicConfig(level=parsed_args.log_level)

    # Surface a one-line deprecation when the layered config contains blocks
    # the thin CLI no longer reads (e.g. `scenario:`). The server still honors them.
    from pyrit.cli._config_reader import ConfigError, warn_on_client_ignored_blocks

    try:
        warn_on_client_ignored_blocks(config_file=parsed_args.config_file)
        return asyncio.run(_run_async(parsed_args=parsed_args))
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
