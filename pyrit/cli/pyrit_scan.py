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
import math
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args, get_origin

import aiofiles

from pyrit.cli._auth import AUTH_MODES, AuthMode
from pyrit.cli._cli_args import (
    ARG_HELP,
    _parse_initializer_arg,
    add_results_arguments,
    build_parameters_from_api,
    collapse_dataset_filters,
    non_negative_int,
    parse_dataset_filter,
    positive_int,
    validate_log_level_argparse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models.catalog import (
        RegisteredScenario,
        RunScenarioRequest,
        ScenarioRunSummary,
    )
    from pyrit.models.parameter import Parameter


def _is_read_timeout(exc: BaseException) -> bool:
    """
    Report whether an exception is an ``httpx.ReadTimeout``.

    Args:
        exc (BaseException): The exception to classify.

    Returns:
        bool: True when httpx is importable and the exception is a read timeout.
    """
    try:
        import httpx

        return isinstance(exc, httpx.ReadTimeout)
    except Exception:
        return False


def _print_debug_traceback(exc: BaseException) -> None:
    """
    Print the traceback for an exception when the log level is ``DEBUG``.

    Args:
        exc (BaseException): The exception caught by the CLI.
    """
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)


def _print_cli_exception(*, exc: BaseException) -> None:
    """
    Print a user-facing error line for an exception that bubbled out of the CLI.

    Surfaces the exception class (so callers can tell ``ReadTimeout`` apart from
    ``HTTPStatusError``) and dumps the traceback when log-level is ``DEBUG``.
    Adds a specific hint for ``httpx.ReadTimeout`` since that case usually means
    the server is taking longer than ``--request-timeout`` to respond and the
    default bare ``str(exc)`` is empty.

    Only ``pyrit_scan`` verbs reach this with a timeout; ``pyrit_shell`` has no
    ``--request-timeout`` and handles that case before it gets here.

    Args:
        exc (BaseException): The exception caught by the CLI.
    """
    cls_name = type(exc).__name__
    detail = str(exc) or repr(exc)

    if _is_read_timeout(exc):
        print(
            "\nError (ReadTimeout): server did not respond in time. Pass '--request-timeout "
            "<seconds>' to wait longer, or check the server logs for a blocked event loop."
        )
    else:
        print(f"\nError ({cls_name}): {detail}")

    _print_debug_traceback(exc)


_DESCRIPTION = """PyRIT Scanner - Run AI security scenarios from the command line.

Requires a running PyRIT backend server. Use 'start-server' to launch one,
or connect to an existing server with --server-url.

Global options (--server-url, --config-file, --log-level) are listed below and
work before or after the verb. Backend commands (run, list-*, add-initializer,
scenario-results, scenario-history) also accept --start-server, --startup-timeout,
and --request-timeout; run 'pyrit_scan <command> --help' to see them.

Examples:
  # Start the backend server
  pyrit_scan start-server

  # List scenarios, initializers, targets, or converters
  pyrit_scan list-scenarios
  pyrit_scan list-initializers
  pyrit_scan list-targets
  pyrit_scan list-converters

  # Run single-turn cyber attacks against a target
  pyrit_scan run airt.cyber --target openai_chat --techniques single_turn

  # Run rapid response with specific datasets and concurrency
  pyrit_scan run airt.rapid_response --target openai_chat
    --techniques role_play_movie_script --dataset-names airt_hate
    --max-dataset-size 5 --max-concurrency 4

  # Attach registered converters to a technique (repeatable, applied in order)
  pyrit_scan run airt.rapid_response --target openai_chat
    --techniques role_play_movie_script:converter.translation_spanish:converter.leetspeak

  # List recent runs, then inspect one (overview by default; --view attacks for per-attack rows,
  # --view conversations/full for message transcripts)
  pyrit_scan scenario-history 20
  pyrit_scan scenario-results 605d715b-7c07-4bde-a8f9-22fea0b50c4f --view attacks
  pyrit_scan scenario-results 605d715b-7c07-4bde-a8f9-22fea0b50c4f --view conversations --limit 3

  # Register a custom initializer from a Python script
  pyrit_scan add-initializer ./my_custom_init.py

  # Connect to a remote server
  pyrit_scan list-scenarios --server-url http://remote:8000

  # Stop the server
  pyrit_scan stop-server
"""


def _positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a number greater than 0, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(f"expected a finite number greater than 0, got {value!r}")
    return parsed


