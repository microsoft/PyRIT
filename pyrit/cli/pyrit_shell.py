# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT Shell - Interactive REPL for PyRIT.

This module provides an interactive shell where PyRIT modules are loaded once
at startup, making subsequent commands instant.
"""

from __future__ import annotations

import asyncio
import cmd
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from pyrit.cli import frontend_core
    from pyrit.models.scenario_result import ScenarioResult

from pyrit.cli import _banner as banner
from pyrit.common.deprecation import print_deprecation_message


class PyRITShell(cmd.Cmd):
    """
    Interactive shell for PyRIT.

    Commands:
        list-scenarios             - List all available scenarios
        list-initializers          - List all available initializers
        list-targets               - List all available targets from the registry
        run <scenario> [opts]      - Run a scenario with optional parameters
        scenario-history           - List all previous scenario runs
        print-scenario [N]         - Print detailed results for scenario run(s)
        help [command]             - Show help for a command
        clear                      - Clear the screen
        exit (quit, q)             - Exit the shell

    Shell Startup Options:
        --config-file <path>    Path to config file (default: ~/.pyrit/.pyrit_conf)
        --log-level <level>     Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) - default for all runs
        --no-animation          Disable the animated startup banner

    Run Command Options:
        --target <name>                 Target name from the TargetRegistry (required)
        --initializers <name> ...       Built-in initializers (supports name:key=val1,val2 syntax)
        --initialization-scripts <...>  Custom Python scripts to run before the scenario
        --strategies, -s <s1> ...       Strategy names to use
        --max-concurrency <N>           Maximum concurrent operations
        --max-retries <N>               Maximum retry attempts
        --memory-labels <JSON>          JSON string of labels
        --log-level <level>             Override default log level for this run
    """

    prompt = "pyrit> "

    def __init__(
        self,
        *,
        no_animation: bool = False,
        config_file: Optional[Path] = None,
        database: Optional[str] = None,
        initialization_scripts: Optional[list[Path]] = None,
        initializer_names: Optional[list[Any]] = None,
        env_files: Optional[list[Path]] = None,
        log_level: Optional[int] = None,
        context: Optional[frontend_core.FrontendCore] = None,
    ) -> None:
        """
        Initialize the PyRIT shell.

        The heavy ``frontend_core`` import, ``FrontendCore`` construction, and
        ``initialize_async`` call all happen on a background thread so the
        shell prompt appears immediately.

        Args:
            no_animation (bool): If True, skip the animated startup banner.
            config_file (Optional[Path]): Path to a YAML configuration file.
            database (Optional[str]): Database type (InMemory, SQLite, or AzureSQL).
            initialization_scripts (Optional[list[Path]]): Initialization script paths.
            initializer_names (Optional[list[Any]]): Initializer entries (names or dicts).
            env_files (Optional[list[Path]]): Environment file paths to load in order.
            log_level (Optional[int]): Logging level constant (e.g., ``logging.WARNING``).
            context (Optional[frontend_core.FrontendCore]): Deprecated. Pre-created FrontendCore
                context. Use the individual keyword arguments instead.

        Raises:
            ValueError: If ``context`` is provided together with any other
                FrontendCore keyword arguments.
        """
        super().__init__()
        self._no_animation = no_animation
        self._context_kwargs: dict[str, Any] = {
            k: v
            for k, v in {
                "config_file": config_file,
                "database": database,
                "initialization_scripts": initialization_scripts,
                "initializer_names": initializer_names,
                "env_files": env_files,
                "log_level": log_level,
            }.items()
            if v is not None
        }

        if context is not None:
            if self._context_kwargs:
                raise ValueError(
                    "Cannot pass 'context' together with FrontendCore keyword arguments "
                    f"({', '.join(self._context_kwargs)}). Use one or the other."
                )
            print_deprecation_message(
                old_item="PyRITShell(context=...)",
                new_item="PyRITShell(database=..., log_level=..., ...)",
                removed_in="0.14.0",
            )
            self._deprecated_context = context
        else:
            self._deprecated_context = None

        # Track scenario execution history: list of (command_string, ScenarioResult) tuples
        self._scenario_history: list[tuple[str, ScenarioResult]] = []

        # Set by the background thread after importing frontend_core.
        self.context: Optional[frontend_core.FrontendCore] = None
        self.default_log_level: Optional[int] = None

        # Initialize PyRIT in background thread for faster startup.
        self._init_thread = threading.Thread(target=self._background_init, daemon=True)
        self._init_complete = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._init_thread.start()

    def _background_init(self) -> None:
        """Import heavy modules and initialize PyRIT in the background."""
        try:
            from pyrit.cli import frontend_core as fc

            self._fc = fc
            if self._deprecated_context is not None:
                self.context = self._deprecated_context
            else:
                self.context = fc.FrontendCore(**self._context_kwargs)
            self.default_log_level = self.context._log_level
            asyncio.run(self.context.initialize_async())
        except BaseException as exc:
            self._init_error = exc
        finally:
            self._init_complete.set()

    def _raise_init_error(self) -> None:
        """Re-raise background initialization failures on the calling thread."""
        if self._init_error is not None:
            raise self._init_error

    def _ensure_initialized(self) -> None:
        """Wait for initialization to complete if not already done."""
        if not self._init_complete.is_set():
            print("Waiting for PyRIT initialization to complete...")
            sys.stdout.flush()
            self._init_complete.wait()
        self._raise_init_error()

    def cmdloop(self, intro: Optional[str] = None) -> None:
        """Override cmdloop to play animated banner before starting the REPL."""
        if intro is None:
            # Play animation immediately while background init continues.
            # Suppress logging during the animation so log lines don't corrupt
            # the ANSI cursor-positioned frames.
            prev_disable = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            try:
                intro = banner.play_animation(no_animation=self._no_animation)
            finally:
                logging.disable(prev_disable)

            # If init already failed while the animation played, surface it now.
            if self._init_complete.is_set():
                self._raise_init_error()
        elif self._init_complete.is_set():
            self._raise_init_error()
        self.intro = intro
        super().cmdloop(intro=self.intro)

    def do_list_scenarios(self, arg: str) -> None:
        """List all available scenarios."""
        self._ensure_initialized()
        try:
            asyncio.run(self._fc.print_scenarios_list_async(context=self.context))
        except Exception as e:
            print(f"Error listing scenarios: {e}")

    def do_list_initializers(self, arg: str) -> None:
        """List all available initializers."""
        self._ensure_initialized()
        try:
            asyncio.run(self._fc.print_initializers_list_async(context=self.context))
        except Exception as e:
            print(f"Error listing initializers: {e}")

    def do_list_targets(self, arg: str) -> None:
        """List all available targets from the TargetRegistry."""
        self._ensure_initialized()
        try:
            asyncio.run(self._fc.print_targets_list_async(context=self.context))
        except Exception as e:
            print(f"Error listing targets: {e}")

    def do_run(self, line: str) -> None:
        """
        Run a scenario.

        Usage:
            run <scenario_name> [options]

        Options:
            --target <name>                 Target name from the TargetRegistry (required)
            --initializers <name> ...       Built-in initializers (supports name:key=val1,val2 syntax)
            --initialization-scripts <...>  Custom Python scripts to run before the scenario
            --strategies, -s <s1> <s2> ...  Strategy names to use
            --max-concurrency <N>           Maximum concurrent operations
            --max-retries <N>               Maximum retry attempts
            --memory-labels <JSON>          JSON string of labels (e.g., '{"key":"value"}')
            --log-level <level>             Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

        Examples:
            run garak.encoding --target my_target --initializers target \
                load_default_datasets
            run garak.encoding --target my_target --initializers target \
                load_default_datasets --strategies base64 rot13
            run foundry.red_team_agent --target my_target --initializers target:tags=default,scorer \
                dataset:mode=strict --strategies base64
            run foundry.red_team_agent --target my_target --initializers target \
                load_default_datasets --max-concurrency 10 --max-retries 3
            run garak.encoding --target my_target --initializers target \
                load_default_datasets \
                --memory-labels '{"run_id":"test123","env":"dev"}'
            run foundry.red_team_agent --target my_target --initializers target \
                load_default_datasets -s jailbreak crescendo
            run garak.encoding --target my_target --initializers target \
                load_default_datasets --log-level DEBUG
            run foundry.red_team_agent --target my_target --initialization-scripts ./my_custom_init.py -s all

        Note:
            --target is required for every run.
            Initializers can be specified per-run or configured in .pyrit_conf.
            Database and env-files are configured via the config file.
        """
        self._ensure_initialized()

        if not line.strip():
            print("Error: Specify a scenario name")
            print("\nUsage: run <scenario_name> [options]")
            print("\nNote: --target is required. Initializers can be specified per-run or in .pyrit_conf.")
            print("\nOptions:")
            print(f"  --target <name>                 {self._fc.ARG_HELP['target']}")
            print(f"  --initializers <name> ...       {self._fc.ARG_HELP['initializers']}")
            print(
                f"  --initialization-scripts <...>  {self._fc.ARG_HELP['initialization_scripts']}"
                " (alternative to --initializers)"
            )
            print(f"  --strategies, -s <s1> <s2> ...  {self._fc.ARG_HELP['scenario_strategies']}")
            print(f"  --max-concurrency <N>           {self._fc.ARG_HELP['max_concurrency']}")
            print(f"  --max-retries <N>               {self._fc.ARG_HELP['max_retries']}")
            print(f"  --memory-labels <JSON>          {self._fc.ARG_HELP['memory_labels']}")
            print(
                "  --log-level <level>             Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
            )
            print("\nExample:")
            print("  run foundry.red_team_agent --target my_target --initializers target load_default_datasets")
            print("\nType 'help run' for more details and examples")
            return

        # Parse arguments using shared parser
        try:
            args = self._fc.parse_run_arguments(args_string=line)
        except ValueError as e:
            print(f"Error: {e}")
            return

        # Resolve initialization scripts if provided
        resolved_scripts = None
        if args["initialization_scripts"]:
            try:
                resolved_scripts = self._fc.resolve_initialization_scripts(script_paths=args["initialization_scripts"])
            except FileNotFoundError as e:
                print(f"Error: {e}")
                return

        # Create a context for this run with overrides
        run_context = self._fc.FrontendCore(
            initialization_scripts=resolved_scripts,
            initializer_names=args["initializers"],
            log_level=args["log_level"] if args["log_level"] else self.default_log_level,
        )
        # Use the existing registries (don't reinitialize)
        run_context._scenario_registry = self.context._scenario_registry
        run_context._initializer_registry = self.context._initializer_registry
        run_context._initialized = True

        try:
            result = asyncio.run(
                self._fc.run_scenario_async(
                    scenario_name=args["scenario_name"],
                    context=run_context,
                    target_name=args["target"],
                    scenario_strategies=args["scenario_strategies"],
                    max_concurrency=args["max_concurrency"],
                    max_retries=args["max_retries"],
                    memory_labels=args["memory_labels"],
                    dataset_names=args["dataset_names"],
                    max_dataset_size=args["max_dataset_size"],
                )
            )
            # Store the command and result in history
            self._scenario_history.append((line, result))
        except KeyboardInterrupt:
            print("\n\nScenario interrupted. Returning to shell.")
        except ValueError as e:
            print(f"Error: {e}")
        except Exception as e:
            print(f"Error running scenario: {e}")
            import traceback

            traceback.print_exc()

    def do_scenario_history(self, arg: str) -> None:
        """
        Display history of scenario runs.

        Usage:
            scenario-history

        Shows a numbered list of all scenario runs with the commands used.
        """
        if not self._scenario_history:
            print("No scenario runs in history.")
            return

        print("\nScenario Run History:")
        print("=" * 80)
        for idx, (command, _) in enumerate(self._scenario_history, start=1):
            print(f"{idx}) {command}")
        print("=" * 80)
        print(f"\nTotal runs: {len(self._scenario_history)}")
        print("\nUse 'print-scenario <number>' to view detailed results for a specific run.")
        print("Use 'print-scenario' to view detailed results for all runs.")

    def do_print_scenario(self, arg: str) -> None:
        """
        Print detailed results for scenario runs.

        Usage:
            print-scenario          Print all scenario results
            print-scenario <N>      Print results for scenario run number N

        Examples:
            print-scenario          Show all previous scenario results
            print-scenario 1        Show results from first scenario run
            print-scenario 3        Show results from third scenario run
        """
        if not self._scenario_history:
            print("No scenario runs in history.")
            return

        # Parse argument
        arg = arg.strip()

        if not arg:
            # Print all scenarios
            print("\nPrinting all scenario results:")
            print("=" * 80)
            for idx, (command, result) in enumerate(self._scenario_history, start=1):
                print(f"\n{'#' * 80}")
                print(f"Scenario Run #{idx}: {command}")
                print(f"{'#' * 80}")
                from pyrit.scenario.printer.console_printer import (
                    ConsoleScenarioResultPrinter,
                )

                printer = ConsoleScenarioResultPrinter()
                asyncio.run(printer.print_summary_async(result))
        else:
            # Print specific scenario
            try:
                scenario_num = int(arg)
                if scenario_num < 1 or scenario_num > len(self._scenario_history):
                    print(f"Error: Scenario number must be between 1 and {len(self._scenario_history)}")
                    return

                command, result = self._scenario_history[scenario_num - 1]
                print(f"\nScenario Run #{scenario_num}: {command}")
                print("=" * 80)
                from pyrit.scenario.printer.console_printer import (
                    ConsoleScenarioResultPrinter,
                )

                printer = ConsoleScenarioResultPrinter()
                asyncio.run(printer.print_summary_async(result))
            except ValueError:
                print(f"Error: Invalid scenario number '{arg}'. Must be an integer.")

    def do_help(self, arg: str) -> None:
        """Show help. Usage: help [command]."""
        if not arg:
            from pyrit.cli._cli_args import ARG_HELP

            # Show general help (no full init needed — ARG_HELP is lightweight)
            super().do_help(arg)
            print("\n" + "=" * 70)
            print("Shell Startup Options:")
            print("=" * 70)
            print("  --config-file <path>")
            print("      Path to YAML configuration file")
            print("      Default: ~/.pyrit/.pyrit_conf")
            print()
            print("  --log-level <level>")
            print("      Default logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL")
            print("      Default: WARNING")
            print("      Can be overridden per-run with 'run <scenario> --log-level <level>'")
            print()
            print("=" * 70)
            print("Run Command Options (specified when running scenarios):")
            print("=" * 70)
            print("  --target <name>  (REQUIRED)")
            print(f"      {ARG_HELP['target']}")
            print("      Example: run foundry.red_team_agent --target my_target")
            print("               --initializers target load_default_datasets")
            print()
            print("  --initializers <name> [<name> ...]")
            print(f"      {ARG_HELP['initializers']}")
            print("      Example: run foundry.red_team_agent --target my_target")
            print("               --initializers target load_default_datasets")
            print("      With params: run foundry.red_team_agent --target my_target")
            print("               --initializers target:tags=default,scorer")
            print("      Multiple with params: run foundry.red_team_agent --target my_target")
            print("               --initializers target:tags=default,scorer dataset:mode=strict")
            print()
            print("  --initialization-scripts <path> [<path> ...]  (Alternative to --initializers)")
            print(f"      {ARG_HELP['initialization_scripts']}")
            print("      Example: run foundry.red_team_agent --initialization-scripts ./my_init.py")
            print()
            print("  --strategies, -s <s1> [<s2> ...]")
            print(f"      {ARG_HELP['scenario_strategies']}")
            print("      Example: run garak.encoding --strategies base64 rot13")
            print()
            print("  --max-concurrency <N>")
            print(f"      {ARG_HELP['max_concurrency']}")
            print()
            print("  --max-retries <N>")
            print(f"      {ARG_HELP['max_retries']}")
            print()
            print("  --memory-labels <JSON>")
            print(f"      {ARG_HELP['memory_labels']}")
            print('      Example: run foundry.red_team_agent --memory-labels \'{"env":"test"}\'')
            print()
            print("  --log-level <level>             Override (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
            print()
            print("  Database and env-files are configured via the config file (--config-file).")
            print()
            print("Start the shell like:")
            print("  pyrit_shell")
            print("  pyrit_shell --config-file ./my_config.yaml --log-level DEBUG")
        else:
            # Show help for specific command
            super().do_help(arg)

    def do_exit(self, arg: str) -> bool:
        """
        Exit the shell. Aliases: quit, q.

        Returns:
            bool: True to exit the shell.
        """
        print("\nGoodbye!")
        return True

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        import os

        os.system("cls" if os.name == "nt" else "clear")

    # Shortcuts and aliases
    do_quit = do_exit
    do_q = do_exit
    do_EOF = do_exit  # Ctrl+D on Unix, Ctrl+Z on Windows  # noqa: N815

    def emptyline(self) -> bool:
        """
        Don't repeat last command on empty line.

        Returns:
            bool: False to prevent repeating the last command.
        """
        return False

    def default(self, line: str) -> None:
        """Handle unknown commands and convert hyphens to underscores."""
        # Try converting hyphens to underscores for command lookup
        parts = line.split(None, 1)
        if parts:
            cmd_with_underscores = parts[0].replace("-", "_")
            method_name = f"do_{cmd_with_underscores}"

            if hasattr(self, method_name):
                # Call the method with the rest of the line as argument
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

    from pyrit.cli._cli_args import ARG_HELP, validate_log_level

    parser = argparse.ArgumentParser(
        prog="pyrit_shell",
        description="PyRIT Interactive Shell - Load modules once, run commands instantly",
    )

    parser.add_argument(
        "--config-file",
        type=Path,
        help=ARG_HELP["config_file"],
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help=(
            "Default logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
            " (default: WARNING, can be overridden per-run)"
        ),
    )

    parser.add_argument(
        "--no-animation",
        action="store_true",
        default=False,
        help="Disable the animated startup banner (show static banner instead)",
    )

    args = parser.parse_args()

    # Play the banner immediately, before heavy imports.
    # Suppress logging so background-thread output doesn't corrupt the animation.
    prev_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        intro = banner.play_animation(no_animation=args.no_animation)
    finally:
        logging.disable(prev_disable)

    # Create shell with deferred initialization — the background thread
    # will import frontend_core, create the FrontendCore context, and call
    # initialize_async while the user is already at the prompt.
    try:
        shell = PyRITShell(
            no_animation=args.no_animation,
            config_file=args.config_file,
            log_level=validate_log_level(log_level=args.log_level),
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
