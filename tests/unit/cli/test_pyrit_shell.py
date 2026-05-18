# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_shell CLI module (thin REST client).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import pyrit_shell


@pytest.fixture()
def mock_api_client():
    """Create a mock PyRITApiClient with default responses."""
    client = AsyncMock()
    client.health_check_async.return_value = True
    client.list_scenarios_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.list_initializers_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.list_targets_async.return_value = {"items": [], "pagination": {"total": 0}}
    client.list_scenario_runs_async.return_value = {"items": []}
    client.close_async = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.fixture()
def shell(mock_api_client):
    """Create a PyRITShell with a pre-wired mock API client."""
    s = pyrit_shell.PyRITShell(no_animation=True)
    s._api_client = mock_api_client
    s._base_url = "http://localhost:8000"
    return s, mock_api_client


class TestPyRITShell:
    """Tests for PyRITShell class."""

    def test_prompt(self, shell):
        s, _ = shell
        assert s.prompt == "pyrit> "

    def test_cmdloop_plays_animation(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with (
            patch("pyrit.cli._banner.play_animation", return_value="BANNER") as mock_play,
            patch("cmd.Cmd.cmdloop") as mock_cmdloop,
        ):
            s.cmdloop()
            mock_play.assert_called_once_with(no_animation=True)
            mock_cmdloop.assert_called_once_with(intro="BANNER")

    def test_cmdloop_honors_explicit_intro(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with (
            patch("pyrit.cli._banner.play_animation") as mock_play,
            patch("cmd.Cmd.cmdloop") as mock_cmdloop,
        ):
            s.cmdloop(intro="Custom intro")
            mock_play.assert_not_called()
            mock_cmdloop.assert_called_once_with(intro="Custom intro")

    def test_do_list_scenarios(self, shell):
        s, client = shell
        s.do_list_scenarios("")
        client.list_scenarios_async.assert_awaited_once()

    def test_do_list_scenarios_rejects_args(self, shell, capsys):
        s, _ = shell
        s.do_list_scenarios("--unknown foo")
        captured = capsys.readouterr()
        assert "does not accept arguments" in captured.out

    def test_do_list_initializers(self, shell):
        s, client = shell
        s.do_list_initializers("")
        client.list_initializers_async.assert_awaited_once()

    def test_do_list_initializers_rejects_args(self, shell, capsys):
        s, _ = shell
        s.do_list_initializers("--unknown foo")
        captured = capsys.readouterr()
        assert "does not accept arguments" in captured.out

    def test_do_list_targets(self, shell):
        s, client = shell
        s.do_list_targets("")
        client.list_targets_async.assert_awaited_once()

    def test_do_run_empty_args(self, shell, capsys):
        s, _ = shell
        s.do_run("")
        captured = capsys.readouterr()
        assert "Specify a scenario name" in captured.out

    def test_do_scenario_history_empty(self, shell, capsys):
        s, client = shell
        client.list_scenario_runs_async.return_value = {"items": []}
        s.do_scenario_history("")
        client.list_scenario_runs_async.assert_awaited_once()

    def test_do_scenario_history_rejects_args(self, shell, capsys):
        s, _ = shell
        s.do_scenario_history("extra")
        captured = capsys.readouterr()
        assert "does not accept arguments" in captured.out

    def test_do_print_scenario_no_args(self, shell, capsys):
        s, _ = shell
        s.do_print_scenario("")
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_do_exit(self, shell):
        s, client = shell
        result = s.do_exit("")
        assert result is True
        client.close_async.assert_awaited_once()

    def test_do_quit_alias(self, shell):
        s, _ = shell
        assert s.do_quit == s.do_exit

    def test_do_q_alias(self, shell):
        s, _ = shell
        assert s.do_q == s.do_exit

    def test_emptyline(self, shell):
        s, _ = shell
        assert s.emptyline() is False

    def test_default_unknown_command(self, shell, capsys):
        s, _ = shell
        s.default("unknown_command")
        captured = capsys.readouterr()
        assert "Unknown command" in captured.out

    def test_default_hyphen_to_underscore(self, shell):
        s, client = shell
        s.default("list-scenarios")
        client.list_scenarios_async.assert_awaited_once()

    def test_do_stop_server_no_launcher(self, shell, capsys):
        s, _ = shell
        with patch("pyrit.cli.pyrit_scan._stop_server_on_port", return_value=False):
            s.do_stop_server("")
        captured = capsys.readouterr()
        assert "No server found" in captured.out

    def test_ensure_client_already_connected(self, shell):
        s, _ = shell
        assert s._ensure_client() is True

    def test_ensure_client_no_server(self, capsys):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with patch(
            "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
            new_callable=AsyncMock,
        ) as mock_probe:
            mock_probe.return_value = False
            result = s._ensure_client()
        assert result is False
        captured = capsys.readouterr()
        assert "Server not available" in captured.out


class TestShellMain:
    """Tests for the shell main() entry point."""

    def test_main_parses_server_url(self):
        with (
            patch("pyrit.cli._banner.play_animation", return_value=""),
            patch("pyrit.cli.pyrit_shell.PyRITShell") as mock_shell_class,
        ):
            mock_shell = MagicMock()
            mock_shell_class.return_value = mock_shell

            with patch("sys.argv", ["pyrit_shell", "--server-url", "http://remote:9000", "--no-animation"]):
                pyrit_shell.main()

            mock_shell_class.assert_called_once()
            assert mock_shell_class.call_args.kwargs["server_url"] == "http://remote:9000"

    def test_main_keyboard_interrupt(self, capsys):
        with (
            patch("pyrit.cli._banner.play_animation", return_value=""),
            patch("pyrit.cli.pyrit_shell.PyRITShell") as mock_shell_class,
            patch("sys.argv", ["pyrit_shell", "--no-animation"]),
        ):
            mock_shell = MagicMock()
            mock_shell.cmdloop.side_effect = KeyboardInterrupt()
            mock_shell_class.return_value = mock_shell

            result = pyrit_shell.main()
            assert result == 0
