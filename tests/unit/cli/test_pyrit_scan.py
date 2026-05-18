# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_scan CLI module (thin REST client).
"""

import logging
from argparse import Namespace
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.cli import pyrit_scan


class TestParseArgs:
    """Tests for parse_args function."""

    def test_parse_args_list_scenarios(self):
        args = pyrit_scan.parse_args(["--list-scenarios"])
        assert args.list_scenarios is True
        assert args.scenario_name is None

    def test_parse_args_list_initializers(self):
        args = pyrit_scan.parse_args(["--list-initializers"])
        assert args.list_initializers is True

    def test_parse_args_scenario_name_only(self):
        args = pyrit_scan.parse_args(["test_scenario"])
        assert args.scenario_name == "test_scenario"
        assert args.log_level == logging.WARNING

    def test_parse_args_with_log_level(self):
        args = pyrit_scan.parse_args(["test_scenario", "--log-level", "DEBUG"])
        assert args.log_level == logging.DEBUG

    def test_parse_args_with_initializers(self):
        args = pyrit_scan.parse_args(["test_scenario", "--initializers", "init1", "init2"])
        assert args.initializers == ["init1", "init2"]

    def test_parse_args_with_add_initializer(self):
        args = pyrit_scan.parse_args(["--add-initializer", "script1.py", "script2.py"])
        assert args.add_initializer == ["script1.py", "script2.py"]

    def test_parse_args_with_strategies(self):
        args = pyrit_scan.parse_args(["test_scenario", "--strategies", "s1", "s2"])
        assert args.scenario_strategies == ["s1", "s2"]

    def test_parse_args_with_strategies_short_flag(self):
        args = pyrit_scan.parse_args(["test_scenario", "-s", "s1", "s2"])
        assert args.scenario_strategies == ["s1", "s2"]

    def test_parse_args_with_max_concurrency(self):
        args = pyrit_scan.parse_args(["test_scenario", "--max-concurrency", "5"])
        assert args.max_concurrency == 5

    def test_parse_args_with_max_retries(self):
        args = pyrit_scan.parse_args(["test_scenario", "--max-retries", "3"])
        assert args.max_retries == 3

    def test_parse_args_with_memory_labels(self):
        args = pyrit_scan.parse_args(["test_scenario", "--memory-labels", '{"key":"value"}'])
        assert args.memory_labels == '{"key":"value"}'

    def test_parse_args_complex_command(self):
        args = pyrit_scan.parse_args(
            [
                "encoding_scenario",
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
        assert args.log_level == logging.INFO
        assert args.initializers == ["openai_target"]
        assert args.scenario_strategies == ["base64", "rot13"]
        assert args.max_concurrency == 10
        assert args.max_retries == 5

    def test_parse_args_invalid_log_level(self):
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--log-level", "INVALID"])

    def test_parse_args_invalid_max_concurrency(self):
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--max-concurrency", "0"])

    def test_parse_args_invalid_max_retries(self):
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--max-retries", "-1"])

    def test_parse_args_help_flag(self):
        with pytest.raises(SystemExit) as exc_info:
            pyrit_scan.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_parse_args_with_target(self):
        args = pyrit_scan.parse_args(["test_scenario", "--target", "my_target"])
        assert args.target == "my_target"

    def test_parse_args_target_default_is_none(self):
        args = pyrit_scan.parse_args(["test_scenario"])
        assert args.target is None

    def test_parse_args_with_list_targets(self):
        args = pyrit_scan.parse_args(["--list-targets"])
        assert args.list_targets is True

    def test_parse_args_with_server_url(self):
        args = pyrit_scan.parse_args(["--list-scenarios", "--server-url", "http://remote:9000"])
        assert args.server_url == "http://remote:9000"

    def test_parse_args_with_start_server(self):
        args = pyrit_scan.parse_args(["--list-scenarios", "--start-server"])
        assert args.start_server is True

    def test_parse_args_with_stop_server(self):
        args = pyrit_scan.parse_args(["--stop-server"])
        assert args.stop_server is True

    def test_main_with_invalid_args(self):
        result = pyrit_scan.main(["--invalid-flag"])
        assert result == 2


class TestExtractScenarioArgs:
    """Tests for the namespaced-dest extraction helper."""

    def test_no_scenario_keys_returns_empty(self):
        result = pyrit_scan._extract_scenario_args(parsed=Namespace(scenario_name="x", config_file=None, log_level=20))
        assert result == {}

    def test_scenario_keys_extracted_with_prefix_stripped(self):
        result = pyrit_scan._extract_scenario_args(
            parsed=Namespace(scenario_name="x", config_file=None, scenario__max_turns=10, scenario__mode="fast")
        )
        assert result == {"max_turns": 10, "mode": "fast"}


def _mock_api_client():
    """Create a mock PyRITApiClient with default response behaviors."""
    client = AsyncMock()
    client.health_check_async.return_value = True
    client.list_scenarios_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.list_initializers_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.list_targets_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.get_scenario_async.return_value = {
        "scenario_name": "test_scenario",
        "supported_parameters": [],
    }
    client.start_scenario_run_async.return_value = {
        "scenario_result_id": "test-id-123",
        "scenario_name": "test_scenario",
        "status": "CREATED",
    }
    client.get_scenario_run_async.return_value = {
        "scenario_result_id": "test-id-123",
        "status": "COMPLETED",
        "total_attacks": 5,
        "completed_attacks": 5,
        "objective_achieved_rate": 40,
    }
    client.get_scenario_run_results_async.return_value = {
        "run": {
            "scenario_result_id": "test-id-123",
            "scenario_name": "test_scenario",
            "status": "COMPLETED",
            "total_attacks": 5,
            "completed_attacks": 5,
            "objective_achieved_rate": 40,
        },
        "attacks": [],
    }
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestMain:
    """Tests for main function (thin REST client)."""

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_scenarios(self, mock_client_class, mock_probe):
        """Test main with --list-scenarios flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 0
        mock_client.list_scenarios_async.assert_awaited_once()

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_initializers(self, mock_client_class, mock_probe):
        """Test main with --list-initializers flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0
        mock_client.list_initializers_async.assert_awaited_once()

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_targets(self, mock_client_class, mock_probe):
        """Test main with --list-targets flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-targets"])

        assert result == 0
        mock_client.list_targets_async.assert_awaited_once()

    def test_main_no_args_shows_help(self):
        """Test main with no arguments shows help."""
        result = pyrit_scan.main([])
        assert result == 0  # shows help and exits

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_run_scenario(self, mock_client_class, mock_probe):
        """Test main running a scenario."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "my_target"])

        assert result == 0
        mock_client.get_scenario_async.assert_awaited_once()
        mock_client.start_scenario_run_async.assert_awaited_once()

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_run_scenario_with_initializers(self, mock_client_class, mock_probe):
        """Test main maps --initializers to request format."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t", "--initializers", "target", "datasets"])

        assert result == 0
        call_kwargs = mock_client.start_scenario_run_async.call_args.kwargs
        request = call_kwargs["request"]
        assert request["initializers"] == ["target", "datasets"]

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=False)
    def test_main_server_not_available(self, mock_probe, capsys):
        """Test main when server is not available."""
        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Server not available" in captured.out

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=False)
    def test_main_stop_server(self, mock_probe, capsys):
        """Test main with --stop-server."""
        result = pyrit_scan.main(["--stop-server"])

        assert result == 0
        captured = capsys.readouterr()
        assert "No server running" in captured.out

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_scenario_not_found(self, mock_client_class, mock_probe, capsys):
        """Test main when scenario is not found on server."""
        mock_client = _mock_api_client()
        mock_client.get_scenario_async.return_value = None
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["nonexistent_scenario", "--target", "t"])

        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_failed_scenario(self, mock_client_class, mock_probe):
        """Test main when scenario run fails."""
        mock_client = _mock_api_client()
        mock_client.get_scenario_run_async.return_value = {
            "scenario_result_id": "test-id",
            "status": "FAILED",
            "total_attacks": 0,
            "completed_attacks": 0,
            "objective_achieved_rate": 0,
            "error": "Something went wrong",
        }
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t"])

        assert result == 1
