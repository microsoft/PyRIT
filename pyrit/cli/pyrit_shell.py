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
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models.scenario_result import ScenarioResult

from pyrit.cli import frontend_core


class PyRITShell(cmd.Cmd):
    """
    Interactive shell for PyRIT.

    Commands:
        list-scenarios             - List all available scenarios
        list-initializers          - List all available initializers
        run <scenario> [opts]      - Run a scenario with optional parameters
        scenario-history           - List all previous scenario runs
        print-scenario [N]         - Print detailed results for scenario run(s)
        help [command]             - Show help for a command
        clear                      - Clear the screen
        exit (quit, q)             - Exit the shell

    Shell Startup Options:
        --database <type>       Database type (InMemory, SQLite, AzureSQL) - default for all runs
        --log-level <level>     Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) - default for all runs
        --env-files <path> ...  Environment files to load in order - default for all runs

    Run Command Options:
        --initializers <name> ...       Built-in initializers to run before the scenario
        --initialization-scripts <...>  Custom Python scripts to run before the scenario
        --env-files <path> ...          Environment files to load in order (overrides startup default)
        --strategies, -s <s1> ...       Strategy names to use
        --max-concurrency <N>           Maximum concurrent operations
        --max-retries <N>               Maximum retry attempts
        --memory-labels <JSON>          JSON string of labels
        --database <type>               Override default database for this run
        --log-level <level>             Override default log level for this run
    """

    intro = """
╔══════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                              ║
║                       ██████╗ ██╗   ██╗██████╗ ██╗████████╗                                  ║
║                       ██╔══██╗╚██╗ ██╔╝██╔══██╗██║╚══██╔══╝                                  ║
║                       ██████╔╝ ╚████╔╝ ██████╔╝██║   ██║                                     ║
║                       ██╔═══╝   ╚██╔╝  ██╔══██╗██║   ██║                                     ║
║                       ██║        ██║   ██║  ██║██║   ██║                                     ║
║                       ╚═╝        ╚═╝   ╚═╝  ╚═╝╚═╝   ╚═╝                                     ║
║                                                                                              ║
║                          Python Risk Identification Tool                                     ║
║                                Interactive Shell                                             ║
║                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                              ║
║  Commands:                                                                                   ║
║    • list-scenarios        - See all available scenarios                                     ║
║    • list-initializers     - See all available initializers                                  ║
║    • run <scenario> [opts] - Execute a security scenario                                     ║
║    • scenario-history      - View your session history                                       ║
║    • print-scenario [N]    - Display detailed results                                        ║
║    • help [command]        - Get help on any command                                         ║
║    • exit                  - Quit the shell                                                  ║
║                                                                                              ║
║  Quick Start:                                                                                ║
║    pyrit> list-scenarios                                                                     ║
║    pyrit> run foundry --initializers openai_objective_target load_default_datasets           ║
║                                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════════════════════╝
"""
    prompt = "pyrit> "

    def __init__(
        self,
        context: frontend_core.FrontendCore,
    ):
        """
        Initialize the PyRIT shell.

        Args:
            context: PyRIT context with loaded registries.
        """
        super().__init__()
        self.context = context
        self.default_database = context._database
        self.default_log_level = context._log_level
        self.default_env_files = context._env_files

        # Track scenario execution history: list of (command_string, ScenarioResult) tuples
        self._scenario_history: list[tuple[str, ScenarioResult]] = []

        # Initialize PyRIT in background thread for faster startup
        self._init_thread = threading.Thread(target=self._background_init, daemon=True)
        self._init_complete = threading.Event()
        self._init_thread.start()

    def _background_init(self) -> None:
        """Initialize PyRIT modules in the background. This dramatically speeds up shell startup."""
        asyncio.run(self.context.initialize_async())
        self._init_complete.set()

    def _ensure_initialized(self) -> None:
        """Wait for initialization to complete if not already done."""
        if not self._init_complete.is_set():
            print("Waiting for PyRIT initialization to complete...")
            sys.stdout.flush()
            self._init_complete.wait()

    def do_list_scenarios(self, arg: str) -> None:
        """List all available scenarios."""
        self._ensure_initialized()
        try:
            asyncio.run(frontend_core.print_scenarios_list_async(context=self.context))
        except Exception as e:
            print(f"Error listing scenarios: {e}")

    def do_list_initializers(self, arg: str) -> None:
        """List all available initializers."""
        self._ensure_initialized()
        try:
            # Discover from scenarios directory by default (same as scan)
            discovery_path = frontend_core.get_default_initializer_discovery_path()
            asyncio.run(
                frontend_core.print_initializers_list_async(context=self.context, discovery_path=discovery_path)
            )
        except Exception as e:
            print(f"Error listing initializers: {e}")

    def do_run(self, line: str) -> None:
        """
        Run a scenario.

        Usage:
            run <scenario_name> [options]

        Options:
            --initializers <name> ...       Built-in initializers to run before the scenario
            --initialization-scripts <...>  Custom Python scripts to run before the scenario
            --env-files <path> ...          Environment files to load in order
            --strategies, -s <s1> <s2> ...  Strategy names to use
            --max-concurrency <N>           Maximum concurrent operations
            --max-retries <N>               Maximum retry attempts
            --memory-labels <JSON>          JSON string of labels (e.g., '{"key":"value"}')
            --database <type>               Override default database (InMemory, SQLite, AzureSQL)
            --log-level <level>             Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

        Examples:
            run garak.encoding --initializers openai_objective_target load_default_datasets
            run garak.encoding --initializers custom_target load_default_datasets --strategies base64 rot13
            run foundry --initializers openai_objective_target load_default_datasets --max-concurrency 10 --max-retries 3
            run garak.encoding --initializers custom_target load_default_datasets --memory-labels '{"run_id":"test123","env":"dev"}'
            run foundry --initializers openai_objective_target load_default_datasets -s jailbreak crescendo
            run garak.encoding --initializers openai_objective_target load_default_datasets --database InMemory --log-level DEBUG
            run foundry --initialization-scripts ./my_custom_init.py -s all

        Note:
            Every scenario requires an initializer (--initializers or --initialization-scripts).
            Database and log-level defaults are set at shell startup but can be overridden per-run.
            Initializers are specified per-run to allow different setups for different scenarios.
        """
        self._ensure_initialized()
        if not line.strip():
            print("Error: Specify a scenario name")
            print("\nUsage: run <scenario_name> [options]")
            print("\nNote: Every scenario requires an initializer.")
            print("\nOptions:")
            print(f"  --initializers <name> ...       {frontend_core.ARG_HELP['initializers']} (REQUIRED)")
            print(
                f"  --initialization-scripts <...>  {frontend_core.ARG_HELP['initialization_scripts']} (alternative to --initializers)"
            )
            print(f"  --strategies, -s <s1> <s2> ...  {frontend_core.ARG_HELP['scenario_strategies']}")
            print(f"  --max-concurrency <N>           {frontend_core.ARG_HELP['max_concurrency']}")
            print(f"  --max-retries <N>               {frontend_core.ARG_HELP['max_retries']}")
            print(f"  --memory-labels <JSON>          {frontend_core.ARG_HELP['memory_labels']}")
            print(
                f"  --database <type>               Override default database ({frontend_core.IN_MEMORY}, {frontend_core.SQLITE}, {frontend_core.AZURE_SQL})"
            )
            print(
                f"  --log-level <level>             Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
            )
            print("\nExample:")
            print("  run foundry --initializers openai_objective_target load_default_datasets")
            print("\nType 'help run' for more details and examples")
            return

        # Parse arguments using shared parser
        try:
            args = frontend_core.parse_run_arguments(args_string=line)
        except ValueError as e:
            print(f"Error: {e}")
            return

        # Resolve initialization scripts if provided
        resolved_scripts = None
        if args["initialization_scripts"]:
            try:
                resolved_scripts = frontend_core.resolve_initialization_scripts(
                    script_paths=args["initialization_scripts"]
                )
            except FileNotFoundError as e:
                print(f"Error: {e}")
                return

        # Resolve env files if provided
        resolved_env_files = None
        if args["env_files"]:
            try:
                resolved_env_files = frontend_core.resolve_env_files(env_file_paths=args["env_files"])
            except ValueError as e:
                print(f"Error: {e}")
                return
        else:
            # Use default env files from shell startup
            resolved_env_files = self.default_env_files

        # Create a context for this run with overrides
        run_context = frontend_core.FrontendCore(
            database=args["database"] or self.default_database,
            initialization_scripts=resolved_scripts,
            initializer_names=args["initializers"],
            env_files=resolved_env_files,
            log_level=args["log_level"] or self.default_log_level,
        )
        # Use the existing registries (don't reinitialize)
        run_context._scenario_registry = self.context._scenario_registry
        run_context._initializer_registry = self.context._initializer_registry
        run_context._initialized = True

        try:
            result = asyncio.run(
                frontend_core.run_scenario_async(
                    scenario_name=args["scenario_name"],
                    context=run_context,
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
            # Show general help
            super().do_help(arg)
            print("\n" + "=" * 70)
            print("Shell Startup Options:")
            print("=" * 70)
            print("  --database <type>")
            print("      Default database type: InMemory, SQLite, or AzureSQL")
            print("      Default: SQLite")
            print("      Can be overridden per-run with 'run <scenario> --database <type>'")
            print()
            print("  --log-level <level>")
            print("      Default logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL")
            print("      Default: WARNING")
            print("      Can be overridden per-run with 'run <scenario> --log-level <level>'")
            print()
            print("=" * 70)
            print("Run Command Options (specified when running scenarios):")
            print("=" * 70)
            print("  --initializers <name> [<name> ...]  (REQUIRED)")
            print(f"      {frontend_core.ARG_HELP['initializers']}")
            print("      Every scenario requires at least one initializer")
            print("      Example: run foundry --initializers openai_objective_target load_default_datasets")
            print()
            print("  --initialization-scripts <path> [<path> ...]  (Alternative to --initializers)")
            print(f"      {frontend_core.ARG_HELP['initialization_scripts']}")
            print("      Example: run foundry --initialization-scripts ./my_init.py")
            print()
            print("  --strategies, -s <s1> [<s2> ...]")
            print(f"      {frontend_core.ARG_HELP['scenario_strategies']}")
            print("      Example: run garak.encoding --strategies base64 rot13")
            print()
            print("  --max-concurrency <N>")
            print(f"      {frontend_core.ARG_HELP['max_concurrency']}")
            print()
            print("  --max-retries <N>")
            print(f"      {frontend_core.ARG_HELP['max_retries']}")
            print()
            print("  --memory-labels <JSON>")
            print(f"      {frontend_core.ARG_HELP['memory_labels']}")
            print('      Example: run foundry --memory-labels \'{"env":"test"}\'')
            print()
            print("Start the shell like:")
            print("  pyrit_shell")
            print("  pyrit_shell --database InMemory --log-level DEBUG")
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
    do_EOF = do_exit  # Ctrl+D on Unix, Ctrl+Z on Windows

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

    parser = argparse.ArgumentParser(
        prog="pyrit_shell",
        description="PyRIT Interactive Shell - Load modules once, run commands instantly",
    )

    parser.add_argument(
        "--database",
        choices=[frontend_core.IN_MEMORY, frontend_core.SQLITE, frontend_core.AZURE_SQL],
        default=frontend_core.SQLITE,
        help=f"Default database type to use ({frontend_core.IN_MEMORY}, {frontend_core.SQLITE}, {frontend_core.AZURE_SQL}) (default: {frontend_core.SQLITE}, can be overridden per-run)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Default logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING, can be overridden per-run)",
    )

    parser.add_argument(
        "--env-files",
        type=str,
        nargs="+",
        help="Environment files to load in order (default for all runs, can be overridden per-run)",
    )

    args = parser.parse_args()

    # Resolve env files if provided
    env_files = None
    if args.env_files:
        try:
            env_files = frontend_core.resolve_env_files(env_file_paths=args.env_files)
        except ValueError as e:
            print(f"Error: {e}")
            return 1

    # Create context (initializers are specified per-run, not at startup)
    context = frontend_core.FrontendCore(
        database=args.database,
        initialization_scripts=None,
        initializer_names=None,
        env_files=env_files,
        log_level=args.log_level,
    )

    # Start shell
    try:
        shell = PyRITShell(context)
        shell.cmdloop()
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
