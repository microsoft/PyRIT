# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_scan CLI module (thin REST client).
"""

import logging
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import _config_reader as pyrit_scan_config_reader
from pyrit.cli import pyrit_scan
from pyrit.models import Parameter
from unit.mocks import make_scenario_result


def _sp(*, name, description="", default=None, param_type="str", choices=None, is_list=False) -> Parameter:
    """Build a real Parameter from the legacy Summary-style kwargs (param_type as a string)."""
    return Parameter.model_validate(
        {
            "name": name,
            "description": description,
            "default": default,
            "type_name": param_type,
            "choices": choices,
            "is_list": is_list,
        }
    )


def test_dataset_filter_help_covers_every_request_model_key():
    """
    The frontend per-key ``--dataset-filters`` help must describe exactly the server-side allow-list.

    A missing key means a filter the request model accepts has no CLI help (and, by design, no
    documented semantics); an extra key means the CLI advertises a filter the server rejects.
    Adding to ``DATASET_FILTERS`` therefore forces adding a ``_DATASET_FILTER_HELP`` entry, where
    the surrounding entries model the one-line semantics note to write.
    """
    from pyrit.cli._cli_args import _DATASET_FILTER_HELP
    from pyrit.models.catalog.scenario import DATASET_FILTERS

    assert set(_DATASET_FILTER_HELP) == DATASET_FILTERS
    assert all(_DATASET_FILTER_HELP.values()), "every dataset filter key needs a semantics note"


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

    def test_parse_args_list_datasets(self):
        args = pyrit_scan.parse_args(["--list-datasets"])
        assert args.list_datasets is True

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

    def test_parse_args_with_dataset_filters(self):
        args = pyrit_scan.parse_args(["test_scenario", "--dataset-filters", "harm_categories=cyber", "data_types=text"])
        assert args.dataset_filters == [("harm_categories", "cyber"), ("data_types", "text")]

    def test_parse_args_dataset_filter_without_equals_errors(self):
        with pytest.raises(SystemExit):
            pyrit_scan.parse_args(["test_scenario", "--dataset-filters", "harm_categories"])

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

    def test_parse_args_with_list_converters(self):
        args = pyrit_scan.parse_args(["--list-converters"])
        assert args.list_converters is True

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
            parsed=Namespace(
                scenario_name="x",
                config_file=None,
                scenario__max_turns=10,
                scenario__mode="fast",
            )
        )
        assert result == {"max_turns": 10, "mode": "fast"}


def _make_scenario_result():
    """Build a minimal but valid ``ScenarioResult`` for the run-results happy path."""
    from datetime import datetime, timezone

    from pyrit.models import (
        AttackOutcome,
        AttackResult,
        ComponentIdentifier,
        ScenarioRunState,
    )

    attack = AttackResult(
        conversation_id="conv-1",
        objective="extract data",
        outcome=AttackOutcome.SUCCESS,
        executed_turns=1,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    return make_scenario_result(
        scenario_name="test_scenario",
        scenario_description="A test",
        objective_target_identifier=ComponentIdentifier.model_validate(
            {"__type__": "FakeTarget", "__module__": "test.mod", "params": {}}
        ),
        objective_scorer_identifier=None,
        attack_results={"strat_a": [attack]},
        scenario_run_state=ScenarioRunState.COMPLETED,
    )


def _mock_api_client():
    """Create a mock PyRITApiClient with default response behaviors (typed wire-data)."""
    from datetime import datetime, timezone

    from pyrit.models import ScenarioRunState, TargetCapabilities
    from pyrit.models.catalog import (
        RegisteredScenario,
        ScenarioRunSummary,
        TargetInstance,
    )

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    client = AsyncMock()
    client.health_check_async.return_value = True
    client.list_scenarios_async.return_value = []
    client.list_initializers_async.return_value = []
    client.list_targets_async.return_value = []
    client.list_datasets_async.return_value = {"items": []}
    client.list_converters_async.return_value = {"items": []}
    client.get_scenario_async.return_value = RegisteredScenario(
        scenario_name="test_scenario",
        scenario_type="X",
        description="",
        default_strategy="",
        aggregate_strategies=[],
        all_strategies=[],
        default_datasets=[],
        supported_parameters=[],
    )
    client.start_scenario_run_async.return_value = ScenarioRunSummary(
        scenario_result_id="test-id-123",
        scenario_name="test_scenario",
        scenario_version=0,
        status=ScenarioRunState.CREATED,
        created_at=now,
        updated_at=now,
        strategies_used=[],
        total_attacks=0,
        completed_attacks=0,
        objective_achieved_rate=0,
    )
    client.get_scenario_run_async.return_value = ScenarioRunSummary(
        scenario_result_id="test-id-123",
        scenario_name="test_scenario",
        scenario_version=0,
        status=ScenarioRunState.COMPLETED,
        created_at=now,
        updated_at=now,
        strategies_used=[],
        total_attacks=5,
        completed_attacks=5,
        objective_achieved_rate=40,
    )
    # get_scenario_run_results_async returns a valid ScenarioResult by default so the
    # run-results happy path is exercised. Tests covering the failure path override
    # this with a side_effect.
    client.get_scenario_run_results_async.return_value = _make_scenario_result()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    # Marker so tests that re-shape the mock can find the unused TargetInstance helper.
    _ = (TargetCapabilities, TargetInstance)
    return client


class TestMain:
    """Tests for main function (thin REST client)."""

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_scenarios(self, mock_client_class, mock_probe):
        """Test main with --list-scenarios flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 0
        mock_client.list_scenarios_async.assert_awaited_once()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_initializers(self, mock_client_class, mock_probe):
        """Test main with --list-initializers flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0
        mock_client.list_initializers_async.assert_awaited_once()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_targets(self, mock_client_class, mock_probe):
        """Test main with --list-targets flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-targets"])

        assert result == 0
        mock_client.list_targets_async.assert_awaited_once()

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_converters(self, mock_client_class, mock_probe):
        """Test main with --list-converters flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-converters"])

        assert result == 0
        mock_client.list_converters_async.assert_awaited_once()

    @patch("pyrit.cli._server_launcher.ServerLauncher.probe_health_async", new_callable=AsyncMock, return_value=True)
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_list_datasets(self, mock_client_class, mock_probe):
        """Test main with --list-datasets flag."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["--list-datasets"])

        assert result == 0
        mock_client.list_datasets_async.assert_awaited_once()

    def test_main_no_args_shows_help(self):
        """Test main with no arguments shows help."""
        result = pyrit_scan.main([])
        assert result == 0  # shows help and exits

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    def test_main_run_scenario(self, _mock_print, mock_client_class, mock_probe):
        """Test main running a scenario."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "my_target"])

        assert result == 0
        mock_client.get_scenario_async.assert_awaited_once()
        mock_client.start_scenario_run_async.assert_awaited_once()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    def test_main_run_scenario_with_initializers(self, _mock_print, mock_client_class, mock_probe):
        """Test main maps --initializers to request format."""
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t", "--initializers", "target", "datasets"])

        assert result == 0
        call_kwargs = mock_client.start_scenario_run_async.call_args.kwargs
        request = call_kwargs["request"]
        assert request.initializers == ["target", "datasets"]

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=False,
    )
    def test_main_server_not_available(self, mock_probe, capsys):
        """Test main when server is not available."""
        result = pyrit_scan.main(["--list-scenarios"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Server not available" in captured.out

    def test_main_malformed_config_is_hard_error(self, tmp_path, capsys):
        """A malformed --config-file should fail loudly, not silently use defaults."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(": :\nnot yaml: [unbalanced\n", encoding="utf-8")
        with patch.object(
            pyrit_scan_config_reader,
            "_DEFAULT_CONFIG_FILE",
            tmp_path / "missing_default.yaml",
        ):
            result = pyrit_scan.main(["--list-scenarios", "--config-file", str(bad)])

        assert result == 1
        assert "not valid YAML" in capsys.readouterr().err

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=False,
    )
    def test_main_stop_server(self, mock_probe, capsys):
        """Test main with --stop-server."""
        result = pyrit_scan.main(["--stop-server"])

        assert result == 0
        captured = capsys.readouterr()
        assert "No server running" in captured.out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
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

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_failed_scenario(self, mock_client_class, mock_probe):
        """Test main when scenario run fails."""
        from datetime import datetime, timezone

        from pyrit.models import ScenarioRunState
        from pyrit.models.catalog import ScenarioRunSummary

        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        mock_client = _mock_api_client()
        mock_client.get_scenario_run_async.return_value = ScenarioRunSummary(
            scenario_result_id="test-id",
            scenario_name="test_scenario",
            scenario_version=0,
            status=ScenarioRunState.FAILED,
            created_at=now,
            updated_at=now,
            total_attacks=0,
            completed_attacks=0,
            objective_achieved_rate=0,
            error="Something went wrong",
        )
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t"])

        assert result == 1


