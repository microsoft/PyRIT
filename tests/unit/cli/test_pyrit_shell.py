# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_shell CLI module (thin REST client).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import pyrit_shell


@pytest.fixture()
def mock_api_client():
    """Create a mock PyRITApiClient with default responses (typed wire-data)."""
    from datetime import datetime, timezone

    from pyrit.models.catalog import RegisteredScenario

    client = AsyncMock()
    client.health_check_async.return_value = True
    client.list_scenarios_async.return_value = []
    client.list_initializers_async.return_value = []
    client.list_targets_async.return_value = []
    client.list_scenario_runs_async.return_value = []
    # Default: scenario fetch returns a typed RegisteredScenario with no declared params.
    client.get_scenario_async.return_value = RegisteredScenario(
        scenario_name="foo",
        scenario_type="X",
        description="",
        default_strategy="",
        aggregate_strategies=[],
        all_strategies=[],
        default_datasets=[],
        max_dataset_size=None,
        supported_parameters=[],
    )
    client.close_async = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    # Helpers for tests to override the default scenario metadata.
    client._make_typed_scenario = lambda **kw: RegisteredScenario(
        scenario_name=kw.get("scenario_name", "foo"),
        scenario_type=kw.get("scenario_type", "X"),
        description=kw.get("description", ""),
        default_strategy=kw.get("default_strategy", ""),
        aggregate_strategies=kw.get("aggregate_strategies", []),
        all_strategies=kw.get("all_strategies", []),
        default_datasets=kw.get("default_datasets", []),
        max_dataset_size=kw.get("max_dataset_size", None),
        supported_parameters=kw.get("supported_parameters", []),
    )
    # Suppress unused-import warning for datetime/timezone helpers used by tests.
    _ = (datetime, timezone)
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

    def test_do_scenario_history_default_limit(self, shell):
        s, client = shell
        client.list_scenario_runs_async.return_value = []
        s.do_scenario_history("")
        client.list_scenario_runs_async.assert_awaited_once_with(limit=10)

    def test_do_scenario_history_accepts_numeric_limit(self, shell):
        s, client = shell
        client.list_scenario_runs_async.return_value = []
        s.do_scenario_history("3")
        client.list_scenario_runs_async.assert_awaited_once_with(limit=3)

    def test_do_scenario_history_rejects_non_integer(self, shell, capsys):
        s, _ = shell
        s.do_scenario_history("extra")
        captured = capsys.readouterr()
        assert "Usage: scenario-history" in captured.out

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
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("pyrit.cli._server_launcher.stop_server_on_port", return_value=False),
        ):
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