_SERVER_URL_HELP = "URL of the PyRIT backend server (default: http://localhost:8000)"
_LOG_LEVEL_HELP = "Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)"
# Scan-specific override of the shared CONFIG_FILE_HELP: for the thin client the file's
# database/initializers/init-scripts/env sections only take effect when a backend is
# launched (start-server or --start-server) and are NOT re-applied to a running server.
_CONFIG_FILE_HELP = (
    "Path to a YAML config file. For commands that talk to a running server this only "
    "selects which backend to connect to (server.url). Its database, initializers, "
    "initialization-scripts, and env sections apply only when a backend is launched "
    "(start-server or --start-server); they are not re-run against an already-running server."
)
_START_SERVER_HELP = "Start a local backend server first if one is not already running"
_STARTUP_TIMEOUT_HELP = "Seconds to wait for a local backend to start (default: server.startup_timeout or 120)"
_REQUEST_TIMEOUT_HELP = (
    "HTTP read timeout in seconds for non-polling server requests "
    "(catalog/results/cancel/etc). Defaults to 60. Polling a live "
    "scenario run always waits indefinitely regardless of this value."
)
_AUTH_MODE_HELP = (
    "Backend authentication mode (default: server.auth_mode or auto). "
    "Auto uses exact-scope device-code authentication in an interactive terminal."
)


def _add_common_options(*, parser: ArgumentParser, suppress_defaults: bool) -> None:
    """
    Add the options that *every* command honors: server URL, config file, log level.

    These live on the root parser (real defaults) *and* on each sub-parser
    (``SUPPRESS`` defaults, so a value parsed by the root — e.g.
    ``pyrit_scan --server-url X run`` — is not clobbered by the sub-parser's own
    default on the second parse pass).

    Args:
        parser (ArgumentParser): Parser to extend.
        suppress_defaults (bool): Use ``argparse.SUPPRESS`` defaults (sub-parser copies)
            instead of real defaults (the root parser, which owns the canonical values).
    """
    default = argparse.SUPPRESS if suppress_defaults else None
    log_default = argparse.SUPPRESS if suppress_defaults else logging.WARNING
    group = parser.add_argument_group("global options")
    group.add_argument("--server-url", type=str, default=default, help=_SERVER_URL_HELP)
    group.add_argument("--config-file", type=Path, default=default, help=_CONFIG_FILE_HELP)
    group.add_argument("--auth-mode", choices=AUTH_MODES, default=default, help=_AUTH_MODE_HELP)
    group.add_argument("--log-level", type=validate_log_level_argparse, default=log_default, help=_LOG_LEVEL_HELP)


def _build_common_parent() -> ArgumentParser:
    """
    Parent parser with the common options for a sub-parser (``SUPPRESS`` defaults).

    Returns:
        ArgumentParser: A help-less parent parser with the common options.
    """
    parser = ArgumentParser(add_help=False)
    _add_common_options(parser=parser, suppress_defaults=True)
    return parser


def _build_client_parent() -> ArgumentParser:
    """
    Parent parser for commands that reach the backend through the API client.

    Adds ``--request-timeout`` plus the auto-start options (``--start-server`` /
    ``--startup-timeout``). Attached to ``run``, the ``list-*`` verbs,
    ``add-initializer``, ``scenario-results``, and ``scenario-history``. Verbs that do
    not open a client (``start-server``, ``stop-server``) deliberately omit it so
    unsupported combinations like ``start-server --request-timeout`` are rejected.

    Returns:
        ArgumentParser: A help-less parent parser with the client/auto-start options.
    """
    parser = ArgumentParser(add_help=False)
    group = parser.add_argument_group("server options")
    group.add_argument("--request-timeout", type=float, default=None, help=_REQUEST_TIMEOUT_HELP)
    group.add_argument("--start-server", action="store_true", help=_START_SERVER_HELP)
    group.add_argument(
        "--startup-timeout", type=_positive_finite_float, default=None, metavar="SECONDS", help=_STARTUP_TIMEOUT_HELP
    )
    return parser


def _build_start_server_parent() -> ArgumentParser:
    """
    Parent parser for the ``start-server`` verb: only ``--startup-timeout`` applies.

    Returns:
        ArgumentParser: A help-less parent parser with the startup timeout option.
    """
    parser = ArgumentParser(add_help=False)
    group = parser.add_argument_group("server startup options")
    group.add_argument(
        "--startup-timeout", type=_positive_finite_float, default=None, metavar="SECONDS", help=_STARTUP_TIMEOUT_HELP
    )
    return parser


