# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_shell CLI module.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pyrit.cli import pyrit_shell


class TestPyRITShell:
    """Tests for PyRITShell class."""

    def test_init(self):
        """Test PyRITShell initialization."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        assert shell.context == mock_context
        assert shell.default_database == "SQLite"
        assert shell.default_log_level == "WARNING"
        assert shell._scenario_history == []
        # Initialize is called in a background thread, so we need to wait for it
        shell._init_complete.wait(timeout=2)
        mock_context.initialize_async.assert_called_once()

    def test_prompt_and_intro(self):
        """Test shell prompt and intro are set."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        assert shell.prompt == "pyrit> "
        assert shell.intro is not None
        assert "Interactive Shell" in str(shell.intro)

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    def test_do_list_scenarios(self, mock_print_scenarios: AsyncMock):
        """Test do_list_scenarios command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_list_scenarios("")

        mock_print_scenarios.assert_called_once_with(context=mock_context)

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    def test_do_list_scenarios_with_exception(self, mock_print_scenarios: AsyncMock, capsys):
        """Test do_list_scenarios handles exceptions."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_print_scenarios.side_effect = ValueError("Test error")

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_list_scenarios("")

        captured = capsys.readouterr()
        assert "Error listing scenarios" in captured.out

    @patch("pyrit.cli.frontend_core.get_default_initializer_discovery_path")
    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    def test_do_list_initializers(self, mock_print_initializers: AsyncMock, mock_get_path: MagicMock):
        """Test do_list_initializers command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_path = Path("/test/path")
        mock_get_path.return_value = mock_path

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_list_initializers("")

        mock_print_initializers.assert_called_once_with(context=mock_context, discovery_path=mock_path)

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    def test_do_list_initializers_with_path(self, mock_print_initializers: AsyncMock):
        """Test do_list_initializers with custom path."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_list_initializers("/custom/path")

        assert mock_print_initializers.call_count == 1
        call_kwargs = mock_print_initializers.call_args[1]
        assert isinstance(call_kwargs["discovery_path"], Path)

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    def test_do_list_initializers_with_exception(self, mock_print_initializers: AsyncMock, capsys):
        """Test do_list_initializers handles exceptions."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_print_initializers.side_effect = ValueError("Test error")

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_list_initializers("")

        captured = capsys.readouterr()
        assert "Error listing initializers" in captured.out

    def test_do_run_empty_line(self, capsys):
        """Test do_run with empty line."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("")

        captured = capsys.readouterr()
        assert "Specify a scenario name" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_basic_scenario(
        self,
        mock_parse_args: MagicMock,
        _mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test do_run with basic scenario."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        mock_result = MagicMock()
        # First call is background init, second call is the actual test
        mock_asyncio_run.side_effect = [None, mock_result]

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("test_scenario --initializers test_init")

        mock_parse_args.assert_called_once()
        assert mock_asyncio_run.call_count == 2

        # Verify result was stored in history
        assert len(shell._scenario_history) == 1
        assert shell._scenario_history[0][0] == "test_scenario --initializers test_init"
        assert shell._scenario_history[0][1] == mock_result

    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_parse_error(self, mock_parse_args: MagicMock, capsys):
        """Test do_run with parse error."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_parse_args.side_effect = ValueError("Parse error")

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("test_scenario --invalid")

        captured = capsys.readouterr()
        assert "Error: Parse error" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_do_run_with_initialization_scripts(
        self,
        mock_resolve_scripts: MagicMock,
        mock_parse_args: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test do_run with initialization scripts."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": None,
            "initialization_scripts": ["script.py"],
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

        mock_resolve_scripts.return_value = [Path("/test/script.py")]
        # First call is background init, second call is the actual test
        mock_asyncio_run.side_effect = [None, MagicMock()]

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("test_scenario --initialization-scripts script.py")

        mock_resolve_scripts.assert_called_once_with(script_paths=["script.py"])
        assert mock_asyncio_run.call_count == 2

    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_do_run_with_missing_script(
        self,
        mock_resolve_scripts: MagicMock,
        mock_parse_args: MagicMock,
        capsys,
    ):
        """Test do_run with missing initialization script."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": None,
            "initialization_scripts": ["missing.py"],
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

        mock_resolve_scripts.side_effect = FileNotFoundError("Script not found")

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("test_scenario --initialization-scripts missing.py")

        captured = capsys.readouterr()
        assert "Error: Script not found" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_with_database_override(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test do_run with database override."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
            "initialization_scripts": None,
            "env_files": None,
            "scenario_strategies": None,
            "max_concurrency": None,
            "max_retries": None,
            "memory_labels": None,
            "database": "InMemory",
            "log_level": None,
            "dataset_names": None,
            "max_dataset_size": None,
        }

        # First call is background init, second call is the actual test
        mock_asyncio_run.side_effect = [None, MagicMock()]

        shell = pyrit_shell.PyRITShell(context=mock_context)

        with patch("pyrit.cli.frontend_core.FrontendCore") as mock_frontend:
            shell.do_run("test_scenario --initializers test_init --database InMemory")

            # Verify FrontendCore was created with overridden database
            call_kwargs = mock_frontend.call_args[1]
            assert call_kwargs["database"] == "InMemory"

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_with_exception(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
        capsys,
    ):
        """Test do_run handles exceptions during scenario run."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        # First call succeeds (background init), second call raises error (the actual test)
        mock_asyncio_run.side_effect = [None, ValueError("Test error")]

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_run("test_scenario --initializers test_init")

        captured = capsys.readouterr()
        assert "Error: Test error" in captured.out

    def test_do_scenario_history_empty(self, capsys):
        """Test do_scenario_history with no history."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_scenario_history("")

        captured = capsys.readouterr()
        assert "No scenario runs in history" in captured.out

    def test_do_scenario_history_with_runs(self, capsys):
        """Test do_scenario_history with scenario runs."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell._scenario_history = [
            ("test_scenario1 --initializers init1", MagicMock()),
            ("test_scenario2 --initializers init2", MagicMock()),
        ]

        shell.do_scenario_history("")

        captured = capsys.readouterr()
        assert "Scenario Run History" in captured.out
        assert "test_scenario1" in captured.out
        assert "test_scenario2" in captured.out
        assert "Total runs: 2" in captured.out

    def test_do_print_scenario_empty(self, capsys):
        """Test do_print_scenario with no history."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.do_print_scenario("")

        captured = capsys.readouterr()
        assert "No scenario runs in history" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.scenario.printer.console_printer.ConsoleScenarioResultPrinter")
    def test_do_print_scenario_all(
        self,
        mock_printer_class: MagicMock,
        mock_asyncio_run: MagicMock,
        capsys,
    ):
        """Test do_print_scenario without argument prints all."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_printer = MagicMock()
        mock_printer_class.return_value = mock_printer

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell._scenario_history = [
            ("test_scenario1", MagicMock()),
            ("test_scenario2", MagicMock()),
        ]

        shell.do_print_scenario("")

        captured = capsys.readouterr()
        assert "Printing all scenario results" in captured.out
        # 1 background init + 2 print calls
        assert mock_asyncio_run.call_count == 3

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.scenario.printer.console_printer.ConsoleScenarioResultPrinter")
    def test_do_print_scenario_specific(
        self,
        mock_printer_class: MagicMock,
        mock_asyncio_run: MagicMock,
        capsys,
    ):
        """Test do_print_scenario with specific scenario number."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()
        mock_printer = MagicMock()
        mock_printer_class.return_value = mock_printer

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell._scenario_history = [
            ("test_scenario1", MagicMock()),
            ("test_scenario2", MagicMock()),
        ]

        shell.do_print_scenario("1")

        captured = capsys.readouterr()
        assert "Scenario Run #1" in captured.out
        # 1 background init + 1 print call
        assert mock_asyncio_run.call_count == 2

    def test_do_print_scenario_invalid_number(self, capsys):
        """Test do_print_scenario with invalid scenario number."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell._scenario_history = [
            ("test_scenario1", MagicMock()),
        ]

        shell.do_print_scenario("5")

        captured = capsys.readouterr()
        assert "must be between 1 and 1" in captured.out

    def test_do_print_scenario_non_integer(self, capsys):
        """Test do_print_scenario with non-integer argument."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell._scenario_history = [
            ("test_scenario1", MagicMock()),
        ]

        shell.do_print_scenario("invalid")

        captured = capsys.readouterr()
        assert "Invalid scenario number" in captured.out

    def test_do_help_without_arg(self, capsys):
        """Test do_help without argument."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        # Capture help output
        with patch("cmd.Cmd.do_help"):
            shell.do_help("")
            captured = capsys.readouterr()
            assert "Shell Startup Options" in captured.out

    def test_do_help_with_arg(self):
        """Test do_help with specific command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        with patch("cmd.Cmd.do_help") as mock_parent_help:
            shell.do_help("run")
            mock_parent_help.assert_called_with("run")

    def test_do_exit(self, capsys):
        """Test do_exit command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        result = shell.do_exit("")

        assert result is True
        captured = capsys.readouterr()
        assert "Goodbye" in captured.out

    def test_do_quit_alias(self):
        """Test do_quit is alias for do_exit."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        assert shell.do_quit == shell.do_exit

    def test_do_q_alias(self):
        """Test do_q is alias for do_exit."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        assert shell.do_q == shell.do_exit

    def test_do_eof_alias(self):
        """Test do_EOF is alias for do_exit."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        assert shell.do_EOF == shell.do_exit

    @patch("os.system")
    def test_do_clear_windows(self, mock_system: MagicMock):
        """Test do_clear on Windows."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        with patch("os.name", "nt"):
            shell.do_clear("")
            mock_system.assert_called_with("cls")

    @patch("os.system")
    def test_do_clear_unix(self, mock_system: MagicMock):
        """Test do_clear on Unix."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        with patch("os.name", "posix"):
            shell.do_clear("")
            mock_system.assert_called_with("clear")

    def test_emptyline(self):
        """Test emptyline doesn't repeat last command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        result = shell.emptyline()

        assert result is False

    def test_default_with_hyphen_to_underscore(self):
        """Test default converts hyphens to underscores."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)

        # Mock a method with underscores
        shell.do_list_scenarios = MagicMock()

        shell.default("list-scenarios")

        shell.do_list_scenarios.assert_called_once_with("")

    def test_default_unknown_command(self, capsys):
        """Test default with unknown command."""
        mock_context = MagicMock()
        mock_context.initialize_async = AsyncMock()

        shell = pyrit_shell.PyRITShell(context=mock_context)
        shell.default("unknown_command")

        captured = capsys.readouterr()
        assert "Unknown command" in captured.out