class TestShellRunAsyncTimeout:
    """Regression: _run_async must time out instead of hanging on a stuck coroutine."""

    def test_run_async_raises_timeout_error(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        try:

            async def hangs():
                # Block on an Event that's never set so the coroutine truly
                # cannot complete on its own; the timeout under test must cut it off.
                await asyncio.Event().wait()

            with pytest.raises(TimeoutError, match="did not complete"):
                s._run_async(hangs(), timeout=0.05)
        finally:
            s._shutdown_loop()

    def test_run_async_returns_value_within_timeout(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        try:

            async def quick():
                return 42

            assert s._run_async(quick(), timeout=5) == 42
        finally:
            s._shutdown_loop()


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

    def test_main_generic_exception(self, capsys):
        with (
            patch("pyrit.cli._banner.play_animation", return_value=""),
            patch("pyrit.cli.pyrit_shell.PyRITShell") as mock_shell_class,
            patch("sys.argv", ["pyrit_shell", "--no-animation"]),
        ):
            mock_shell = MagicMock()
            mock_shell.cmdloop.side_effect = RuntimeError("boom")
            mock_shell_class.return_value = mock_shell

            result = pyrit_shell.main()
            assert result == 1
            captured = capsys.readouterr()
            assert "boom" in captured.out

    def test_main_log_level_and_config_file(self, tmp_path):
        with (
            patch("pyrit.cli._banner.play_animation", return_value=""),
            patch("pyrit.cli.pyrit_shell.PyRITShell") as mock_shell_class,
            patch(
                "sys.argv",
                [
                    "pyrit_shell",
                    "--no-animation",
                    "--log-level",
                    "DEBUG",
                    "--config-file",
                    str(tmp_path / "conf.yaml"),
                    "--start-server",
                ],
            ),
        ):
            mock_shell = MagicMock()
            mock_shell_class.return_value = mock_shell
            assert pyrit_shell.main() == 0
            kwargs = mock_shell_class.call_args.kwargs
            assert kwargs["start_server"] is True
            assert kwargs["config_file"] == tmp_path / "conf.yaml"


class TestResolveBaseUrl:
    def test_explicit_server_url_wins(self):
        s = pyrit_shell.PyRITShell(no_animation=True, server_url="http://custom:1234")
        assert s._resolve_base_url() == "http://custom:1234"

    def test_falls_back_to_config_reader(self, tmp_path):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with patch("pyrit.cli._config_reader.read_server_url", return_value="http://from-cfg:8000"):
            assert s._resolve_base_url() == "http://from-cfg:8000"

    def test_default_when_config_returns_none(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with patch("pyrit.cli._config_reader.read_server_url", return_value=None):
            from pyrit.cli._config_reader import DEFAULT_SERVER_URL

            assert s._resolve_base_url() == DEFAULT_SERVER_URL


class TestEnsureClientStartServer:
    def test_start_server_launches_when_not_running(self):
        s = pyrit_shell.PyRITShell(no_animation=True, start_server=True)
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("pyrit.cli._server_launcher.ServerLauncher.start_async", new_callable=AsyncMock) as mock_start,
            patch("pyrit.cli.api_client.PyRITApiClient") as mock_client_class,
        ):
            mock_start.return_value = "http://localhost:8000"
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value = mock_client
            assert s._ensure_client() is True
            assert s._api_client is mock_client
            assert s._start_server is False  # only auto-start once

    def test_start_server_failure_returns_false(self, capsys):
        s = pyrit_shell.PyRITShell(no_animation=True, start_server=True)
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.start_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("nope"),
            ),
        ):
            assert s._ensure_client() is False
            assert "Error starting server: nope" in capsys.readouterr().out


class TestDoAddInitializer:
    def test_no_args_prints_usage(self, shell, capsys):
        s, _ = shell
        s.do_add_initializer("")
        assert "Usage" in capsys.readouterr().out

    def test_file_not_found(self, shell, capsys):
        s, _ = shell
        s.do_add_initializer("/nonexistent/_xyz_not_a_file.py")
        assert "File not found" in capsys.readouterr().out

    def test_success_path(self, shell, tmp_path, capsys):
        s, client = shell
        script = tmp_path / "my_init.py"
        script.write_text("def init(): pass")
        client.register_initializer_async = AsyncMock(return_value={"status": "ok"})
        s.do_add_initializer(str(script))
        assert "Registered initializer 'my_init'" in capsys.readouterr().out
        client.register_initializer_async.assert_awaited_once()

    def test_success_with_quoted_path_containing_spaces(self, shell, tmp_path, capsys):
        s, client = shell
        script_dir = tmp_path / "initializer scripts"
        script_dir.mkdir()
        script = script_dir / "my_init.py"
        script.write_text("def init(): pass")
        client.register_initializer_async = AsyncMock(return_value={"status": "ok"})

        s.do_add_initializer(f'"{script}"')

        assert "Registered initializer 'my_init'" in capsys.readouterr().out
        client.register_initializer_async.assert_awaited_once_with(name="my_init", script_content="def init(): pass")

    def test_malformed_path_quote(self, shell, capsys):
        s, client = shell
        client.register_initializer_async = AsyncMock(return_value={"status": "ok"})

        s.do_add_initializer('"unterminated')

        assert "Error parsing initializer paths" in capsys.readouterr().out
        client.register_initializer_async.assert_not_called()

    def test_success_with_multiple_quoted_paths(self, shell, tmp_path, capsys):
        s, client = shell
        script_dir = tmp_path / "initializer scripts"
        script_dir.mkdir()
        first = script_dir / "first_init.py"
        second = script_dir / "second_init.py"
        first.write_text("def init(): pass")
        second.write_text("def init(): pass")
        client.register_initializer_async = AsyncMock(return_value={"status": "ok"})

        s.do_add_initializer(f'"{first}" "{second}"')

        out = capsys.readouterr().out
        assert "Registered initializer 'first_init'" in out
        assert "Registered initializer 'second_init'" in out
        assert client.register_initializer_async.await_count == 2

    def test_server_not_available_error(self, shell, tmp_path, capsys):
        from pyrit.cli.api_client import ServerNotAvailableError

        s, client = shell
        script = tmp_path / "init.py"
        script.write_text("x = 1")
        client.register_initializer_async = AsyncMock(side_effect=ServerNotAvailableError("server gone"))
        s.do_add_initializer(str(script))
        assert "server gone" in capsys.readouterr().out

    def test_generic_error(self, shell, tmp_path, capsys):
        s, client = shell
        script = tmp_path / "init.py"
        script.write_text("x = 1")
        client.register_initializer_async = AsyncMock(side_effect=RuntimeError("boom"))
        s.do_add_initializer(str(script))
        assert "Error registering initializer: boom" in capsys.readouterr().out