# ---------------------------------------------------------------------------
# Internal helper coverage
# ---------------------------------------------------------------------------


class TestStopServerOnPort:
    """Tests for stop_server_on_port helper (now lives in _server_launcher)."""

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    @patch("os.kill")
    def test_stop_on_windows_finds_pid_via_netstat(self, mock_kill, mock_run):
        from pyrit.cli import _server_launcher

        mock_run.return_value = MagicMock(
            stdout="  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       1234\n",
        )
        assert _server_launcher.stop_server_on_port(port=8000) is True
        mock_kill.assert_called_once()

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    @patch("os.kill")
    def test_stop_on_windows_does_not_match_port_substring(self, mock_kill, mock_run):
        from pyrit.cli import _server_launcher

        # Listening on :8000/:8080 should NOT be matched when stopping port 80.
        mock_run.return_value = MagicMock(
            stdout=(
                "  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       1234\n"
                "  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       2345\n"
            ),
        )
        assert _server_launcher.stop_server_on_port(port=80) is False
        mock_kill.assert_not_called()

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    @patch("os.kill")
    def test_stop_on_windows_matches_ipv6_local_address(self, mock_kill, mock_run):
        from pyrit.cli import _server_launcher

        mock_run.return_value = MagicMock(
            stdout="  TCP    [::]:8000              [::]:0                 LISTENING       9999\n",
        )
        assert _server_launcher.stop_server_on_port(port=8000) is True
        mock_kill.assert_called_once_with(9999, pytest.importorskip("signal").SIGTERM)

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    @patch("os.kill")
    def test_stop_on_unix_finds_pid_via_lsof(self, mock_kill, mock_run):
        from pyrit.cli import _server_launcher

        mock_run.return_value = MagicMock(stdout="5678\n")
        assert _server_launcher.stop_server_on_port(port=8000) is True
        mock_kill.assert_called_once_with(5678, pytest.importorskip("signal").SIGTERM)

    @patch("subprocess.run", side_effect=OSError("nope"))
    def test_stop_swallows_errors_and_returns_false(self, _mock_run):
        from pyrit.cli import _server_launcher

        assert _server_launcher.stop_server_on_port(port=8000) is False

    @patch("sys.platform", "linux")
    @patch("subprocess.run")
    def test_stop_returns_false_when_no_pid_found(self, mock_run):
        from pyrit.cli import _server_launcher

        mock_run.return_value = MagicMock(stdout="")
        assert _server_launcher.stop_server_on_port(port=8000) is False


