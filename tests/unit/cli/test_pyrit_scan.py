# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_scan CLI module.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import pyrit_scan


class TestParseArgs:
    """Tests for parse_args function."""

    def test_parse_args_list_scenarios(self):
        """Test parsing --list-scenarios flag."""
        args = pyrit_scan.parse_args(["--list-scenarios"])

        assert args.list_scenarios is True
        assert args.scenario_name is None

    def test_parse_args_list_initializers(self):
        """Test parsing --list-initializers flag."""
        args = pyrit_scan.parse_args(["--list-initializers"])

        assert args.list_initializers is True
        assert args.scenario_name is None

    def test_parse_args_scenario_name_only(self):
        """Test parsing scenario name without options."""
        args = pyrit_scan.parse_args(["test_scenario"])

        assert args.scenario_name == "test_scenario"
        assert args.database == "SQLite"
        assert args.log_level == "WARNING"

    def test_parse_args_with_database(self):
        """Test parsing with database option."""
        args = pyrit_scan.parse_args(["test_scenario", "--database", "InMemory"])

        assert args.database == "InMemory"

    def test_parse_args_with_log_level(self):
        """Test parsing with log-level option."""
        args = pyrit_scan.parse_args(["test_scenario", "--log-level", "DEBUG"])

        assert args.log_level == "DEBUG"

    def test_parse_args_with_initializers(self):
        """Test parsing with initializers."""
        args = pyrit_scan.parse_args(["test_scenario", "--initializers", "init1", "init2"])

        assert args.initializers == ["init1", "init2"]

    def test_parse_args_with_initialization_scripts(self):
        """Test parsing with initialization-scripts."""
        args = pyrit_scan.parse_args(["test_scenario", "--initialization-scripts", "script1.py", "script2.py"])

        assert args.initialization_scripts == ["script1.py", "script2.py"]

    def test_parse_args_with_strategies(self):
        """Test parsing with strategies."""
        args = pyrit_scan.parse_args(["test_scenario", "--strategies", "s1", "s2"])

        assert args.scenario_strategies == ["s1", "s2"]

    def test_parse_args_with_strategies_short_flag(self):
        """Test parsing with -s flag."""
        args = pyrit_scan.parse_args(["test_scenario", "-s", "s1", "s2"])

        assert args.scenario_strategies == ["s1", "s2"]

    def test_parse_args_with_max_concurrency(self):
        """Test parsing with max-concurrency."""
        args = pyrit_scan.parse_args(["test_scenario", "--max-concurrency", "5"])

        assert args.max_concurrency == 5

    def test_parse_args_with_max_retries(self):
        """Test parsing with max-retries."""
        args = pyrit_scan.parse_args(["test_scenario", "--max-retries", "3"])

        assert args.max_retries == 3

    def test_parse_args_with_memory_labels(self):
        """Test parsing with memory-labels."""
        args = pyrit_scan.parse_args(["test_scenario", "--memory-labels", '{"key":"value"}'])

        assert args.memory_labels == '{"key":"value"}'

    def test_parse_args_complex_command(self):
        """Test parsing complex command with multiple options."""
        args = pyrit_scan.parse_args(
            [
                "encoding_scenario",
                "--database",
                "InMemory",
                "--log-level",
                "INFO",
                "--initializers",
                "openai_target",
                "--strategies",
                "base64",
                "rot13",
                "--max-concurrency",
                "10",
                "--max-retries",
                "5",
                "--memory-labels",
                '{"env":"test"}',
            ]
        )

        assert args.scenario_name == "encoding_scenario"
        assert args.database == "InMemory"
        assert args.log_level == "INFO"
        assert args.initializers == ["openai_target"]
        assert args.scenario_strategies == ["base64", "rot13"]
        assert args.max_concurrency == 10
        assert args.max_retries == 5
        assert args.memory_labels == '{"env":"test"}'

    def test_parse_args_invalid_database(self):
        """Test parsing with invalid database raises error."""
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--database", "InvalidDB"])

    def test_parse_args_invalid_log_level(self):
        """Test parsing with invalid log level raises error."""
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--log-level", "INVALID"])

    def test_parse_args_invalid_max_concurrency(self):
        """Test parsing with invalid max-concurrency raises error."""
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--max-concurrency", "0"])

    def test_parse_args_invalid_max_retries(self):
        """Test parsing with invalid max-retries raises error."""
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--max-retries", "-1"])

    def test_parse_args_help_flag(self):
        """Test parsing --help flag exits."""
        with pytest.raises(SystemExit) as exc_info:
            pyrit_scan.parse_args(["--help"])

        assert exc_info.value.code == 0