class TestDoRun:
    @staticmethod
    def _run_payload(status="COMPLETED"):
        """Build a typed ScenarioRunSummary for use as a mock return value."""
        from datetime import datetime, timezone

        from pyrit.models import ScenarioRunState
        from pyrit.models.catalog import ScenarioRunSummary

        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        return ScenarioRunSummary(
            scenario_result_id="rid-1",
            scenario_name="foo",
            scenario_version=0,
            status=ScenarioRunState(status),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _empty_scenario_result():
        """Build a minimal ScenarioResult for use as get_scenario_run_results_async return."""
        from pyrit.models import ScenarioIdentifier, ScenarioResult, ScenarioRunState

        return ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name="foo"),
            objective_target_identifier=None,
            objective_scorer_identifier=None,
            attack_results={},
            scenario_run_state=ScenarioRunState.COMPLETED,
        )

    def test_run_invalid_arguments(self, shell, capsys):
        s, _ = shell
        with patch("pyrit.cli._cli_args.parse_run_arguments", side_effect=ValueError("bad")):
            s.do_run("foo --target t")
        assert "Error: bad" in capsys.readouterr().out

    def test_run_start_failure(self, shell, capsys):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(side_effect=RuntimeError("nope"))
        with patch(
            "pyrit.cli._cli_args.parse_run_arguments",
            return_value={"scenario_name": "foo", "target": "t"},
        ):
            s.do_run("foo --target t")
        assert "Error starting scenario: nope" in capsys.readouterr().out

    def test_run_completed_path_with_results(self, shell, capsys):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(return_value=self._run_payload())
        client.get_scenario_run_async = AsyncMock(return_value=self._run_payload("COMPLETED"))
        client.get_scenario_run_results_async = AsyncMock(return_value=self._empty_scenario_result())
        with (
            patch(
                "pyrit.cli._cli_args.parse_run_arguments",
                return_value={
                    "scenario_name": "foo",
                    "target": "t",
                    "initializers": ["a", {"name": "b", "args": {"x": 1}}],
                    "scenario_strategies": ["s1"],
                    "max_concurrency": 2,
                    "max_retries": 3,
                    "memory_labels": {"k": "v"},
                    "dataset_names": ["d1"],
                    "max_dataset_size": 5,
                },
            ),
            patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("pyrit.cli._output.print_scenario_run_summary"),
            patch("time.sleep"),
        ):
            s.do_run("foo --target t")
        sent = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent.initializers == ["a", "b"]
        assert sent.initializer_args == {"b": {"x": 1}}
        assert sent.strategies == ["s1"]
        assert sent.max_concurrency == 2
        assert sent.max_retries == 3
        assert sent.labels == {"k": "v"}
        assert sent.dataset_names == ["d1"]
        assert sent.max_dataset_size == 5

    def test_run_failed_status_calls_summary(self, shell):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(return_value=self._run_payload())
        client.get_scenario_run_async = AsyncMock(return_value=self._run_payload("FAILED"))
        with (
            patch(
                "pyrit.cli._cli_args.parse_run_arguments",
                return_value={"scenario_name": "foo", "target": "t"},
            ),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("pyrit.cli._output.print_scenario_run_summary") as mock_summary,
            patch("time.sleep"),
        ):
            s.do_run("foo --target t")
        mock_summary.assert_called_once()

    def test_run_completed_fallback_to_summary_on_results_error(self, shell):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(return_value=self._run_payload())
        client.get_scenario_run_async = AsyncMock(return_value=self._run_payload("COMPLETED"))
        client.get_scenario_run_results_async = AsyncMock(side_effect=RuntimeError("nope"))
        with (
            patch(
                "pyrit.cli._cli_args.parse_run_arguments",
                return_value={"scenario_name": "foo", "target": "t"},
            ),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("pyrit.cli._output.print_scenario_run_summary") as mock_summary,
            patch("time.sleep"),
        ):
            s.do_run("foo --target t")
        mock_summary.assert_called_once()

    def test_run_keyboard_interrupt_cancels(self, shell, capsys):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(return_value=self._run_payload())
        # Use MagicMock so KeyboardInterrupt raises synchronously on call —
        # this simulates Ctrl+C arriving between polling iterations, matching
        # how signals are delivered to the shell's main thread in production.
        client.get_scenario_run_async = MagicMock(side_effect=KeyboardInterrupt)
        client.cancel_scenario_run_async = AsyncMock(return_value=None)
        with (
            patch(
                "pyrit.cli._cli_args.parse_run_arguments",
                return_value={"scenario_name": "foo", "target": "t"},
            ),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("time.sleep"),
        ):
            s.do_run("foo --target t")
        client.cancel_scenario_run_async.assert_awaited_once()
        assert "cancelled" in capsys.readouterr().out.lower()

    def test_run_keyboard_interrupt_cancel_fails_warns(self, shell, capsys):
        s, client = shell
        client.start_scenario_run_async = AsyncMock(return_value=self._run_payload())
        client.get_scenario_run_async = MagicMock(side_effect=KeyboardInterrupt)
        client.cancel_scenario_run_async = AsyncMock(side_effect=RuntimeError("offline"))
        with (
            patch(
                "pyrit.cli._cli_args.parse_run_arguments",
                return_value={"scenario_name": "foo", "target": "t"},
            ),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("time.sleep"),
        ):
            s.do_run("foo --target t")
        assert "could not cancel" in capsys.readouterr().out.lower()