class TestAddScenarioParamsFromApi:
    """Tests for _add_scenario_params_from_api."""

    def test_adds_unseen_params_as_optional_flags(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[
                _sp(name="max_turns", description="Max turns.", param_type="str"),
                _sp(name="mode", description="Mode.", param_type="str"),
            ],
        )
        parsed = parser.parse_args(["--max-turns", "5", "--mode", "fast"])
        assert parsed.scenario__max_turns == "5"
        assert parsed.scenario__mode == "fast"

    def test_skips_params_that_collide_with_existing_flags(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        parser.add_argument("--target")
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="target", description="...", param_type="str")],
        )
        parsed = parser.parse_args(["--target", "x"])
        # Original --target wins; no scenario__target added.
        assert parsed.target == "x"
        assert not hasattr(parsed, "scenario__target")


class TestBuildRunRequest:
    """Tests for _build_run_request."""

    def test_includes_initializer_args(self):
        parsed = Namespace(
            target="t",
            initializers=[
                {"name": "openai_target", "args": {"model": "gpt-4"}},
                "datasets",
            ],
            scenario_strategies=None,
            max_concurrency=None,
            max_retries=None,
            dataset_names=None,
            max_dataset_size=None,
            dataset_filters=None,
            memory_labels=None,
        )
        request = pyrit_scan._build_run_request(parsed_args=parsed, scenario_name="s")
        assert request.initializers == ["openai_target", "datasets"]
        assert request.initializer_args == {"openai_target": {"model": "gpt-4"}}

    def test_populates_optional_fields(self):
        parsed = Namespace(
            target="t",
            initializers=None,
            scenario_strategies=["s1"],
            max_concurrency=3,
            max_retries=2,
            dataset_names=["d1"],
            max_dataset_size=10,
            dataset_filters=None,
            memory_labels='{"key":"value"}',
        )
        request = pyrit_scan._build_run_request(parsed_args=parsed, scenario_name="s")
        assert request.strategies == ["s1"]
        assert request.max_concurrency == 3
        assert request.max_retries == 2
        assert request.dataset_names == ["d1"]
        assert request.max_dataset_size == 10
        assert request.labels == {"key": "value"}

    def test_populates_dataset_filters(self):
        parsed = Namespace(
            target="t",
            initializers=None,
            scenario_strategies=None,
            max_concurrency=None,
            max_retries=None,
            dataset_names=None,
            max_dataset_size=None,
            dataset_filters=[("harm_categories", "cyber"), ("data_types", "text")],
            memory_labels=None,
        )
        request = pyrit_scan._build_run_request(parsed_args=parsed, scenario_name="s")
        assert request.dataset_filters == {"harm_categories": ["cyber"], "data_types": ["text"]}

    def test_duplicate_dataset_filter_key_raises(self):
        parsed = Namespace(
            target="t",
            initializers=None,
            scenario_strategies=None,
            max_concurrency=None,
            max_retries=None,
            dataset_names=None,
            max_dataset_size=None,
            dataset_filters=[("harm_categories", "cyber"), ("harm_categories", "violence")],
            memory_labels=None,
        )
        with pytest.raises(ValueError, match="Duplicate dataset filter 'harm_categories'"):
            pyrit_scan._build_run_request(parsed_args=parsed, scenario_name="s")

    def test_includes_scenario_declared_params(self):
        parsed = Namespace(
            target=None,
            initializers=None,
            scenario_strategies=None,
            max_concurrency=None,
            max_retries=None,
            dataset_names=None,
            max_dataset_size=None,
            dataset_filters=None,
            memory_labels=None,
            scenario__max_turns="7",
        )
        request = pyrit_scan._build_run_request(parsed_args=parsed, scenario_name="s")
        assert request.scenario_params == {"max_turns": "7"}