def _build_global_parser() -> ArgumentParser:
    """
    Union of every option group.

    Used *only* by the legacy shim to strip options and locate a verb; it is never
    attached to a command. Keeping it a union (rather than the per-command groups)
    lets the shim reorder any option placed before a verb, regardless of which
    command ultimately owns it.

    Returns:
        ArgumentParser: A help-less parser recognizing all common and server options.
    """
    return ArgumentParser(add_help=False, parents=[_build_common_parent(), _build_client_parent()])


def _add_run_arguments(*, parser: ArgumentParser, scenario_params: list[Parameter] | None = None) -> None:
    """
    Add the ``run`` verb's arguments (scenario positional + run flags) to *parser*.

    Args:
        parser (ArgumentParser): The ``run`` sub-parser to populate.
        scenario_params (list[Parameter] | None): Scenario-declared parameters to
            register as flags. Provided on the second parse pass, once the
            scenario metadata has been fetched. Defaults to None.
    """
    parser.add_argument(
        "scenario_name",
        type=str,
        help="Name of the scenario to run",
    )
    parser.add_argument("--target", type=str, help=ARG_HELP["target"])
    parser.add_argument(
        "--initializers",
        type=_parse_initializer_arg,
        nargs="+",
        help=ARG_HELP["initializers"],
    )
    parser.add_argument(
        "--techniques",
        "-t",
        type=str,
        nargs="+",
        dest="scenario_techniques",
        help=ARG_HELP["scenario_techniques"],
    )
    parser.add_argument("--max-concurrency", type=positive_int, help=ARG_HELP["max_concurrency"])
    parser.add_argument("--max-retries", type=non_negative_int, help=ARG_HELP["max_retries"])
    parser.add_argument("--memory-labels", type=str, help=ARG_HELP["memory_labels"])
    parser.add_argument("--dataset-names", type=str, nargs="+", help=ARG_HELP["dataset_names"])
    parser.add_argument("--max-dataset-size", type=positive_int, help=ARG_HELP["max_dataset_size"])
    parser.add_argument(
        "--dataset-filters",
        type=parse_dataset_filter,
        nargs="+",
        metavar="KEY=VALUE",
        help=ARG_HELP["dataset_filters"],
    )
    if scenario_params:
        _add_scenario_params_from_api(parser=parser, params=scenario_params)


#: Discovery verbs that only list a catalog and exit, mapped to their help text.
_LIST_VERBS: dict[str, str] = {
    "list-scenarios": "List all available scenarios",
    "list-initializers": "List all available initializers",
    "list-targets": "List all available targets",
    "list-converters": "List all registered converter instances",
    "list-datasets": "List all available datasets",
}


def _build_parser(*, scenario_params: list[Parameter] | None = None, add_help: bool = True) -> ArgumentParser:
    """
    Build the top-level ``pyrit_scan`` parser with one sub-parser per verb.

    Args:
        scenario_params (list[Parameter] | None): Scenario-declared parameters to
            register on the ``run`` sub-parser (second parse pass). Defaults to None.
        add_help (bool): Whether to register the ``-h``/``--help`` action.

    Returns:
        ArgumentParser: The configured parser.
    """
    common_parent = _build_common_parent()
    client_parent = _build_client_parent()
    start_server_parent = _build_start_server_parent()
    client_parents = [common_parent, client_parent]

    parser = ArgumentParser(
        prog="pyrit_scan",
        description=_DESCRIPTION,
        formatter_class=RawDescriptionHelpFormatter,
        add_help=add_help,
    )
    # The root parser owns the real global options so top-level help and pre-verb
    # handling (e.g. ``pyrit_scan --server-url X --help``) work normally.
    _add_common_options(parser=parser, suppress_defaults=False)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", title="commands")

    run_parser = subparsers.add_parser(
        "run",
        parents=client_parents,
        help="Run a scenario against a target",
        formatter_class=RawDescriptionHelpFormatter,
    )
    _add_run_arguments(parser=run_parser, scenario_params=scenario_params)

    for verb, help_text in _LIST_VERBS.items():
        subparsers.add_parser(verb, parents=client_parents, help=help_text)

    add_init_parser = subparsers.add_parser(
        "add-initializer",
        parents=client_parents,
        help="Register initializer(s) from Python script file(s)",
    )
    add_init_parser.add_argument(
        "files",
        type=str,
        nargs="+",
        metavar="FILE",
        help="Initializer script file(s) to register",
    )

    results_parser = subparsers.add_parser(
        "scenario-results",
        parents=client_parents,
        help="Inspect the results of a completed scenario run",
    )
    results_parser.add_argument("scenario_result_id", type=str, help="Scenario result id to inspect")
    add_results_arguments(parser=results_parser)

    history_parser = subparsers.add_parser(
        "scenario-history",
        parents=client_parents,
        help="List recent scenario runs",
    )
    history_parser.add_argument(
        "limit",
        type=positive_int,
        nargs="?",
        default=10,
        metavar="N",
        help="Number of recent runs to show (default: 10)",
    )

    subparsers.add_parser(
        "start-server", parents=[common_parent, start_server_parent], help="Start a local backend server"
    )
    subparsers.add_parser("stop-server", parents=[common_parent], help="Stop the backend server")

    return parser


