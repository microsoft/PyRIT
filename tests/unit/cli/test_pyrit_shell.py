# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_shell CLI module.
"""

import cmd
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import _banner as banner
from pyrit.cli import pyrit_shell


@pytest.fixture()
def mock_fc():
    """Patch FrontendCore so the background thread uses a controllable mock context."""
    mock_context = MagicMock()
    mock_context._database = "SQLite"
    mock_context._log_level = "WARNING"
    mock_context._env_files = None
    mock_context._scenario_registry = MagicMock()
    mock_context._initializer_registry = MagicMock()
    mock_context.initialize_async = AsyncMock()

    with patch("pyrit.cli.frontend_core.FrontendCore", return_value=mock_context) as mock_fc_class:
        yield mock_context, mock_fc_class


@pytest.fixture()
def shell():
    """Create a fully-initialized PyRITShell without spawning a background thread.

    Bypasses the real ``_background_init`` and wires up a mock FrontendCore
    directly, avoiding thread + asyncio.run overhead per test.
    """
    mock_context = MagicMock()
    mock_context._database = "SQLite"
    mock_context._log_level = "WARNING"
    mock_context._env_files = None
    mock_context._scenario_registry = MagicMock()
    mock_context._initializer_registry = MagicMock()
    mock_context.initialize_async = AsyncMock()

    with patch("pyrit.cli.frontend_core.FrontendCore", return_value=mock_context) as mock_fc_class:
        with patch.object(pyrit_shell.PyRITShell, "_background_init"):
            s = pyrit_shell.PyRITShell()
        # Manually set the state that _background_init would have set
        from pyrit.cli import frontend_core as fc_module

        s._fc = fc_module
        s.context = mock_context
        s.default_log_level = mock_context._log_level
        s._init_complete.set()
        yield s, mock_context, mock_fc_class


class TestPyRITShell:
    """Tests for PyRITShell class."""

    def test_init(self, mock_fc):
        """Test PyRITShell initialization."""
        ctx, mock_fc_class = mock_fc

        shell = pyrit_shell.PyRITShell()
        shell._init_thread.join(timeout=5)

        assert shell._init_complete.is_set()
        assert shell.context is ctx
        assert shell.default_log_level == "WARNING"
        assert shell._scenario_history == []
        mock_fc_class.assert_called_once_with()
        ctx.initialize_async.assert_called_once()

    def test_background_init_failure_sets_event_and_raises_in_ensure_initialized(self, mock_fc):
        """Test failed background initialization unblocks waiters and surfaces the original error."""
        ctx, _ = mock_fc
        ctx.initialize_async = AsyncMock(side_effect=RuntimeError("Initialization failed"))

        shell = pyrit_shell.PyRITShell()
        shell._init_thread.join(timeout=2)

        assert shell._init_complete.is_set()
        with pytest.raises(RuntimeError, match="Initialization failed"):
            shell._ensure_initialized()

    def test_deprecated_context_param_emits_warning(self, mock_fc):
        """Test that passing context= emits a DeprecationWarning and uses the provided context."""
        ctx, _ = mock_fc

        with pytest.warns(DeprecationWarning, match="context"):
            shell = pyrit_shell.PyRITShell(context=ctx)
        shell._init_thread.join(timeout=5)

        assert shell.context is ctx

    def test_context_with_kwargs_raises_value_error(self, mock_fc):
        """Test that passing both context and FrontendCore kwargs raises ValueError."""
        ctx, _ = mock_fc

        with pytest.raises(ValueError, match="Cannot pass 'context' together with"):
            pyrit_shell.PyRITShell(context=ctx, database="InMemory")

    def test_prompt_and_intro(self, shell):
        """Test shell prompt is set and cmdloop wires play_animation to intro."""
        s, ctx, _ = shell

        assert s.prompt == "pyrit> "

        # Verify that cmdloop calls play_animation and passes the result as intro
        with (
            patch("pyrit.cli._banner.play_animation", return_value="TEST_BANNER") as mock_play,
            patch("cmd.Cmd.cmdloop") as mock_cmdloop,
        ):
            s.cmdloop()

            mock_play.assert_called_once_with(no_animation=s._no_animation)
            mock_cmdloop.assert_called_once_with(intro="TEST_BANNER")

    def test_cmdloop_honors_explicit_intro(self, shell):
        """Test that cmdloop passes through a non-None intro without calling play_animation."""
        s, ctx, _ = shell

        with patch("pyrit.cli._banner.play_animation") as mock_play, patch("cmd.Cmd.cmdloop") as mock_cmdloop:
            s.cmdloop(intro="Custom intro")

            mock_play.assert_not_called()
            mock_cmdloop.assert_called_once_with(intro="Custom intro")

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    def test_do_list_scenarios(self, mock_print_scenarios: AsyncMock, shell):
        """Test do_list_scenarios command."""
        s, ctx, _ = shell

        s.do_list_scenarios("")

        mock_print_scenarios.assert_called_once_with(context=ctx)

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    def test_do_list_scenarios_with_exception(self, mock_print_scenarios: AsyncMock, shell, capsys):
        """Test do_list_scenarios handles exceptions."""
        s, ctx, _ = shell
        mock_print_scenarios.side_effect = ValueError("Test error")

        s.do_list_scenarios("")

        captured = capsys.readouterr()
        assert "Error listing scenarios" in captured.out

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    def test_do_list_initializers(self, mock_print_initializers: AsyncMock, shell):
        """Test do_list_initializers command."""
        s, ctx, _ = shell

        s.do_list_initializers("")

        mock_print_initializers.assert_called_once_with(context=ctx)

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    def test_do_list_initializers_with_exception(self, mock_print_initializers: AsyncMock, shell, capsys):
        """Test do_list_initializers handles exceptions."""
        s, ctx, _ = shell
        mock_print_initializers.side_effect = ValueError("Test error")

        s.do_list_initializers("")

        captured = capsys.readouterr()
        assert "Error listing initializers" in captured.out

    def test_do_run_empty_line(self, shell, capsys):
        """Test do_run with empty line."""
        s, ctx, _ = shell

        s.do_run("")

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
        shell,
    ):
        """Test do_run with basic scenario."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        mock_result = MagicMock()
        mock_asyncio_run.side_effect = [mock_result]

        s.do_run("test_scenario --initializers test_init")

        mock_parse_args.assert_called_once()
        assert mock_asyncio_run.call_count == 1

        # Verify result was stored in history
        assert len(s._scenario_history) == 1
        assert s._scenario_history[0][0] == "test_scenario --initializers test_init"
        assert s._scenario_history[0][1] == mock_result

    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_parse_error(self, mock_parse_args: MagicMock, shell, capsys):
        """Test do_run with parse error."""
        s, ctx, _ = shell
        mock_parse_args.side_effect = ValueError("Parse error")

        s.do_run("test_scenario --invalid")

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
        shell,
    ):
        """Test do_run with initialization scripts."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": None,
            "initialization_scripts": ["script.py"],
            "scenario_strategies": None,
            "max_concurrency": None,
            "max_retries": None,
            "memory_labels": None,
            "log_level": None,
            "dataset_names": None,
            "max_dataset_size": None,
            "target": None,
        }

        mock_resolve_scripts.return_value = [Path("/test/script.py")]
        mock_asyncio_run.side_effect = [MagicMock()]

        s.do_run("test_scenario --initialization-scripts script.py")

        mock_resolve_scripts.assert_called_once_with(script_paths=["script.py"])
        assert mock_asyncio_run.call_count == 1

    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_do_run_with_missing_script(
        self,
        mock_resolve_scripts: MagicMock,
        mock_parse_args: MagicMock,
        shell,
        capsys,
    ):
        """Test do_run with missing initialization script."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": None,
            "initialization_scripts": ["missing.py"],
            "scenario_strategies": None,
            "max_concurrency": None,
            "max_retries": None,
            "memory_labels": None,
            "log_level": None,
            "dataset_names": None,
            "max_dataset_size": None,
            "target": None,
        }

        mock_resolve_scripts.side_effect = FileNotFoundError("Script not found")

        s.do_run("test_scenario --initialization-scripts missing.py")

        captured = capsys.readouterr()
        assert "Error: Script not found" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_with_exception(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
        capsys,
    ):
        """Test do_run handles exceptions during scenario run."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        mock_asyncio_run.side_effect = [ValueError("Test error")]

        s.do_run("test_scenario --initializers test_init")

        captured = capsys.readouterr()
        assert "Error: Test error" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_do_run_keyboard_interrupt_returns_to_shell(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
        capsys,
    ):
        """Test that Ctrl+C during scenario run returns to shell instead of crashing."""
        s, ctx, _ = shell

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
            "target": None,
        }

        mock_asyncio_run.side_effect = KeyboardInterrupt()

        s.do_run("test_scenario --initializers test_init")

        captured = capsys.readouterr()
        assert "interrupted" in captured.out.lower()
        # Scenario should NOT be added to history
        assert len(s._scenario_history) == 0

    def test_do_scenario_history_empty(self, shell, capsys):
        """Test do_scenario_history with no history."""
        s, ctx, _ = shell

        s.do_scenario_history("")

        captured = capsys.readouterr()
        assert "No scenario runs in history" in captured.out

    def test_do_scenario_history_with_runs(self, shell, capsys):
        """Test do_scenario_history with scenario runs."""
        s, ctx, _ = shell

        s._scenario_history = [
            ("test_scenario1 --initializers init1", MagicMock()),
            ("test_scenario2 --initializers init2", MagicMock()),
        ]

        s.do_scenario_history("")

        captured = capsys.readouterr()
        assert "Scenario Run History" in captured.out
        assert "test_scenario1" in captured.out
        assert "test_scenario2" in captured.out
        assert "Total runs: 2" in captured.out

    def test_do_print_scenario_empty(self, shell, capsys):
        """Test do_print_scenario with no history."""
        s, ctx, _ = shell

        s.do_print_scenario("")

        captured = capsys.readouterr()
        assert "No scenario runs in history" in captured.out

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.scenario.printer.console_printer.ConsoleScenarioResultPrinter")
    def test_do_print_scenario_all(
        self,
        mock_printer_class: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
        capsys,
    ):
        """Test do_print_scenario without argument prints all."""
        s, ctx, _ = shell
        mock_printer = MagicMock()
        mock_printer_class.return_value = mock_printer

        s._scenario_history = [
            ("test_scenario1", MagicMock()),
            ("test_scenario2", MagicMock()),
        ]

        s.do_print_scenario("")

        captured = capsys.readouterr()
        assert "Printing all scenario results" in captured.out
        # 2 print calls (no background init)
        assert mock_asyncio_run.call_count == 2

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.scenario.printer.console_printer.ConsoleScenarioResultPrinter")
    def test_do_print_scenario_specific(
        self,
        mock_printer_class: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
        capsys,
    ):
        """Test do_print_scenario with specific scenario number."""
        s, ctx, _ = shell
        mock_printer = MagicMock()
        mock_printer_class.return_value = mock_printer

        s._scenario_history = [
            ("test_scenario1", MagicMock()),
            ("test_scenario2", MagicMock()),
        ]

        s.do_print_scenario("1")

        captured = capsys.readouterr()
        assert "Scenario Run #1" in captured.out
        # 1 print call (no background init)
        assert mock_asyncio_run.call_count == 1

    def test_do_print_scenario_invalid_number(self, shell, capsys):
        """Test do_print_scenario with invalid scenario number."""
        s, ctx, _ = shell

        s._scenario_history = [
            ("test_scenario1", MagicMock()),
        ]

        s.do_print_scenario("5")

        captured = capsys.readouterr()
        assert "must be between 1 and 1" in captured.out

    def test_do_print_scenario_non_integer(self, shell, capsys):
        """Test do_print_scenario with non-integer argument."""
        s, ctx, _ = shell

        s._scenario_history = [
            ("test_scenario1", MagicMock()),
        ]

        s.do_print_scenario("invalid")

        captured = capsys.readouterr()
        assert "Invalid scenario number" in captured.out

    def test_do_help_without_arg(self, shell, capsys):
        """Test do_help without argument."""
        s, ctx, _ = shell

        # Capture help output
        with patch("cmd.Cmd.do_help"):
            s.do_help("")
            captured = capsys.readouterr()
            assert "Shell Startup Options" in captured.out

    def test_do_help_with_arg(self, shell):
        """Test do_help with specific command."""
        s, ctx, _ = shell

        with patch("cmd.Cmd.do_help") as mock_parent_help:
            s.do_help("run")
            mock_parent_help.assert_called_with("run")

    @patch.object(cmd.Cmd, "cmdloop")
    @patch.object(banner, "play_animation")
    def test_cmdloop_sets_intro_via_play_animation(self, mock_play: MagicMock, mock_cmdloop: MagicMock, shell):
        """Test cmdloop wires banner.play_animation into intro and threads --no-animation."""
        s, ctx, _ = shell

        mock_play.return_value = "animated banner"

        # Note: no_animation is not set because shell fixture uses default
        s._no_animation = True
        s.cmdloop()

        mock_play.assert_called_once_with(no_animation=True)
        assert s.intro == "animated banner"
        mock_cmdloop.assert_called_once_with(intro="animated banner")

    @patch.object(cmd.Cmd, "cmdloop")
    def test_cmdloop_honors_explicit_intro(self, mock_cmdloop: MagicMock, shell):
        """Test cmdloop honors a non-None intro argument without calling play_animation."""
        s, ctx, _ = shell

        s.cmdloop(intro="custom intro")

        assert s.intro == "custom intro"
        mock_cmdloop.assert_called_once_with(intro="custom intro")

    def test_do_exit(self, shell, capsys):
        """Test do_exit command."""
        s, ctx, _ = shell

        result = s.do_exit("")

        assert result is True
        captured = capsys.readouterr()
        assert "Goodbye" in captured.out

    def test_do_quit_alias(self, shell):
        """Test do_quit is alias for do_exit."""
        s, ctx, _ = shell

        assert s.do_quit == s.do_exit

    def test_do_q_alias(self, shell):
        """Test do_q is alias for do_exit."""
        s, ctx, _ = shell

        assert s.do_q == s.do_exit

    def test_do_eof_alias(self, shell):
        """Test do_EOF is alias for do_exit."""
        s, ctx, _ = shell

        assert s.do_EOF == s.do_exit

    @patch("os.system")
    def test_do_clear_windows(self, mock_system: MagicMock, shell):
        """Test do_clear on Windows."""
        s, ctx, _ = shell

        with patch("os.name", "nt"):
            s.do_clear("")
            mock_system.assert_called_with("cls")

    @patch("os.system")
    def test_do_clear_unix(self, mock_system: MagicMock, shell):
        """Test do_clear on Unix."""
        s, ctx, _ = shell

        with patch("os.name", "posix"):
            s.do_clear("")
            mock_system.assert_called_with("clear")

    def test_emptyline(self, shell):
        """Test emptyline doesn't repeat last command."""
        s, ctx, _ = shell

        result = s.emptyline()

        assert result is False

    def test_default_with_hyphen_to_underscore(self, shell):
        """Test default converts hyphens to underscores."""
        s, ctx, _ = shell

        # Mock a method with underscores
        s.do_list_scenarios = MagicMock()

        s.default("list-scenarios")

        s.do_list_scenarios.assert_called_once_with("")

    def test_default_unknown_command(self, shell, capsys):
        """Test default with unknown command."""
        s, ctx, _ = shell

        s.default("unknown_command")

        captured = capsys.readouterr()
        assert "Unknown command" in captured.out