class TestResolveServerUrl:
    """Tests for _resolve_server_url_async."""

    async def test_uses_cli_flag_when_provided(self):
        parsed = Namespace(
            server_url="http://override:7000",
            start_server=False,
            config_file=None,
        )
        with patch(
            "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
            new=AsyncMock(return_value=True),
        ):
            result = await pyrit_scan._resolve_server_url_async(parsed_args=parsed)
        assert result == "http://override:7000"

    async def test_returns_none_when_unhealthy_and_no_start_server(self):
        parsed = Namespace(server_url=None, start_server=False, config_file=None)
        with (
            patch(
                "pyrit.cli._config_reader.read_server_url",
                return_value=None,
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=False),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) is None

    async def test_auto_starts_server_when_requested(self):
        parsed = Namespace(server_url=None, start_server=True, config_file=None)
        with (
            patch("pyrit.cli._config_reader.read_server_url", return_value=None),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.start_async",
                new=AsyncMock(return_value="http://localhost:8000"),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) == "http://localhost:8000"

    async def test_returns_none_when_start_server_raises(self, capsys):
        parsed = Namespace(server_url=None, start_server=True, config_file=None)
        with (
            patch("pyrit.cli._config_reader.read_server_url", return_value=None),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.start_async",
                new=AsyncMock(side_effect=RuntimeError("nope")),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) is None
        assert "nope" in capsys.readouterr().out

    async def test_start_server_refuses_when_url_differs_from_default(self, capsys):
        # User explicitly configured a non-default URL but asks us to launch the bundled
        # backend. The launcher only knows how to bind localhost:8000, so we must refuse.
        parsed = Namespace(server_url="http://other:9999", start_server=True, config_file=None)
        start_async_mock = AsyncMock()
        with (
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.start_async",
                new=start_async_mock,
            ),
        ):
            result = await pyrit_scan._resolve_server_url_async(parsed_args=parsed)
        assert result is None
        start_async_mock.assert_not_called()
        err = capsys.readouterr().err
        assert "cannot --start-server" in err
        assert "http://other:9999" in err

    async def test_resolution_order_cli_beats_config_beats_default(self):
        """CLI flag > config-file value > built-in default."""
        # 1) CLI flag wins even when config has a different value.
        parsed = Namespace(server_url="http://cli:1111", start_server=False, config_file=None)
        with (
            patch(
                "pyrit.cli._config_reader.read_server_url",
                return_value="http://cfg:2222",
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=True),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) == "http://cli:1111"

        # 2) Config wins when CLI omitted.
        parsed = Namespace(server_url=None, start_server=False, config_file=None)
        with (
            patch(
                "pyrit.cli._config_reader.read_server_url",
                return_value="http://cfg:2222",
            ),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=True),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) == "http://cfg:2222"

        # 3) Built-in default when neither CLI nor config provide a URL.
        from pyrit.cli._config_reader import DEFAULT_SERVER_URL

        parsed = Namespace(server_url=None, start_server=False, config_file=None)
        with (
            patch("pyrit.cli._config_reader.read_server_url", return_value=None),
            patch(
                "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
                new=AsyncMock(return_value=True),
            ),
        ):
            assert await pyrit_scan._resolve_server_url_async(parsed_args=parsed) == DEFAULT_SERVER_URL