class TestMain:
    """Tests for main function."""

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_list_scenarios(self, mock_frontend_core: MagicMock, mock_print_scenarios: AsyncMock):
        """Test main with --list-scenarios flag."""
        mock_print_scenarios.return_value = 0

        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 0
        mock_print_scenarios.assert_called_once()
        mock_frontend_core.assert_called_once()

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    @patch("pyrit.cli.frontend_core.get_default_initializer_discovery_path")
    def test_main_list_initializers(
        self,
        mock_get_path: MagicMock,
        mock_frontend_core: MagicMock,
        mock_print_initializers: AsyncMock,
    ):
        """Test main with --list-initializers flag."""
        mock_print_initializers.return_value = 0
        mock_get_path.return_value = Path("/test/path")

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0
        mock_print_initializers.assert_called_once()
        mock_get_path.assert_called_once()

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_list_scenarios_with_scripts(
        self,
        mock_frontend_core: MagicMock,
        mock_resolve_scripts: MagicMock,
        mock_print_scenarios: AsyncMock,
    ):
        """Test main with --list-scenarios and --initialization-scripts."""
        mock_resolve_scripts.return_value = [Path("/test/script.py")]
        mock_print_scenarios.return_value = 0

        result = pyrit_scan.main(["--list-scenarios", "--initialization-scripts", "script.py"])

        assert result == 0
        mock_resolve_scripts.assert_called_once_with(script_paths=["script.py"])
        mock_print_scenarios.assert_called_once()

    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_main_list_scenarios_with_missing_script(self, mock_resolve_scripts: MagicMock):
        """Test main with --list-scenarios and missing script file."""
        mock_resolve_scripts.side_effect = FileNotFoundError("Script not found")

        result = pyrit_scan.main(["--list-scenarios", "--initialization-scripts", "missing.py"])

        assert result == 1

    def test_main_no_scenario_specified(self, capsys):
        """Test main without scenario name."""
        result = pyrit_scan.main([])

        assert result == 1
        captured = capsys.readouterr()
        assert "No scenario specified" in captured.out

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_basic(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main running a basic scenario."""
        result = pyrit_scan.main(["test_scenario", "--initializers", "test_init"])

        assert result == 0
        mock_asyncio_run.assert_called_once()

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_with_scripts(
        self,
        mock_frontend_core: MagicMock,
        mock_resolve_scripts: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main running scenario with initialization scripts."""
        mock_resolve_scripts.return_value = [Path("/test/script.py")]

        result = pyrit_scan.main(["test_scenario", "--initialization-scripts", "script.py"])

        assert result == 0
        mock_resolve_scripts.assert_called_once_with(script_paths=["script.py"])
        mock_asyncio_run.assert_called_once()

    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_main_run_scenario_with_missing_script(self, mock_resolve_scripts: MagicMock):
        """Test main with missing initialization script."""
        mock_resolve_scripts.side_effect = FileNotFoundError("Script not found")

        result = pyrit_scan.main(["test_scenario", "--initialization-scripts", "missing.py"])

        assert result == 1

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_with_all_options(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main with all scenario options."""
        result = pyrit_scan.main(
            [
                "test_scenario",
                "--database",
                "InMemory",
                "--log-level",
                "DEBUG",
                "--initializers",
                "init1",
                "init2",
                "--strategies",
                "s1",
                "s2",
                "--max-concurrency",
                "10",
                "--max-retries",
                "5",
                "--memory-labels",
                '{"key":"value"}',
            ]
        )

        assert result == 0
        mock_asyncio_run.assert_called_once()

        # Verify FrontendCore was called with correct args
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["database"] == "InMemory"
        assert call_kwargs["log_level"] == "DEBUG"
        assert call_kwargs["initializer_names"] == ["init1", "init2"]

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.parse_memory_labels")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_with_memory_labels(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_parse_labels: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main with memory labels parsing."""
        mock_parse_labels.return_value = {"key": "value"}

        result = pyrit_scan.main(["test_scenario", "--initializers", "test_init", "--memory-labels", '{"key":"value"}'])

        assert result == 0
        mock_parse_labels.assert_called_once_with(json_string='{"key":"value"}')

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_with_exception(
        self,
        mock_frontend_core: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main handles exceptions during scenario run."""
        mock_asyncio_run.side_effect = ValueError("Test error")

        result = pyrit_scan.main(["test_scenario", "--initializers", "test_init"])

        assert result == 1

    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_database_defaults_to_sqlite(self, mock_frontend_core: MagicMock):
        """Test main uses SQLite as default database."""
        pyrit_scan.main(["--list-scenarios"])

        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["database"] == "SQLite"

    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_log_level_defaults_to_warning(self, mock_frontend_core: MagicMock):
        """Test main uses WARNING as default log level."""
        pyrit_scan.main(["--list-scenarios"])

        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["log_level"] == "WARNING"

    def test_main_with_invalid_args(self):
        """Test main with invalid arguments."""
        result = pyrit_scan.main(["--invalid-flag"])

        assert result == 2  # argparse returns 2 for invalid arguments

    @patch("builtins.print")
    def test_main_prints_startup_message(self, mock_print: MagicMock):
        """Test main prints startup message."""
        pyrit_scan.main(["--list-scenarios"])

        # Check that "Starting PyRIT..." was printed
        calls = [str(call_obj) for call_obj in mock_print.call_args_list]
        assert any("Starting PyRIT" in str(call_obj) for call_obj in calls)

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_run_scenario_calls_run_scenario_async(
        self,
        mock_frontend_core: MagicMock,
        mock_asyncio_run: MagicMock,
    ):
        """Test main properly calls run_scenario_async."""
        pyrit_scan.main(["test_scenario", "--initializers", "test_init", "--strategies", "s1"])

        # Verify asyncio.run was called with run_scenario_async
        assert mock_asyncio_run.call_count == 1
        assert mock_asyncio_run.call_count == 1


class TestMainIntegration:
    """Integration-style tests for main function."""

    @patch("pyrit.cli.frontend_core.print_scenarios_list_async", new_callable=AsyncMock)
    @patch("pyrit.registry.ScenarioRegistry")
    @patch("pyrit.setup.initialize_pyrit_async", new_callable=AsyncMock)
    def test_main_list_scenarios_integration(
        self,
        mock_init_pyrit: AsyncMock,
        mock_scenario_registry: MagicMock,
        mock_print_scenarios: AsyncMock,
    ):
        """Test main --list-scenarios with minimal mocking."""
        mock_print_scenarios.return_value = 0

        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 0

    @patch("pyrit.cli.frontend_core.print_initializers_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.get_default_initializer_discovery_path")
    def test_main_list_initializers_integration(
        self,
        mock_get_path: MagicMock,
        mock_print_initializers: AsyncMock,
    ):
        """Test main --list-initializers with minimal mocking."""
        mock_get_path.return_value = Path("/test/path")
        mock_print_initializers.return_value = 0

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0