def _discover_verbs() -> frozenset[str]:
    """
    Read the registered subcommand verbs straight off the built parser's subparsers.

    Returns:
        frozenset[str]: Every registered subcommand verb.
    """
    parser = _build_parser(add_help=False)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    return frozenset[str]()


#: Every valid subcommand verb (used by the legacy-argv shim to detect new-style calls).
#: Derived from _build_parser so adding/renaming a subcommand can't leave this stale.
_KNOWN_VERBS: frozenset[str] = _discover_verbs()


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


def _add_scenario_params_from_api(*, parser: ArgumentParser, params: list[Parameter]) -> None:
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


#: Legacy "mode flag" → new subcommand verb, used by the back-compat shim.
#: ``--start-server`` is intentionally absent: it stays a global modifier flag and
#: only maps to the ``start-server`` verb when it appears with no other command.
_LEGACY_COMMAND_FLAGS: dict[str, str] = {
    "--list-scenarios": "list-scenarios",
    "--list-initializers": "list-initializers",
    "--list-targets": "list-targets",
    "--list-converters": "list-converters",
    "--list-datasets": "list-datasets",
    "--add-initializer": "add-initializer",
    "--stop-server": "stop-server",
}


def _warn_legacy(*, old: str, new: str) -> None:
    """Warn (visibly and via ``DeprecationWarning``) about a legacy ``pyrit_scan`` invocation."""
    from pyrit.common.deprecation import print_deprecation_message

    print_deprecation_message(old_item=f"pyrit_scan {old}", new_item=f"pyrit_scan {new}", removed_in="1.3.0")
    # DeprecationWarning is suppressed by default in a CLI, so also print a visible note.
    print(f"Note: 'pyrit_scan {old}' is deprecated; use 'pyrit_scan {new}' instead.", file=sys.stderr)


def _translate_legacy_argv(argv: list[str]) -> list[str]:
    """
    Rewrite legacy flag-style invocations into the new subcommand form.

    Back-compat shim for one release. Maps ``--list-scenarios`` → ``list-scenarios``
    etc., a bare ``<scenario>`` → ``run <scenario>`` (implicit run), and a standalone
    ``--start-server`` → the ``start-server`` verb, emitting a deprecation warning
    for each. It also (without warning) moves a new-style verb to the front when it
    was placed after global options (e.g. ``--server-url X list-scenarios``), so
    globals work before or after the verb. New-style calls that already start with a
    verb pass through untouched, as does the brand-new ``scenario-results`` surface
    (no legacy form). Delete this function when the deprecation window closes.

    Args:
        argv (list[str]): The raw argument list (already ``sys.argv[1:]``).

    Returns:
        list[str]: The possibly-rewritten argument list to feed to argparse.
    """
    if not argv or argv[0] in _KNOWN_VERBS or argv[0] in ("-h", "--help"):
        return argv

    # A legacy command flag anywhere in argv → prepend its verb, drop the flag.
    for index, token in enumerate(argv):
        verb = _LEGACY_COMMAND_FLAGS.get(token)
        if verb is not None:
            _warn_legacy(old=token, new=verb)
            return [verb, *argv[:index], *argv[index + 1 :]]

    # No legacy command flag. Strip global options with the global parser and
    # inspect what remains.
    _, leftover = _build_global_parser().parse_known_args(argv)
    if leftover:
        if leftover[0] in _KNOWN_VERBS:
            # A new-style verb placed after global options (e.g.
            # ``--server-url X list-scenarios``). Move the verb to the front so its
            # sub-parser sees the globals. This is valid ordering, not a legacy
            # form, so it does not warn.
            verb = leftover[0]
            index = argv.index(verb)
            return [verb, *argv[:index], *argv[index + 1 :]]
        if leftover[0] in ("-h", "--help"):
            # Global options followed by top-level help (e.g. ``--server-url X --help``).
            # Leave argv untouched so the root parser prints its own help instead of
            # misreading ``--help`` as an implicit scenario name.
            return argv
        # Otherwise it is a bare scenario name (+ run flags) → implicit run.
        _warn_legacy(old="<scenario> (implicit run)", new="run <scenario>")
        return ["run", *argv]

    # Only global options remained: a standalone --start-server means "just start".
    if "--start-server" in argv:
        _warn_legacy(old="--start-server", new="start-server")
        return ["start-server", *[token for token in argv if token != "--start-server"]]

    return argv