class TestScenarioParamCoercion:
    """Regression tests for client-side coercion of typed scenario-declared params."""

    def test_list_param_uses_nargs_plus(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="items", description="...", param_type="list[str]", is_list=True)],
        )
        parsed = parser.parse_args(["--items", "a", "b", "c"])
        assert parsed.scenario__items == ["a", "b", "c"]

    def test_int_param_is_coerced(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="max_turns", description="...", param_type="int")],
        )
        parsed = parser.parse_args(["--max-turns", "7"])
        assert parsed.scenario__max_turns == 7

    def test_int_param_invalid_value_rejected_client_side(self, capsys):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="max_turns", description="...", param_type="int")],
        )
        with pytest.raises(SystemExit):
            parser.parse_args(["--max-turns", "not-an-int"])
        assert "invalid value" in capsys.readouterr().err

    def test_bool_param_rejects_invalid_value_client_side(self, capsys):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="dry_run", description="...", param_type="bool")],
        )

        parsed = parser.parse_args(["--dry-run", "false"])
        assert parsed.scenario__dry_run is False

        parsed = parser.parse_args(["--dry-run", "yes"])
        assert parsed.scenario__dry_run is True

        with pytest.raises(SystemExit):
            parser.parse_args(["--dry-run", "maybe"])
        assert "invalid value" in capsys.readouterr().err

        # "on"/"y" are NOT part of the canonical boolean vocabulary the shell and
        # backend accept (true/false, 1/0, yes/no); scan must reject them too so
        # the same flag never behaves differently depending on the entry point.
        with pytest.raises(SystemExit):
            parser.parse_args(["--dry-run", "on"])
        assert "invalid value" in capsys.readouterr().err

    def test_list_int_param_coerces_each_value(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="sample_ids", description="...", param_type="list[int]", is_list=True)],
        )

        parsed = parser.parse_args(["--sample-ids", "1", "2", "3"])
        assert parsed.scenario__sample_ids == [1, 2, 3]

    def test_typed_choices_are_compared_after_coercion(self):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="max_turns", description="...", param_type="int", choices=["1", "2"])],
        )

        parsed = parser.parse_args(["--max-turns", "1"])
        assert parsed.scenario__max_turns == 1

    def test_choices_validated_client_side(self, capsys):
        from argparse import ArgumentParser

        parser = ArgumentParser()
        pyrit_scan._add_scenario_params_from_api(
            parser=parser,
            params=[_sp(name="mode", description="...", param_type="str", choices=["fast", "slow"])],
        )
        parsed = parser.parse_args(["--mode", "fast"])
        assert parsed.scenario__mode == "fast"

        with pytest.raises(SystemExit):
            parser.parse_args(["--mode", "warp"])
        assert "invalid value" in capsys.readouterr().err


