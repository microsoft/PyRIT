# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT Shell - Interactive REPL for PyRIT.

This module provides an interactive shell that talks to the PyRIT backend
server over HTTP. No heavy pyrit imports — all operations go through REST.
"""

from __future__ import annotations

import asyncio
import cmd
import concurrent.futures
import contextlib
import logging
import os
import shlex
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.cli import _banner as banner
from pyrit.cli._auth import AUTH_MODES, AuthMode

if TYPE_CHECKING:
    from collections.abc import Coroutine

_T = TypeVar("_T")


def _split_initializer_paths(arg: str) -> list[str]:
    """
    Split a command-line argument string into individual file paths.

    Supports quoting paths that contain spaces. On Windows, backslashes are treated
    as literal path separators (not escape characters) so that unquoted paths such as
    ``C:\\Users\\me\\init.py`` are preserved; surrounding quotes are stripped from each
    token. On POSIX systems, standard ``shlex`` parsing is used.

    Args:
        arg: The raw argument string passed to the ``add-initializer`` command.

    Returns:
        The list of individual file path strings parsed from ``arg``.

    Raises:
        ValueError: If the argument contains unbalanced quotes.
    """
    if os.name == "nt":
        lexer = shlex.shlex(arg, posix=False)
        lexer.whitespace_split = True
        tokens = list(lexer)
        return [_strip_surrounding_quotes(token) for token in tokens]
    return shlex.split(arg)


def _strip_surrounding_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _print_shell_exception(*, exc: BaseException) -> None:
    """
    Print a user-facing error line for an exception raised by a shell command.

    Mirrors ``pyrit_scan._print_cli_exception`` but never suggests ``--request-timeout``,
    which the shell does not accept. A bare ``httpx.ReadTimeout`` stringifies to nothing,
    so it needs its own line or the command reports an empty error.

    Args:
        exc (BaseException): The exception caught by the shell command.
    """
    from pyrit.cli.pyrit_scan import _is_read_timeout, _print_debug_traceback

    if _is_read_timeout(exc):
        print("\nError (ReadTimeout): server did not respond in time. Check the server logs for a blocked event loop.")
    else:
        print(f"\nError ({type(exc).__name__}): {str(exc) or repr(exc)}")

    _print_debug_traceback(exc)


class PyRITShell(cmd.Cmd):
    """
    Interactive shell for PyRIT (thin REST client).

    Commands:
        list-scenarios             - List all available scenarios
        list-initializers          - List all available initializers
        list-targets               - List all available targets
        list-converters            - List all registered converter instances
        add-initializer <file>...  - Register initializer(s) from Python script file(s)
        run <scenario> [opts]      - Run a scenario with optional parameters
        scenario-history [N]       - List the last N (default 10) scenario runs
        scenario-results [id]      - Inspect a run: --view overview|attacks|conversations|full
        print-scenario [id]        - Deprecated alias for 'scenario-results'
        start-server               - Start a local backend server
        stop-server                - Stop the owned backend server
        help [command]             - Show help for a command
        clear                      - Clear the screen
        exit (quit, q)             - Exit the shell
    """

    prompt = "pyrit> "

    def __init__(
        self,
        *,
        no_animation: bool = False,
        server_url: str | None = None,
        config_file: Path | None = None,
        start_server: bool = False,
        auth_mode: AuthMode | None = None,
    ) -> None:
        """
        Initialize the PyRIT shell.

        Args:
            no_animation: If True, skip the animated startup banner.
            server_url: Optional explicit server URL.
            config_file: Optional config file path.
            start_server: If True, auto-start a local backend.
            auth_mode: Optional backend authentication mode override.
        """
        super().__init__()
        self._no_animation = no_animation
        self._server_url = server_url
        self._config_file = config_file
        self._start_server = start_server
        self._auth_mode = auth_mode
        self._api_client: Any = None  # PyRITApiClient (lazy)
        self._base_url: str | None = None
        self._launcher: Any = None  # ServerLauncher (lazy)

        # Persistent event loop running on a background thread. All async
        # calls (health probe, REST methods, scenario polling) are scheduled
        # here so the shared httpx.AsyncClient stays in a single loop.
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, name="pyrit-shell-loop", daemon=True)
        self._loop_thread.start()

    def _run_async(self, coro: Coroutine[Any, Any, _T], *, timeout: float | None = 120.0) -> _T:
        """
        Run a coroutine on the shell's persistent loop and return its result.

        Args:
            coro: Coroutine to schedule on the background loop.
            timeout: Maximum seconds to wait. ``None`` waits forever. Defaults to
                120s, which comfortably covers every per-call REST request and
                the 30s server startup probe.

        Returns:
            The coroutine's result.

        Raises:
            TimeoutError: If the coroutine does not complete within *timeout*.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"Backend call did not complete within {timeout}s. The server may be hung or "
                "unreachable; try `stop-server` and re-running."
            ) from exc

    def _shutdown_loop(self) -> None:
        """Stop the background event loop and join the thread."""
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
            with contextlib.suppress(Exception):
                self._loop.close()

    def _resolve_base_url(self) -> str:
        """
        Determine the server base URL.

        Returns:
            str: The configured base URL, falling back to the built-in default.
        """
        from pyrit.cli._config_reader import DEFAULT_SERVER_URL, read_server_url

        if self._server_url:
            return self._server_url
        return read_server_url(config_file=self._config_file) or DEFAULT_SERVER_URL

    def _resolve_auth_mode(self) -> AuthMode:
        """
        Determine the backend authentication mode.

        Returns:
            The selected authentication mode.
        """
        from pyrit.cli._config_reader import read_server_settings

        return self._auth_mode or read_server_settings(config_file=self._config_file).auth_mode

    def _open_client(self, *, base_url: str) -> bool:
        """
        Open an API client while keeping connection failures inside the REPL.

        Returns:
            ``True`` when the client is ready, otherwise ``False``.
        """
        import httpx

        from pyrit.cli._auth import CliAuthenticationError
        from pyrit.cli._config_reader import ConfigError
        from pyrit.cli._output import print_error_with_hint
        from pyrit.cli.api_client import PyRITApiClient

        self._base_url = base_url
        try:
            client = PyRITApiClient(base_url=base_url, auth_mode=self._resolve_auth_mode())
            self._run_async(client.__aenter__(), timeout=None)
        except CliAuthenticationError as exc:
            self._api_client = None
            print_error_with_hint(
                message=str(exc),
                hint="Use --auth-mode to select auto, device_code, azure_cli, or none.",
            )
            return False
        except ConfigError as exc:
            self._api_client = None
            print(f"Error: {exc}")
            return False
        except httpx.HTTPError as exc:
            self._api_client = None
            print_error_with_hint(
                message=f"Could not initialize the client for {base_url}: {exc}",
                hint="Check the server's /api/auth/config endpoint and your network connection.",
            )
            return False

        self._api_client = client
        return True

    def _ensure_client(self) -> bool:
        """
        Ensure the API client is connected.

        Returns:
            bool: ``True`` if the client is ready, ``False`` otherwise.
        """
        if self._api_client is not None:
            return True

        if self._base_url:
            base_url = self._base_url
        else:
            from pyrit.cli._config_reader import ConfigError

            try:
                base_url = self._resolve_base_url()
            except ConfigError as exc:
                print(f"Error: {exc}")
                return False

        # Check health
        from pyrit.cli._server_launcher import ServerLauncher

        healthy = self._run_async(ServerLauncher.probe_health_async(base_url=base_url))

        if not healthy and self._start_server:
            self._launcher = ServerLauncher()
            try:
                base_url = self._run_async(
                    self._launcher.start_async(config_file=self._config_file),
                    timeout=None,
                )
                healthy = True
            except RuntimeError as exc:
                print(f"Error starting server: {exc}")
                return False

        if not healthy:
            from pyrit.cli._output import print_error_with_hint

            print_error_with_hint(
                message=f"Server not available at {base_url}",
                hint="Use 'start-server' to launch a local backend, or restart with --server-url.",
            )
            return False

        if not self._open_client(base_url=base_url):
            return False
        self._start_server = False  # only auto-start once
        return True

    def cmdloop(self, intro: str | None = None) -> None:
        """Override cmdloop to play animated banner before starting the REPL."""
        if intro is None:
            prev_disable = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            try:
                intro = banner.play_animation(no_animation=self._no_animation)
            finally:
                logging.disable(prev_disable)
        self.intro = intro
        super().cmdloop(intro=self.intro)

    # ------------------------------------------------------------------
    # List commands
    # ------------------------------------------------------------------

    def do_list_scenarios(self, arg: str) -> None:
        """List all available scenarios."""
        if arg.strip():
            print(f"Error: list-scenarios does not accept arguments, got: {arg.strip()}")
            return
        if not self._ensure_client():
            return
        from pyrit.cli import _output

        try:
            scenarios = self._run_async(self._api_client.list_scenarios_async())
            _output.print_scenario_list(items=scenarios)
        except Exception as e:
            print(f"Error listing scenarios: {e}")

    def do_list_initializers(self, arg: str) -> None:
        """List all available initializers."""
        if arg.strip():
            print(f"Error: list-initializers does not accept arguments, got: {arg.strip()}")
            return
        if not self._ensure_client():
            return
        from pyrit.cli import _output

        try:
            initializers = self._run_async(self._api_client.list_initializers_async())
            _output.print_initializer_list(items=initializers)
        except Exception as e:
            print(f"Error listing initializers: {e}")

    def do_list_targets(self, arg: str) -> None:
        """List all available targets."""
        if arg.strip():
            print(f"Error: list-targets does not accept arguments, got: {arg.strip()}")
            return
        if not self._ensure_client():
            return
        from pyrit.cli import _output

        try:
            targets = self._run_async(self._api_client.list_targets_async())
            _output.print_target_list(items=targets)
        except Exception as e:
            print(f"Error listing targets: {e}")

    def do_list_converters(self, arg: str) -> None:
        """List all registered converter instances."""
        if arg.strip():
            print(f"Error: list-converters does not accept arguments, got: {arg.strip()}")
            return
        if not self._ensure_client():
            return
        from pyrit.cli import _output

        try:
            resp = self._run_async(self._api_client.list_converters_async())
            _output.print_converter_list(items=resp.get("items", []))
        except Exception as e:
            print(f"Error listing converters: {e}")

    def do_add_initializer(self, arg: str) -> None:
        """
        Register an initializer from a Python script file.

        Usage:
            add-initializer <file_path> [<file_path> ...]
        """
        if not self._ensure_client():
            return
        if not arg.strip():
            print("Usage: add-initializer <file_path> [<file_path> ...]")
            return

        from pyrit.cli.api_client import ServerNotAvailableError

        try:
            script_path_strings = _split_initializer_paths(arg)
        except ValueError as exc:
            print(f"Error parsing initializer paths: {exc}")
            return

        for script_path_str in script_path_strings:
            script_path = Path(script_path_str).resolve()
            if not script_path.exists():
                print(f"Error: File not found: {script_path}")
                return
            try:
                content = script_path.read_text()
                self._run_async(
                    self._api_client.register_initializer_async(name=script_path.stem, script_content=content)
                )
                print(f"Registered initializer '{script_path.stem}' from {script_path}")
            except ServerNotAvailableError as exc:
                print(f"Error: {exc}")
                return
            except Exception as exc:
                print(f"Error registering initializer: {exc}")
                return

    # ------------------------------------------------------------------
    # Run command
    # ------------------------------------------------------------------

    def do_run(self, line: str) -> None:
        """
        Run a scenario.

        Usage:
            run <scenario_name> [options]

        Options:
            --target <name>                 Target name (required)
            --initializers <name> ...       Initializer names (supports name:key=val syntax)
            --techniques, -t <t1> <t2> ...  Technique names. Append registered
                                            converters to a technique with
                                            ':converter.<name>' (repeatable), e.g.
                                            role_play_movie_script:converter.translation_spanish.
                                            Use list-converters to see names.
            --max-concurrency <N>           Maximum concurrent operations
            --max-retries <N>               Maximum retry attempts
            --memory-labels <JSON>          JSON string of labels
            --dataset-names <name> ...      Override default dataset names
            --max-dataset-size <N>          Maximum items per dataset
            --<scenario-flag> <value>       Scenario-declared parameters (see list-scenarios)

        Notes:
            Database, env files, and initialization scripts are configured on
            the backend via its config file. Use `add-initializer` to register
            custom initializers on the running server.
        """
        if not self._ensure_client():
            return

        if not line.strip():
            print("Error: Specify a scenario name")
            print("Usage: run <scenario_name> --target <name> [options]")
            return

        from pyrit.cli._cli_args import (
            build_parameters_from_api,
            collapse_dataset_filters,
            extract_scenario_args,
            parse_run_arguments,
        )
        from pyrit.cli._output import (
            print_scenario_result_async,
            print_scenario_run_progress,
            print_scenario_run_summary,
        )
        from pyrit.cli.pyrit_scan import _is_read_timeout, _print_cli_exception, _print_debug_traceback
        from pyrit.models import ScenarioRunState
        from pyrit.models.catalog import RunScenarioRequest

        # Fetch scenario metadata so the parser recognizes scenario-declared flags.
        scenario_name_token = line.split(maxsplit=1)[0]
        try:
            scenario_meta = self._run_async(self._api_client.get_scenario_async(scenario_name=scenario_name_token))
        except Exception as exc:
            print(f"Error fetching scenario metadata: {exc}")
            return
        if scenario_meta is None:
            print(f"Error: Scenario '{scenario_name_token}' not found on server.")
            return
        declared_params = build_parameters_from_api(api_params=scenario_meta.supported_parameters)

        # Parse arguments
        try:
            args = parse_run_arguments(args_string=line, declared_params=declared_params)
        except ValueError as e:
            print(f"Error: {e}")
            return

        scenario_name = args["scenario_name"]

        # Build typed request
        request_kwargs: dict[str, Any] = {
            "scenario_name": scenario_name,
            "target_name": args.get("target") or "",
        }

        # Map initializers
        initializers = args.get("initializers")
        if initializers:
            init_names: list[str] = []
            init_args: dict[str, dict[str, Any]] = {}
            for entry in initializers:
                if isinstance(entry, str):
                    init_names.append(entry)
                elif isinstance(entry, dict):
                    name = entry["name"]
                    init_names.append(name)
                    if entry.get("args"):
                        init_args[name] = entry["args"]
            request_kwargs["initializers"] = init_names
            if init_args:
                request_kwargs["initializer_args"] = init_args

        if args.get("scenario_techniques"):
            request_kwargs["techniques"] = args["scenario_techniques"]
        if args.get("max_concurrency") is not None:
            request_kwargs["max_concurrency"] = args["max_concurrency"]
        if args.get("max_retries") is not None:
            request_kwargs["max_retries"] = args["max_retries"]
        if args.get("dataset_names"):
            request_kwargs["dataset_names"] = args["dataset_names"]
        if args.get("max_dataset_size") is not None:
            request_kwargs["max_dataset_size"] = args["max_dataset_size"]
        if args.get("dataset_filters"):
            request_kwargs["dataset_filters"] = collapse_dataset_filters(args["dataset_filters"])
        if args.get("memory_labels"):
            request_kwargs["labels"] = args["memory_labels"]

        scenario_params = extract_scenario_args(parsed=args)
        if scenario_params:
            request_kwargs["scenario_params"] = scenario_params

        request = RunScenarioRequest(**request_kwargs)

        # Start run
        print(f"\nRunning scenario: {scenario_name}")
        sys.stdout.flush()

        try:
            run = self._run_async(self._api_client.start_scenario_run_async(request=request))
        except Exception as exc:
            if _is_read_timeout(exc):
                # The server keeps initializing after the client stops waiting, so whether the
                # run started is unknown. The shell has no --request-timeout, so it handles the
                # timeout itself rather than going through the shared printer.
                print("\nERROR: The scenario start request timed out, so it is unknown whether the run started.")
                print(
                    "\nError (ReadTimeout): server did not respond in time. Check "
                    "'scenario-history' before retrying, or the run may be started twice. Check "
                    "the server logs for a blocked event loop."
                )
                _print_debug_traceback(exc)
            else:
                print("\nERROR: The scenario could not be started.")
                _print_cli_exception(exc=exc)
            return

        scenario_result_id = run.scenario_result_id

        # Poll for completion
        import time

        try:
            while True:
                run = self._run_async(self._api_client.get_scenario_run_async(scenario_result_id=scenario_result_id))
                print_scenario_run_progress(run=run)
                if run.status in {
                    ScenarioRunState.COMPLETED,
                    ScenarioRunState.FAILED,
                    ScenarioRunState.CANCELLED,
                }:
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n\nCancelling scenario run...")
            try:
                self._run_async(self._api_client.cancel_scenario_run_async(scenario_result_id=scenario_result_id))
                print("Scenario run cancelled.")
            except Exception:
                print("Warning: could not cancel scenario run.")
            print("Returning to shell.")
            return

        # Print results
        if run.status == ScenarioRunState.COMPLETED:
            try:
                detail = self._run_async(
                    self._api_client.get_scenario_run_results_async(scenario_result_id=scenario_result_id)
                )
                self._run_async(print_scenario_result_async(result=detail))
            except Exception as exc:
                print(
                    "\nERROR: The scenario completed, but its detailed results could not be "
                    "retrieved or parsed from the server."
                )
                if _is_read_timeout(exc):
                    # The shell has no --request-timeout, so it must not reach the shared
                    # printer, which advises it.
                    print(
                        f"\nError (ReadTimeout): server did not respond in time. Retry with "
                        f"'scenario-results {scenario_result_id}', or check the server logs for a "
                        "blocked event loop."
                    )
                    _print_debug_traceback(exc)
                else:
                    _print_cli_exception(exc=exc)
                print_scenario_run_summary(run=run)
        else:
            print_scenario_run_summary(run=run)

    # ------------------------------------------------------------------
    # History commands
    # ------------------------------------------------------------------

    def do_scenario_history(self, arg: str) -> None:
        """
        Display history of scenario runs from the server (most recent first).

        Usage:
            scenario-history          Show the last 10 runs
            scenario-history <N>      Show the last N runs
        """
        arg = arg.strip()
        limit = 10
        if arg:
            try:
                limit = int(arg)
            except ValueError:
                limit = 0
            if limit < 1:
                print(f"Usage: scenario-history [N]. Got non-positive-integer argument: {arg!r}")
                return
        if not self._ensure_client():
            return
        from pyrit.cli._output import print_scenario_runs_list

        try:
            runs = self._run_async(self._api_client.list_scenario_runs_async(limit=limit))
            print_scenario_runs_list(runs=runs)
        except Exception as e:
            _print_shell_exception(exc=e)

    def do_scenario_results(self, arg: str) -> None:
        """
        Inspect the results of a completed scenario run.

        Usage:
            scenario-results <scenario_result_id>
                [--view overview|attacks|conversations|full]
                [--attack-result-ids <id> ...] [--limit N]

        Views:
            overview       Scenario-level aggregate: totals and per-group success
                           rates (the default).
            attacks        One row per attack result (id, objective, outcome,
                           turns, score).
            conversations  The main-conversation transcript for each attack
                           (messages plus their scores and full rationale).
            full           The attacks table followed by the transcripts.

        For conversations/full, when neither --attack-result-ids nor --limit is
        given, at most 5 attacks are shown to avoid dumping a whole run.
        """
        if not self._ensure_client():
            return

        import shlex

        from pyrit.cli._cli_args import ScenarioResultView, build_scenario_results_parser
        from pyrit.cli._output import print_attacks_table, print_conversations, print_scenario_result_async
        from pyrit.cli._results import (
            apply_view_limit_policy,
            build_attacks_table_payload,
            build_conversations_payload_async,
            resolve_view,
        )

        try:
            tokens = shlex.split(arg)
        except ValueError as exc:
            print(f"Error parsing arguments: {exc}")
            return
        if not tokens:
            print(
                "Usage: scenario-results <scenario_result_id> "
                "[--view overview|attacks|conversations|full] [--attack-result-ids <id> ...] [--limit N]"
            )
            print("Use 'scenario-history' to see available run IDs.")
            return

        parser = build_scenario_results_parser()
        try:
            parsed = parser.parse_args(tokens)
        except SystemExit:
            return

        view = resolve_view(view=parsed.view)
        limit = apply_view_limit_policy(view=view, limit=parsed.limit, attack_result_ids=parsed.attack_result_ids)

        try:
            result = self._run_async(
                self._api_client.get_scenario_run_results_async(scenario_result_id=parsed.scenario_result_id)
            )
        except Exception as exc:
            _print_shell_exception(exc=exc)
            return

        if view is ScenarioResultView.OVERVIEW:
            self._run_async(print_scenario_result_async(result=result))
            return

        if view in (ScenarioResultView.ATTACKS, ScenarioResultView.FULL):
            attacks_payload = build_attacks_table_payload(
                result=result,
                scenario_result_id=parsed.scenario_result_id,
                attack_result_ids=parsed.attack_result_ids,
                limit=limit,
            )
            print_attacks_table(payload=attacks_payload)
            if view is ScenarioResultView.ATTACKS:
                return

        try:
            conversations_payload = self._run_async(
                build_conversations_payload_async(
                    result=result,
                    client=self._api_client,
                    scenario_result_id=parsed.scenario_result_id,
                    attack_result_ids=parsed.attack_result_ids,
                    limit=limit,
                )
            )
        except Exception as exc:
            _print_shell_exception(exc=exc)
            return
        print_conversations(payload=conversations_payload)

    def do_print_scenario(self, arg: str) -> None:
        """
        Print a scenario run's overview (deprecated alias for ``scenario-results``).

        Equivalent to ``scenario-results <id>``, whose default ``overview`` view
        produces the same output.

        Usage:
            print-scenario <scenario_result_id>
        """
        from pyrit.common.deprecation import print_deprecation_message

        print_deprecation_message(
            old_item="print-scenario",
            new_item="scenario-results",
            removed_in="1.3.0",
        )
        # DeprecationWarning is suppressed by default in the REPL, so also print a visible note.
        print("Note: 'print-scenario' is deprecated; use 'scenario-results <id>' instead.")
        self.do_scenario_results(arg.strip())

    # ------------------------------------------------------------------
    # Server management
    # ------------------------------------------------------------------

    def do_start_server(self, arg: str) -> None:
        """Start a local pyrit_backend server."""
        if arg.strip():
            print(f"Error: start-server does not accept arguments, got: {arg.strip()}")
            return
        from pyrit.cli._server_launcher import ServerLauncher

        base_url = self._resolve_base_url()

        # Check if already running
        if self._run_async(ServerLauncher.probe_health_async(base_url=base_url)):
            print(f"Server already running at {base_url}")
            if self._api_client is None:
                self._open_client(base_url=base_url)
            return

        self._launcher = ServerLauncher()
        try:
            new_url = self._run_async(
                self._launcher.start_async(config_file=self._config_file),
                timeout=None,
            )
            self._base_url = new_url
            # Create new client for the started server
            if self._api_client is not None:
                self._run_async(self._api_client.close_async())
                self._api_client = None
            self._open_client(base_url=new_url)
        except RuntimeError as exc:
            print(f"Error: {exc}")

    def do_stop_server(self, arg: str) -> None:
        """Stop the backend server."""
        if arg.strip():
            print(f"Error: stop-server does not accept arguments, got: {arg.strip()}")
            return
        from pyrit.cli._server_launcher import ServerLauncher, parse_local_server_address, stop_server_on_port

        # If we own the launcher, use it directly
        if self._launcher is not None:
            if not self._launcher.stop():
                print("Server could not be stopped.")
                return
            print("Server stopped.")
        else:
            # Find and kill by port. Probe first so we don't SIGTERM a non-pyrit
            # process that happens to be listening on this port.
            base_url = self._base_url or self._resolve_base_url()
            local_address = parse_local_server_address(base_url=base_url)
            if local_address is None:
                print(f"Cannot stop non-local server {base_url}. Stop it on its host instead.")
                return
            _, port = local_address
            if not self._run_async(ServerLauncher.probe_health_async(base_url=base_url)):
                print(f"No pyrit backend responding at {base_url}; not stopping anything.")
                return
            if stop_server_on_port(port=port):
                print(f"Server on port {port} stopped.")
            else:
                print(f"Server on port {port} could not be stopped.")
                return

        # Close the API client since the server is gone
        if self._api_client is not None:
            with contextlib.suppress(Exception):
                self._run_async(self._api_client.close_async())
            self._api_client = None
        self._launcher = None

    # ------------------------------------------------------------------
    # Utility commands
    # ------------------------------------------------------------------

    def do_help(self, arg: str) -> None:
        """Show help. Usage: help [command]."""
        if not arg:
            super().do_help(arg)
            print("\nUse 'help <command>' for details on a specific command.")
        else:
            normalized_arg = arg.replace("-", "_")
            super().do_help(normalized_arg)

    def do_exit(self, arg: str) -> bool:
        """
        Exit the shell.

        Returns:
            bool: Always ``True`` to signal the ``cmd`` loop to terminate.
        """
        if self._api_client is not None:
            with contextlib.suppress(Exception):
                self._run_async(self._api_client.close_async())
            self._api_client = None
        self._shutdown_loop()
        print("\nGoodbye!")
        return True

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        import os

        os.system("cls" if os.name == "nt" else "clear")  # type: ignore[ty:deprecated]

    # Shortcuts and aliases
    do_quit = do_exit
    do_q = do_exit
    do_EOF = do_exit  # noqa: N815

    def emptyline(self) -> bool:
        """
        Don't repeat last command on empty line.

        Returns:
            bool: Always ``False`` so the ``cmd`` loop does not exit.
        """
        return False

    def default(self, line: str) -> None:
        """Handle unknown commands and convert hyphens to underscores."""
        parts = line.split(None, 1)
        if parts:
            cmd_with_underscores = parts[0].replace("-", "_")
            method_name = f"do_{cmd_with_underscores}"
            if hasattr(self, method_name):
                arg = parts[1] if len(parts) > 1 else ""
                getattr(self, method_name)(arg)
                return
        print(f"Unknown command: {line}")
        print("Type 'help' or '?' for available commands")