def parse_args(args: list[str] | None = None) -> Namespace:
    """
    Parse command-line arguments (pass 1 — tolerant of scenario-declared flags).

    The raw argv is first run through ``_translate_legacy_argv`` (the back-compat
    shim). Pass 1 then uses ``parse_known_args`` so scenario-specific flags (e.g.
    ``--max-turns 7``) don't error before we've fetched the scenario's declared
    parameters. Unknown leftovers are stashed on the Namespace as ``_unknown_args``,
    and the translated argv as ``_translated_args``, for the ``run`` reparse.

    Args:
        args: Argument list (``sys.argv[1:]`` when None).

    Returns:
        Namespace: Parsed command-line arguments.
    """
    raw_args = list(args) if args is not None else list(sys.argv[1:])
    translated = _translate_legacy_argv(raw_args)
    parser = _build_parser(add_help=True)
    parsed, unknown = parser.parse_known_args(translated)
    parsed._unknown_args = unknown
    parsed._translated_args = translated
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

    Raises:
        TypeError: If the configured server URL is not a string.
    """
    from pyrit.cli._config_reader import DEFAULT_SERVER_URL, read_server_settings
    from pyrit.cli._server_launcher import ServerLauncher, parse_local_server_address

    server_settings = read_server_settings(config_file=parsed_args.config_file)
    base_url = parsed_args.server_url or server_settings.url or DEFAULT_SERVER_URL
    if not isinstance(base_url, str):
        raise TypeError(f"Configured server URL must be a string, got {type(base_url).__name__}")
    startup_timeout = getattr(parsed_args, "startup_timeout", None) or server_settings.startup_timeout

    # Probe existing server
    if await ServerLauncher.probe_health_async(base_url=base_url):
        return base_url

    # Auto-start if requested
    if parsed_args.start_server:
        local_address = parse_local_server_address(base_url=base_url)
        if local_address is None:
            print(
                f"Error: cannot --start-server because the configured server URL ({base_url}) "
                "is not a plain local HTTP URL. Use localhost or 127.0.0.1, "
                "or start a remote backend separately.",
                file=sys.stderr,
            )
            return None
        host, port = local_address
        launcher = ServerLauncher()
        try:
            return await launcher.start_async(
                host=host,
                port=port,
                config_file=parsed_args.config_file,
                startup_timeout=startup_timeout,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}")
            return None

    return None


def _resolve_configured_server_url(*, parsed_args: Namespace) -> str:
    """
    Resolve the effective server URL (without probing).

    Returns:
        str: The configured server URL, falling back to the built-in default.

    Raises:
        TypeError: If the configured server URL is not a string.
    """
    from pyrit.cli._config_reader import DEFAULT_SERVER_URL, read_server_url

    server_url = parsed_args.server_url or read_server_url(config_file=parsed_args.config_file) or DEFAULT_SERVER_URL
    if not isinstance(server_url, str):
        raise TypeError(f"Configured server URL must be a string, got {type(server_url).__name__}")
    return server_url


def _resolve_auth_mode(*, parsed_args: Namespace) -> AuthMode:
    """
    Resolve the authentication mode from CLI and layered configuration.

    Returns:
        The selected authentication mode.

    Raises:
        ValueError: If an unsupported mode is supplied programmatically.
    """
    from pyrit.cli._config_reader import read_server_settings

    configured_mode = read_server_settings(config_file=parsed_args.config_file).auth_mode
    raw_auth_mode = getattr(parsed_args, "auth_mode", None)
    if raw_auth_mode is None:
        return configured_mode
    if raw_auth_mode not in AUTH_MODES:
        raise ValueError(f"Unsupported authentication mode: {raw_auth_mode}")
    return cast("AuthMode", raw_auth_mode)


async def _handle_stop_server_async(*, parsed_args: Namespace) -> int:
    """
    Handle ``stop-server``: probe, then terminate the listening process.

    Returns:
        int: Zero when no server is running or shutdown succeeds; one otherwise.
    """
    from pyrit.cli._server_launcher import ServerLauncher, parse_local_server_address, stop_server_on_port

    base_url = _resolve_configured_server_url(parsed_args=parsed_args)
    local_address = parse_local_server_address(base_url=base_url)
    if local_address is None:
        print(f"Cannot stop non-local server {base_url}. Stop it on its host instead.", file=sys.stderr)
        return 1
    if not await ServerLauncher.probe_health_async(base_url=base_url):
        print(f"No server running at {base_url}.")
        return 0

    _, port = local_address
    if not await asyncio.to_thread(stop_server_on_port, port=port):
        print(f"Server at {base_url} is running but could not be stopped.")
        print(f"Find and kill it manually: look for a process listening on port {port}.")
        return 1
    if await ServerLauncher.probe_health_async(base_url=base_url):
        print(f"Server process exited, but a healthy backend is still responding at {base_url}.")
        return 1
    print(f"Server on port {port} stopped.")
    return 0


async def _handle_list_commands_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Dispatch a ``list-*`` verb.

    Returns:
        int: Exit code (always ``0`` on success).
    """
    from pyrit.cli import _output

    command = parsed_args.command
    if command == "list-scenarios":
        _output.print_scenario_list(items=await client.list_scenarios_async())
    elif command == "list-initializers":
        _output.print_initializer_list(items=await client.list_initializers_async())
    elif command == "list-targets":
        _output.print_target_list(items=await client.list_targets_async())
    elif command == "list-datasets":
        resp = await client.list_datasets_async()
        _output.print_dataset_list(items=resp.get("items", []))
    elif command == "list-converters":
        resp = await client.list_converters_async()
        _output.print_converter_list(items=resp.get("items", []))
    return 0