class TestMainExtraPaths:
    """Tests for additional main() code paths."""

    def test_main_no_args_prints_help_and_exits_zero(self, capsys):
        result = pyrit_scan.main([])
        assert result == 0
        captured = capsys.readouterr()
        assert "PyRIT Scanner" in captured.out or "usage" in captured.out.lower()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_scenario_not_found_lists_available(self, mock_client_class, _mock_probe, capsys):
        from pyrit.models.catalog import RegisteredScenario

        mock_client = _mock_api_client()
        mock_client.get_scenario_async.return_value = None
        mock_client.list_scenarios_async.return_value = [
            RegisteredScenario(
                scenario_name="alt_a",
                scenario_type="X",
                description="",
                default_strategy="",
                aggregate_strategies=[],
                all_strategies=[],
                default_datasets=[],
            ),
            RegisteredScenario(
                scenario_name="alt_b",
                scenario_type="X",
                description="",
                default_strategy="",
                aggregate_strategies=[],
                all_strategies=[],
                default_datasets=[],
            ),
        ]
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["nonexistent", "--target", "t"])
        assert result == 1
        captured = capsys.readouterr()
        assert "alt_a" in captured.out
        assert "alt_b" in captured.out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_start_scenario_failure(self, mock_client_class, _mock_probe, capsys):
        mock_client = _mock_api_client()
        mock_client.start_scenario_run_async.side_effect = RuntimeError("server full")
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t"])
        assert result == 1
        captured = capsys.readouterr()
        assert "server full" in captured.out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_run_results_failure_is_hard_error(self, mock_client_class, _mock_probe, capsys):
        mock_client = _mock_api_client()
        mock_client.get_scenario_run_results_async.side_effect = RuntimeError("nope")
        mock_client_class.return_value = mock_client

        result = pyrit_scan.main(["test_scenario", "--target", "t"])
        # A completed run whose results can't be fetched/parsed is a hard CLI failure.
        assert result == 1
        captured = capsys.readouterr()
        # The error must be surfaced loudly (not swallowed) and include the exception detail.
        assert "ERROR: The scenario completed" in captured.out
        assert "nope" in captured.out
        # The summary printer should still be used as a fallback for context.
        assert "test_scenario" in captured.out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_start_server_only_prints_url_and_returns_zero(self, mock_client_class, _mock_probe, capsys):
        result = pyrit_scan.main(["--start-server"])
        assert result == 0
        captured = capsys.readouterr()
        assert "running" in captured.out.lower()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli._server_launcher.stop_server_on_port", return_value=True)
    def test_main_stop_server_kills_process_and_returns_zero(self, _stop_mock, _mock_probe, capsys):
        result = pyrit_scan.main(["--stop-server"])
        assert result == 0
        assert "stopped" in capsys.readouterr().out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli._server_launcher.stop_server_on_port", return_value=False)
    def test_main_stop_server_when_process_cannot_be_identified(self, _stop_mock, _mock_probe, capsys):
        result = pyrit_scan.main(["--stop-server"])
        assert result == 0
        out = capsys.readouterr().out
        assert "could not identify" in out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_add_initializer_missing_file(self, mock_client_class, _mock_probe, capsys, tmp_path):
        mock_client = _mock_api_client()
        mock_client_class.return_value = mock_client
        missing = tmp_path / "nonexistent.py"

        result = pyrit_scan.main(["--add-initializer", str(missing)])
        assert result == 1
        assert "File not found" in capsys.readouterr().out

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_add_initializer_success(self, mock_client_class, _mock_probe, capsys, tmp_path):
        mock_client = _mock_api_client()
        mock_client.register_initializer_async = AsyncMock(return_value={"initializer_name": "myinit"})
        mock_client_class.return_value = mock_client

        script = tmp_path / "myinit.py"
        script.write_text("# stub initializer\n")

        result = pyrit_scan.main(["--add-initializer", str(script)])
        assert result == 0
        assert "Registered initializer 'myinit'" in capsys.readouterr().out
        mock_client.register_initializer_async.assert_awaited_once()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    def test_main_add_initializer_server_disabled(self, mock_client_class, _mock_probe, capsys, tmp_path):
        from pyrit.cli.api_client import ServerNotAvailableError

        mock_client = _mock_api_client()
        mock_client.register_initializer_async = AsyncMock(side_effect=ServerNotAvailableError("disabled"))
        mock_client_class.return_value = mock_client

        script = tmp_path / "myinit.py"
        script.write_text("# stub\n")

        result = pyrit_scan.main(["--add-initializer", str(script)])
        assert result == 1
        assert "disabled" in capsys.readouterr().out