def main() -> int:
    """
    Entry point for pyrit_shell.

    Returns:
        int: Exit code.
    """
    import argparse

    from pyrit.cli._cli_args import ARG_HELP

    parser = argparse.ArgumentParser(
        prog="pyrit_shell",
        description="PyRIT Interactive Shell - Thin REST client for the PyRIT backend",
    )

    parser.add_argument(
        "--server-url",
        type=str,
        help="URL of the PyRIT backend server (default: http://localhost:8000)",
    )

    parser.add_argument(
        "--start-server",
        action="store_true",
        help="Start a local pyrit_backend server if one is not already running",
    )

    parser.add_argument(
        "--config-file",
        type=Path,
        help=ARG_HELP["config_file"],
    )

    parser.add_argument(
        "--auth-mode",
        choices=AUTH_MODES,
        help="Backend authentication mode (default: server.auth_mode or auto)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Logging level (default: WARNING)",
    )

    parser.add_argument(
        "--no-animation",
        action="store_true",
        default=False,
        help="Disable the animated startup banner",
    )

    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))

    from pyrit.cli._config_reader import ConfigError, validate_client_config

    try:
        validate_client_config(config_file=args.config_file)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Play banner immediately
    prev_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        intro = banner.play_animation(no_animation=args.no_animation)
    finally:
        logging.disable(prev_disable)

    try:
        shell = PyRITShell(
            no_animation=args.no_animation,
            server_url=args.server_url,
            config_file=args.config_file,
            start_server=args.start_server,
            auth_mode=args.auth_mode,
        )
        shell.cmdloop(intro=intro)
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")
        return 0
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