async def _handle_add_initializer_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Handle ``add-initializer``: upload one or more scripts to the server.

    Returns:
        int: Exit code (``0`` on success, ``1`` on failure).
    """
    from pyrit.cli.api_client import ServerNotAvailableError

    for script_path_str in parsed_args.files:
        script_path = await asyncio.to_thread(Path(script_path_str).resolve)
        if not await asyncio.to_thread(script_path.exists):
            print(f"Error: File not found: {script_path}")
            return 1
        try:
            async with aiofiles.open(script_path) as script_file:
                script_content = await script_file.read()
            await client.register_initializer_async(
                name=script_path.stem,
                script_content=script_content,
            )
            print(f"Registered initializer '{script_path.stem}' from {script_path}")
        except ServerNotAvailableError as exc:
            print(f"Error: {exc}")
            return 1
    return 0


async def _handle_results_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Handle the ``scenario-results`` verb: fetch a run and render the requested view.

    Returns:
        int: Exit code (``0`` on success, ``1`` on error).
    """
    from pyrit.cli import _output
    from pyrit.cli._cli_args import ScenarioResultView
    from pyrit.cli._results import (
        apply_view_limit_policy,
        build_attacks_table_payload,
        build_conversations_payload_async,
        resolve_view,
    )

    scenario_result_id = parsed_args.scenario_result_id
    view = resolve_view(view=parsed_args.view)
    limit = apply_view_limit_policy(view=view, limit=parsed_args.limit, attack_result_ids=parsed_args.attack_result_ids)

    try:
        result = await client.get_scenario_run_results_async(scenario_result_id=scenario_result_id)
    except Exception as exc:
        _print_cli_exception(exc=exc)
        return 1

    if view is ScenarioResultView.OVERVIEW:
        await _output.print_scenario_result_async(result=result)
        return 0

    if view in (ScenarioResultView.ATTACKS, ScenarioResultView.FULL):
        attacks_payload = build_attacks_table_payload(
            result=result,
            scenario_result_id=scenario_result_id,
            attack_result_ids=parsed_args.attack_result_ids,
            limit=limit,
        )
        _output.print_attacks_table(payload=attacks_payload)
        if view is ScenarioResultView.ATTACKS:
            return 0

    try:
        conversations_payload = await build_conversations_payload_async(
            result=result,
            client=client,
            scenario_result_id=scenario_result_id,
            attack_result_ids=parsed_args.attack_result_ids,
            limit=limit,
        )
    except Exception as exc:
        _print_cli_exception(exc=exc)
        return 1
    _output.print_conversations(payload=conversations_payload)
    return 0