class TestMain:
    """Tests for main function."""

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_default_args(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main with default arguments."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_shell_class.call_args[1]
        assert call_kwargs["log_level"] == logging.WARNING
        mock_shell.cmdloop.assert_called_once()

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_with_config_file_arg(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main with config-file argument."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell", "--config-file", "my_config.yaml"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_shell_class.call_args[1]
        assert call_kwargs["config_file"] == Path("my_config.yaml")

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_with_log_level_arg(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main with log-level argument."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell", "--log-level", "DEBUG"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_shell_class.call_args[1]
        assert call_kwargs["log_level"] == logging.DEBUG

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_with_keyboard_interrupt(self, mock_play: MagicMock, mock_shell_class: MagicMock, capsys):
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
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_with_exception(self, mock_play: MagicMock, mock_shell_class: MagicMock, capsys):
        """Test main handles exceptions."""
        mock_shell = MagicMock()
        mock_shell.cmdloop.side_effect = ValueError("Test error")
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_creates_context_without_initializers(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main creates context without initializers."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            pyrit_shell.main()

        call_kwargs = mock_shell_class.call_args[1]
        # main() should not pass initialization_scripts or initializer_names
        assert "initialization_scripts" not in call_kwargs
        assert "initializer_names" not in call_kwargs

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_with_no_animation_flag(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main passes --no-animation flag to PyRITShell."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell", "--no-animation"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_shell_class.call_args[1]
        assert call_kwargs["no_animation"] is True

    @patch("pyrit.cli.pyrit_shell.PyRITShell")
    @patch("pyrit.cli._banner.play_animation", return_value="")
    def test_main_default_animation_enabled(self, mock_play: MagicMock, mock_shell_class: MagicMock):
        """Test main defaults to animation enabled (no_animation=False)."""
        mock_shell = MagicMock()
        mock_shell_class.return_value = mock_shell

        with patch("sys.argv", ["pyrit_shell"]):
            result = pyrit_shell.main()

        assert result == 0
        call_kwargs = mock_shell_class.call_args[1]
        assert call_kwargs["no_animation"] is False


class TestPyRITShellRunCommand:
    """Detailed tests for the run command."""

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_run_with_all_parameters(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
    ):
        """Test run command with all parameters."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["init1"],
            "initialization_scripts": None,
            "scenario_strategies": ["s1", "s2"],
            "max_concurrency": 10,
            "max_retries": 5,
            "memory_labels": {"key": "value"},
            "log_level": "DEBUG",
            "dataset_names": None,
            "max_dataset_size": None,
            "target": None,
        }

        mock_asyncio_run.side_effect = [MagicMock()]

        with patch("pyrit.cli.frontend_core.FrontendCore"), patch("pyrit.cli.frontend_core.run_scenario_async"):
            s.do_run("test_scenario --initializers init1 --strategies s1 s2 --max-concurrency 10")

            # Verify run_scenario_async was called with correct args
            # (it's called via asyncio.run, so check the mock_asyncio_run call)
            assert mock_asyncio_run.call_count == 1

    @patch("pyrit.cli.pyrit_shell.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_run_arguments")
    def test_run_stores_result_in_history(
        self,
        mock_parse_args: MagicMock,
        mock_asyncio_run: MagicMock,
        shell,
    ):
        """Test run command stores result in history."""
        s, ctx, _ = shell

        mock_parse_args.return_value = {
            "scenario_name": "test_scenario",
            "initializers": ["test_init"],
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

        mock_result1 = MagicMock()
        mock_result2 = MagicMock()
        mock_asyncio_run.side_effect = [mock_result1, mock_result2]

        # Run two scenarios
        s.do_run("scenario1 --initializers init1")
        s.do_run("scenario2 --initializers init2")

        # Verify both are in history
        assert len(s._scenario_history) == 2
        assert s._scenario_history[0][1] == mock_result1
        assert s._scenario_history[1][1] == mock_result2