class TestListErrors:
    def test_list_scenarios_error(self, shell, capsys):
        s, client = shell
        client.list_scenarios_async = AsyncMock(side_effect=RuntimeError("x"))
        s.do_list_scenarios("")
        assert "Error listing scenarios" in capsys.readouterr().out

    def test_list_initializers_error(self, shell, capsys):
        s, client = shell
        client.list_initializers_async = AsyncMock(side_effect=RuntimeError("x"))
        s.do_list_initializers("")
        assert "Error listing initializers" in capsys.readouterr().out

    def test_list_targets_error(self, shell, capsys):
        s, client = shell
        client.list_targets_async = AsyncMock(side_effect=RuntimeError("x"))
        s.do_list_targets("")
        assert "Error listing targets" in capsys.readouterr().out

    def test_scenario_history_error(self, shell, capsys):
        s, client = shell
        client.list_scenario_runs_async = AsyncMock(side_effect=RuntimeError("x"))
        s.do_scenario_history("")
        assert "Error" in capsys.readouterr().out


class TestPrintScenarioAndHelp:
    def test_print_scenario_success(self, shell):
        from pyrit.models import ScenarioIdentifier, ScenarioResult, ScenarioRunState

        s, client = shell
        empty_result = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name="foo"),
            objective_target_identifier=None,
            objective_scorer_identifier=None,
            attack_results={},
            scenario_run_state=ScenarioRunState.COMPLETED,
        )
        client.get_scenario_run_results_async = AsyncMock(return_value=empty_result)
        with patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock) as mock_print:
            s.do_print_scenario("rid-1")
        mock_print.assert_awaited_once()

    def test_print_scenario_error(self, shell, capsys):
        s, client = shell
        client.get_scenario_run_results_async = AsyncMock(side_effect=RuntimeError("oops"))
        s.do_print_scenario("rid-1")
        assert "Error: oops" in capsys.readouterr().out

    def test_do_help_with_arg_normalizes_hyphen(self, shell):
        s, _ = shell
        with patch("cmd.Cmd.do_help") as mock_help:
            s.do_help("list-scenarios")
        mock_help.assert_called_once_with("list_scenarios")

    def test_do_help_no_arg(self, shell, capsys):
        s, _ = shell
        with patch("cmd.Cmd.do_help"):
            s.do_help("")
        assert "Use 'help <command>'" in capsys.readouterr().out