async def _handle_scenario_history_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Handle the ``scenario-history`` verb: list recent scenario runs.

    Returns:
        int: Exit code (always ``0`` on success).
    """
    from pyrit.cli import _output

    runs = await client.list_scenario_runs_async(limit=parsed_args.limit)
    _output.print_scenario_runs_list(runs=runs)
    return 0


def _reparse_with_scenario_params(*, parsed_args: Namespace, supported_params: list[Parameter]) -> Namespace | None:
    """
    Re-parse the ``run`` invocation with scenario-declared flags registered.

    The translated argument list is read from ``parsed_args._translated_args``
    (populated by ``parse_args``). If no scenario-declared parameters exist but
    pass 1 left unknown args behind, surface the error via a strict re-parse.

    Returns:
        Namespace | None: The re-parsed Namespace, or ``None`` on argparse ``SystemExit``.
    """
    translated: list[str] = getattr(parsed_args, "_translated_args", [])

    if not supported_params:
        unknown = getattr(parsed_args, "_unknown_args", None)
        if not unknown:
            return parsed_args
        # Re-parse strictly so argparse prints the standard "unrecognized arguments" error.
        strict_parser = _build_parser(add_help=True)
        try:
            return strict_parser.parse_args(translated)
        except SystemExit:
            return None

    pass2_parser = _build_parser(scenario_params=supported_params, add_help=True)
    try:
        return pass2_parser.parse_args(translated)
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

    if parsed_args.scenario_techniques:
        kwargs["techniques"] = parsed_args.scenario_techniques
    if parsed_args.max_concurrency is not None:
        kwargs["max_concurrency"] = parsed_args.max_concurrency
    if parsed_args.max_retries is not None:
        kwargs["max_retries"] = parsed_args.max_retries
    if parsed_args.dataset_names:
        kwargs["dataset_names"] = parsed_args.dataset_names
    if parsed_args.max_dataset_size is not None:
        kwargs["max_dataset_size"] = parsed_args.max_dataset_size
    if parsed_args.dataset_filters:
        kwargs["dataset_filters"] = collapse_dataset_filters(parsed_args.dataset_filters)
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
    total_techniques: int,
) -> ScenarioRunSummary:
    """
    Poll the server until the run reaches a terminal status.

    Returns:
        ScenarioRunSummary: The final run summary.
    """
    from pyrit.cli import _output
    from pyrit.models import ScenarioRunState

    terminal_states = {ScenarioRunState.COMPLETED, ScenarioRunState.FAILED, ScenarioRunState.CANCELLED}

    seen_retry_attack_ids: set[str] = set()
    while True:
        run: ScenarioRunSummary = await client.get_scenario_run_async(scenario_result_id=scenario_result_id)
        _output.print_scenario_retry_warnings(run=run, seen_attack_ids=seen_retry_attack_ids)
        _output.print_scenario_run_progress(run=run, total_techniques=total_techniques)
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

    total_techniques = len(request.techniques or scenario_meta.all_techniques or [])
    print(f"\nRunning scenario: {scenario_name}")
    sys.stdout.flush()

    try:
        run = await client.start_scenario_run_async(request=request)
    except Exception as exc:
        if _is_read_timeout(exc):
            # The server keeps initializing after the client stops waiting, so whether the run
            # started is genuinely unknown here and must not be reported as a failure to start.
            print("\nERROR: The scenario start request timed out, so it is unknown whether the run started.")
            _print_cli_exception(exc=exc)
            print("Check 'pyrit_scan scenario-history' before retrying, or the run may be started twice.")
        else:
            print("\nERROR: The scenario could not be started.")
            _print_cli_exception(exc=exc)
        return 1

    scenario_result_id = run.scenario_result_id

    try:
        run = await _poll_until_terminal_async(
            client=client,
            scenario_result_id=scenario_result_id,
            total_techniques=total_techniques,
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
            if _is_read_timeout(exc):
                print(f"Retry with 'pyrit_scan scenario-results {scenario_result_id}'.")
            _output.print_scenario_run_summary(run=run)
            return 1
        return 0

    _output.print_scenario_run_summary(run=run)
    return 1


async def _handle_run_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Handle the ``run`` verb: resolve the scenario, reparse its declared flags, then run it.

    Returns:
        int: Exit code (``0`` if the run completed successfully, ``1`` otherwise).
    """
    scenario_name = parsed_args.scenario_name
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

    return await _run_scenario_async(client=client, parsed_args=reparsed, scenario_meta=scenario_meta)