class TestScenarioParamFlow:
    """Regression tests for scenario-declared parameters flowing through the CLI."""

    @staticmethod
    def _build_mock_client(supported_params=None, status="COMPLETED"):
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock

        from pyrit.models import ScenarioRunState
        from pyrit.models.catalog import (
            RegisteredScenario,
            ScenarioRunSummary,
        )

        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        typed_params: list[Parameter] = []
        for p in supported_params or []:
            if isinstance(p, Parameter):
                typed_params.append(p)
            else:
                typed_params.append(
                    _sp(
                        name=p["name"],
                        description=p.get("description", ""),
                        default=p.get("default"),
                        param_type=p.get("param_type", "str"),
                        choices=p.get("choices"),
                        is_list=p.get("is_list", False),
                    )
                )

        client = AsyncMock()
        client.list_scenarios_async.return_value = [
            RegisteredScenario(
                scenario_name="foo",
                scenario_type="X",
                description="",
                default_strategy="",
                aggregate_strategies=[],
                all_strategies=[],
                default_datasets=[],
            )
        ]
        client.get_scenario_async.return_value = RegisteredScenario(
            scenario_name="foo",
            scenario_type="X",
            description="",
            default_strategy="",
            aggregate_strategies=[],
            all_strategies=[],
            default_datasets=[],
            supported_parameters=typed_params,
        )
        client.start_scenario_run_async.return_value = ScenarioRunSummary(
            scenario_result_id="rid",
            scenario_name="foo",
            scenario_version=0,
            status=ScenarioRunState.CREATED,
            created_at=now,
            updated_at=now,
        )
        client.get_scenario_run_async.return_value = ScenarioRunSummary(
            scenario_result_id="rid",
            scenario_name="foo",
            scenario_version=0,
            status=ScenarioRunState(status),
            created_at=now,
            updated_at=now,
        )
        # Default: get_scenario_run_results_async returns a valid result (happy path).
        client.get_scenario_run_results_async.return_value = _make_scenario_result()
        client.close_async = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    @patch("pyrit.cli._output.print_scenario_run_progress")
    def test_scenario_declared_flag_is_forwarded(self, _mock_prog, _mock_print, mock_client_class, _mock_probe):
        client = self._build_mock_client(supported_params=[{"name": "max_turns", "description": "..."}])
        mock_client_class.return_value = client

        result = pyrit_scan.main(["foo", "--target", "t", "--max-turns", "7"])

        assert result == 0
        sent_request = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent_request.scenario_params == {"max_turns": "7"}

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    @patch("pyrit.cli._output.print_scenario_run_progress")
    def test_typed_scenario_flags_are_forwarded_as_typed_values(
        self, _mock_prog, _mock_print, mock_client_class, _mock_probe
    ):
        client = self._build_mock_client(
            supported_params=[
                {"name": "dry_run", "description": "...", "param_type": "bool"},
                {
                    "name": "sample_ids",
                    "description": "...",
                    "param_type": "list[int]",
                    "is_list": True,
                },
            ]
        )
        mock_client_class.return_value = client

        result = pyrit_scan.main(["foo", "--target", "t", "--dry-run", "yes", "--sample-ids", "1", "2"])

        assert result == 0
        sent_request = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent_request.scenario_params == {"dry_run": True, "sample_ids": [1, 2]}

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    @patch("pyrit.cli._output.print_scenario_run_progress")
    def test_unknown_flag_after_valid_scenario_errors(self, _mock_prog, _mock_print, mock_client_class, _mock_probe):
        client = self._build_mock_client(supported_params=[{"name": "max_turns", "description": "..."}])
        mock_client_class.return_value = client

        result = pyrit_scan.main(["foo", "--target", "t", "--max-turns", "7", "--unknown-flag"])

        assert result == 1
        client.start_scenario_run_async.assert_not_called()

    @patch(
        "pyrit.cli._server_launcher.ServerLauncher.probe_health_async",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("pyrit.cli.api_client.PyRITApiClient")
    @patch("pyrit.cli._output.print_scenario_result_async", new_callable=AsyncMock)
    @patch("pyrit.cli._output.print_scenario_run_progress")
    def test_no_scenario_params_passes_through_cleanly(self, _mock_prog, _mock_print, mock_client_class, _mock_probe):
        client = self._build_mock_client(supported_params=[])
        mock_client_class.return_value = client

        result = pyrit_scan.main(["foo", "--target", "t"])

        assert result == 0
        sent_request = client.start_scenario_run_async.call_args.kwargs["request"]
        assert sent_request.scenario_params is None

    def test_parse_args_tolerates_scenario_specific_flags(self):
        # Pass 1 must not error on scenario-declared flags (they're recognized in pass 2).
        parsed = pyrit_scan.parse_args(["foo", "--target", "t", "--max-turns", "7"])
        assert parsed.scenario_name == "foo"
        assert parsed.target == "t"
        assert parsed._unknown_args == ["--max-turns", "7"]