class TestMain:
    """Tests for main function."""

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_default_args(self, mock_frontend_core: MagicMock, mock_shell_class: MagicMock):
        """Test main with default arguments."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 0
        mock_frontend_core.assert_called_once()
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["database"] == "SQLite"
        assert call_kwargs["log_level"] == "WARNING"
        mock_shell.cmdloop.assert_called_once()

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_with_database_arg(self, mock_frontend_core: MagicMock, mock_shell_class: MagicMock):
        """Test main with database argument."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell", "--database", "InMemory"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["database"] == "InMemory"

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_with_log_level_arg(self, mock_frontend_core: MagicMock, mock_shell_class: MagicMock):
        """Test main with log-level argument."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell", "--log-level", "DEBUG"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["log_level"] == "DEBUG"

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_with_keyboard_interrupt(self, mock_frontend_core: MagicMock, mock_shell_class: MagicMock, capsys):
        """Test main handles keyboard interrupt."""
        mock_shell = MagicMock()
        mock_shell.cmdloop.side_effect = KeyboardInterrupt()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 0
        captured = capsys.readouterr()
        assert "Interrupted" in captured.out

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_with_exception(self, mock_frontend_core: MagicMock, mock_shell_class: MagicMock, capsys):
        """Test main handles exceptions."""
        mock_shell = MagicMock()
        mock_shell.cmdloop.side_effect = ValueError("Test error")
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out

    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_creates_context_without_initializers(self, mock_frontend_core: MagicMock):
        """Test main creates context without initializers."""
        with patch("pyrit.cli.pyrit_shell.PyRITShell"), patch("sys.argv", ["pyrit_shell"]):
            pyrit_shell.main()

        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["initialization_scripts"] is None
        assert call_kwargs["initializer_names"] is None


class TestPyRITShellRunCommand:
    """Detailed tests for the run command."""

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_run_with_all_parameters(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test run command with all parameters."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["init1"],
            "initialization_scripts": None,
            "env_files": None,
            "scenario_strategies": ["s1", "s2"],
            "max_concurrency": 10,
            "max_retries": 5,
            "memory_labels": {"key": "value"},
            "database": "InMemory",
            "log_level": "DEBUG",
            "dataset_names": None,
            "max_dataset_size": None,
        }

        # First call is background init, second call is the actual test
        mock_asyncio_run.side_effect = [None, MagicMock()]

        shell = pyrit_shell.PyRITShell(context=mock_context)

        with patch("pyrit.cli.frontend_core.FrontendCore"), patch("pyrit.cli.frontend_core.run_scenario_async"):
            shell.do_run("test_scenario --initializers init1 --strategies s1 s2 --max-concurrency 10")

            # Verify run_scenario_async was called with correct args
            # (it's called via asyncio.run, so check the mock_asyncio_run call)
            assert mock_asyncio_run.call_count == 2

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_run_stores_result_in_history(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test run command stores result in history."""
        mock_context = MagicMock()
        mock_context._database = "SQLite"
        mock_context._log_level = "WARNING"
        mock_context._scenario_registry = MagicMock()
        mock_context._initializer_registry = MagicMock()
        mock_context.initialize_async = AsyncMock()

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        mock_result1 = MagicMock()
        mock_result2 = MagicMock()
        # First call is background init, then two actual test calls
        mock_asyncio_run.side_effect = [None, mock_result1, mock_result2]

        shell = pyrit_shell.PyRITShell(context=mock_context)

        # Run two scenarios
        shell.do_run("scenario1 --initializers init1")
        shell.do_run("scenario2 --initializers init2")

        # Verify both are in history
        assert len(shell._scenario_history) == 2
        assert shell._scenario_history[0][1] == mock_result1
        assert shell._scenario_history[1][1] == mock_result2