#: Post-client verbs, each a uniform ``(*, client, parsed_args) -> int`` handler. Reached
#: only after the API client is open (start-server/stop-server are handled earlier, before
#: any client exists), so dispatch here is a pure table lookup with no branching.
_CLIENT_HANDLERS: dict[str, Callable[..., Any]] = {
    "run": _handle_run_async,
    "add-initializer": _handle_add_initializer_async,
    "scenario-results": _handle_results_async,
    "scenario-history": _handle_scenario_history_async,
    **dict.fromkeys(_LIST_VERBS, _handle_list_commands_async),
}


async def _dispatch_with_client_async(*, client: Any, parsed_args: Namespace) -> int:
    """
    Dispatch a verb that needs an open API client.

    Returns:
        int: Exit code from the dispatched command.

    Raises:
        TypeError: If the dispatched handler returns a non-int exit code.
    """
    handler = _CLIENT_HANDLERS[parsed_args.command]
    result = await handler(client=client, parsed_args=parsed_args)
    if not isinstance(result, int):
        raise TypeError(
            f"Handler for '{parsed_args.command}' must return an int exit code, got {type(result).__name__}"
        )
    return result


async def _run_async(*, parsed_args: Namespace) -> int:
    """
    Core async logic for pyrit_scan.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    from pyrit.cli import _output
    from pyrit.cli.api_client import PyRITApiClient, ServerNotAvailableError

    command = parsed_args.command

    # stop-server needs no API client.
    if command == "stop-server":
        return await _handle_stop_server_async(parsed_args=parsed_args)

    # The start-server verb forces an auto-start attempt, then just confirms.
    if command == "start-server":
        parsed_args.start_server = True

    base_url_result = await _resolve_server_url_async(parsed_args=parsed_args)
    if base_url_result is None:
        attempted = _resolve_configured_server_url(parsed_args=parsed_args)
        _output.print_error_with_hint(
            message=f"Server not available at {attempted}",
            hint="Use 'start-server' to launch a local backend, or pass '--server-url <url>'.",
        )
        return 1

    if command == "start-server":
        print(f"Server is running at {base_url_result}")
        return 0

    try:
        async with PyRITApiClient(
            base_url=base_url_result,
            request_timeout=getattr(parsed_args, "request_timeout", None),
            auth_mode=_resolve_auth_mode(parsed_args=parsed_args),
        ) as client:
            return await _dispatch_with_client_async(client=client, parsed_args=parsed_args)
    except ServerNotAvailableError as exc:
        _output.print_error_with_hint(
            message=str(exc),
            hint="Use 'start-server' to launch a local backend, or pass '--server-url <url>'.",
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

    # No verb at all: show the top-level help listing the subcommands.
    if getattr(parsed_args, "command", None) is None:
        _build_parser().print_help()
        return 0

    # Unknown flags are only expected for `run` (scenario-declared flags, resolved
    # in the reparse). For any other verb they are genuinely unrecognized.
    unknown = getattr(parsed_args, "_unknown_args", [])
    if unknown and parsed_args.command != "run":
        strict_parser = _build_parser(add_help=True)
        try:
            strict_parser.parse_args(parsed_args._translated_args)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1

    logging.basicConfig(level=getattr(parsed_args, "log_level", logging.WARNING))

    from pyrit.cli._config_reader import ConfigError, validate_client_config

    try:
        validate_client_config(config_file=getattr(parsed_args, "config_file", None))
        return asyncio.run(_run_async(parsed_args=parsed_args))
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