class TestServerManagement:
    def test_start_server_already_running(self, capsys):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("pyrit.cli.api_client.PyRITApiClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value = mock_client
            s.do_start_server("")
        assert "already running" in capsys.readouterr().out
        assert s._api_client is mock_client

    def test_start_server_launch_success(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("pyrit.cli._server_launcher.ServerLauncher.start_async", new_callable=AsyncMock) as mock_start,
            patch("pyrit.cli.api_client.PyRITApiClient") as mock_client_class,
        ):
            mock_start.return_value = "http://localhost:8000"
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value = mock_client
            s.do_start_server("")
        assert s._base_url == "http://localhost:8000"

    def test_start_server_launch_replaces_existing_client(self):
        s = pyrit_shell.PyRITShell(no_animation=True)
        existing = AsyncMock()
        existing.close_async = AsyncMock()
        s._api_client = existing
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("pyrit.cli._server_launcher.ServerLauncher.start_async", new_callable=AsyncMock) as mock_start,
            patch("pyrit.cli.api_client.PyRITApiClient") as mock_client_class,
        ):
            mock_start.return_value = "http://localhost:8000"
            new_client = AsyncMock()
            new_client.__aenter__ = AsyncMock(return_value=new_client)
            mock_client_class.return_value = new_client
            s.do_start_server("")
        existing.close_async.assert_awaited_once()
        assert s._api_client is new_client

    def test_start_server_launch_failure(self, capsys):
        s = pyrit_shell.PyRITShell(no_animation=True)
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.start_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("nope"),
            ),
        ):
            s.do_start_server("")
        assert "nope" in capsys.readouterr().out

    def test_stop_server_with_owned_launcher(self, shell, capsys):
        s, client = shell
        launcher = MagicMock()
        s._launcher = launcher
        s.do_stop_server("")
        launcher.stop.assert_called_once()
        assert "Server stopped" in capsys.readouterr().out
        assert s._launcher is None
        assert s._api_client is None

    def test_stop_server_by_port_success(self, shell, capsys):
        s, _ = shell
        s._base_url = "http://localhost:8000"
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("pyrit.cli._server_launcher.stop_server_on_port", return_value=True),
        ):
            s.do_stop_server("")
        assert "stopped" in capsys.readouterr().out

    def test_stop_server_by_port_skips_when_no_pyrit_backend(self, shell, capsys):
        s, _ = shell
        s._base_url = "http://localhost:8000"
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("pyrit.cli._server_launcher.stop_server_on_port") as mock_stop,
        ):
            s.do_stop_server("")
        mock_stop.assert_not_called()
        assert "not stopping" in capsys.readouterr().out

    def test_stop_server_close_client_swallows_errors(self, shell):
        s, client = shell
        launcher = MagicMock()
        s._launcher = launcher
        client.close_async = AsyncMock(side_effect=RuntimeError("ignored"))
        s.do_stop_server("")
        assert s._api_client is None


class TestShellScenarioParamFlow:
    """Regression tests: shell.do_run must forward scenario-declared parameters."""

    def test_run_passes_scenario_declared_params(self, shell):
        from pyrit.models.catalog import ScenarioParameterSummary

        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(
            supported_parameters=[ScenarioParameterSummary(name="max_turns", description="...", param_type="str")],
        )
        client.start_scenario_run_async = AsyncMock(return_value=TestDoRun._run_payload("CREATED"))
        client.get_scenario_run_async = AsyncMock(return_value=TestDoRun._run_payload("COMPLETED"))
        client.get_scenario_run_results_async = AsyncMock(return_value=TestDoRun._empty_scenario_result())

        with (
            patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("time.sleep"),
        ):
            s.do_run("foo --target t --max-turns 7")

        sent_request = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent_request.scenario_params == {"max_turns": "7"}

    def test_run_metadata_fetch_failure_aborts(self, shell, capsys):
        s, client = shell
        client.get_scenario_async = AsyncMock(side_effect=RuntimeError("net down"))
        s.do_run("foo --target t")
        assert "Error fetching scenario metadata" in capsys.readouterr().out

    def test_run_unknown_scenario_aborts(self, shell, capsys):
        s, client = shell
        client.get_scenario_async.return_value = None
        s.do_run("foo --target t")
        assert "not found on server" in capsys.readouterr().out

    def test_run_unknown_flag_for_scenario_with_declared_params_errors(self, shell, capsys):
        from pyrit.models.catalog import ScenarioParameterSummary

        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(
            supported_parameters=[ScenarioParameterSummary(name="max_turns", description="...", param_type="str")],
        )
        s.do_run("foo --target t --not-a-real-flag x")
        captured = capsys.readouterr().out
        assert "Unknown argument" in captured or "Error" in captured

    def test_run_fat_fingered_flag_with_no_scenario_params_errors(self, shell, capsys):
        """Even when the scenario declares no params, unknown flags must error (no silent no-op)."""
        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(supported_parameters=[])
        s.do_run("foo --target t --initialization-scripts /nope.py")
        captured = capsys.readouterr().out
        assert "Unknown argument: --initialization-scripts" in captured
        client.start_scenario_run_async.assert_not_called()

    def test_run_fat_fingered_log_level_flag_errors(self, shell, capsys):
        """--log-level was a stale shell-only flag; passing it must now error."""
        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(supported_parameters=[])
        s.do_run("foo --target t --log-level DEBUG")
        captured = capsys.readouterr().out
        assert "Unknown argument: --log-level" in captured
        client.start_scenario_run_async.assert_not_called()


class TestScenarioParamCoercionInShell:
    """Shell-side regression tests for typed scenario params from the catalog."""

    def test_shell_list_param_collects_multiple_values(self, shell):
        from pyrit.models.catalog import ScenarioParameterSummary

        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(
            supported_parameters=[
                ScenarioParameterSummary(name="items", description="list field", param_type="list[str]", is_list=True)
            ],
        )
        client.start_scenario_run_async = AsyncMock(return_value=TestDoRun._run_payload("CREATED"))
        client.get_scenario_run_async = AsyncMock(return_value=TestDoRun._run_payload("COMPLETED"))
        client.get_scenario_run_results_async = AsyncMock(return_value=TestDoRun._empty_scenario_result())

        with (
            patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock),
            patch("pyrit.cli._output.print_scenario_run_progress"),
            patch("time.sleep"),
        ):
            s.do_run("foo --target t --items a b c")

        sent = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent.scenario_params == {"items": ["a", "b", "c"]}

    def test_shell_choices_rejected_before_request(self, shell, capsys):
        from pyrit.models.catalog import ScenarioParameterSummary

        s, client = shell
        client.get_scenario_async.return_value = client._make_typed_scenario(
            supported_parameters=[
                ScenarioParameterSummary(name="mode", description="...", param_type="str", choices=["fast", "slow"])
            ],
        )
        s.do_run("foo --target t --mode warp")
        out = capsys.readouterr().out
        # Parameter.coerce_value raises ValueError on out-of-choice values;
        # do_run surfaces these as "Error: ...".
        assert "Error" in out
        client.start_scenario_run_async.assert_not_called()


class TestSplitInitializerPaths:
    def test_posix_splits_on_whitespace(self):
        with patch.object(pyrit_shell.os, "name", "posix"):
            assert pyrit_shell._split_initializer_paths("/a/one.py /b/two.py") == ["/a/one.py", "/b/two.py"]

    def test_posix_respects_quotes_with_spaces(self):
        with patch.object(pyrit_shell.os, "name", "posix"):
            assert pyrit_shell._split_initializer_paths('"/a b/one.py"') == ["/a b/one.py"]

    def test_windows_preserves_unquoted_backslash_path(self):
        with patch.object(pyrit_shell.os, "name", "nt"):
            assert pyrit_shell._split_initializer_paths(r"C:\Users\me\init.py") == [r"C:\Users\me\init.py"]

    def test_windows_quoted_path_with_spaces_strips_quotes(self):
        with patch.object(pyrit_shell.os, "name", "nt"):
            assert pyrit_shell._split_initializer_paths(r'"C:\a b\one.py"') == [r"C:\a b\one.py"]

    def test_windows_multiple_paths(self):
        with patch.object(pyrit_shell.os, "name", "nt"):
            result = pyrit_shell._split_initializer_paths(r'"C:\a b\one.py" C:\c\two.py')
            assert result == [r"C:\a b\one.py", r"C:\c\two.py"]

    @pytest.mark.parametrize("os_name", ["posix", "nt"])
    def test_unterminated_quote_raises(self, os_name):
        with patch.object(pyrit_shell.os, "name", os_name):
            with pytest.raises(ValueError):
                pyrit_shell._split_initializer_paths('"unterminated')
