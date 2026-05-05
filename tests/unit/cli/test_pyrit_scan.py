# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the pyrit_scan CLI module.
"""

import logging
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
        assert args.log_level == logging.WARNING

    def test_parse_args_with_log_level(self):
        """Test parsing with log-level option."""
        args = pyrit_scan.parse_args(["test_scenario", "--log-level", "DEBUG"])

        assert args.log_level == logging.DEBUG

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
        assert args.memory_labels == '{"env":"test"}'

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

    def test_parse_args_with_target(self):
        """Test parsing with --target option."""
        args = pyrit_scan.parse_args(["test_scenario", "--target", "my_target"])

        assert args.target == "my_target"

    def test_parse_args_target_default_is_none(self):
        """Test --target defaults to None when not provided."""
        args = pyrit_scan.parse_args(["test_scenario"])

        assert args.target is None

    def test_parse_args_with_list_targets(self):
        """Test parsing --list-targets flag."""
        args = pyrit_scan.parse_args(["--list-targets"])

        assert args.list_targets is True


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
    def test_main_list_initializers(
        self,
        mock_frontend_core: MagicMock,
        mock_print_initializers: AsyncMock,
    ):
        """Test main with --list-initializers flag."""
        mock_print_initializers.return_value = 0

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0
        mock_print_initializers.assert_called_once()

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

    @patch("pyrit.cli.frontend_core.print_targets_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_list_targets_with_initializers(
        self,
        mock_frontend_core: MagicMock,
        mock_print_targets: AsyncMock,
    ):
        """Test main with --list-targets and --initializers passes initializers to FrontendCore."""
        mock_print_targets.return_value = 0

        result = pyrit_scan.main(["--list-targets", "--initializers", "target"])

        assert result == 0
        mock_frontend_core.assert_called_once()
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["initializer_names"] == ["target"]
        mock_print_targets.assert_called_once()

    @patch("pyrit.cli.frontend_core.print_targets_list_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_main_list_targets_with_scripts(
        self,
        mock_frontend_core: MagicMock,
        mock_resolve_scripts: MagicMock,
        mock_print_targets: AsyncMock,
    ):
        """Test main with --list-targets and --initialization-scripts passes scripts to FrontendCore."""
        mock_resolve_scripts.return_value = [Path("/test/script.py")]
        mock_print_targets.return_value = 0

        result = pyrit_scan.main(["--list-targets", "--initialization-scripts", "script.py"])

        assert result == 0
        mock_resolve_scripts.assert_called_once_with(script_paths=["script.py"])
        mock_frontend_core.assert_called_once()
        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["initialization_scripts"] == [Path("/test/script.py")]
        mock_print_targets.assert_called_once()

    @patch("pyrit.cli.frontend_core.resolve_initialization_scripts")
    def test_main_list_targets_with_missing_script(self, mock_resolve_scripts: MagicMock):
        """Test main with --list-targets and missing script file."""
        mock_resolve_scripts.side_effect = FileNotFoundError("Script not found")

        result = pyrit_scan.main(["--list-targets", "--initialization-scripts", "missing.py"])

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
        assert call_kwargs["log_level"] == logging.DEBUG
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
    def test_main_log_level_defaults_to_warning(self, mock_frontend_core: MagicMock):
        """Test main uses WARNING as default log level."""
        pyrit_scan.main(["--list-scenarios"])

        call_kwargs = mock_frontend_core.call_args[1]
        assert call_kwargs["log_level"] == logging.WARNING

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
    def test_main_list_initializers_integration(
        self,
        mock_print_initializers: AsyncMock,
    ):
        """Test main --list-initializers with minimal mocking."""
        mock_print_initializers.return_value = 0

        result = pyrit_scan.main(["--list-initializers"])

        assert result == 0


class TestTwoPassParsing:
    """Tests for the two-pass scenario-parameter augmentation flow."""

    @staticmethod
    def _patch_resolve(scenario_class):
        """Patch the registry lookup so tests don't depend on the real registry."""
        return patch.object(pyrit_scan, "_resolve_scenario_class", return_value=scenario_class)

    @staticmethod
    def _make_scenario_class(declared_params):
        """Build a stand-in class whose only obligation is to expose supported_parameters()."""

        class _FakeScenario:
            @classmethod
            def supported_parameters(cls):
                return list(declared_params)

        return _FakeScenario

    def test_no_scenario_resolved_leaves_namespace_unaugmented(self):
        """When the positional name is missing or unknown, scenario flags do not appear."""
        with self._patch_resolve(None):
            args = pyrit_scan.parse_args(["--list-scenarios"])

        # No scenario__-prefixed attrs sneaked in.
        scenario_keys = [k for k in vars(args) if k.startswith("scenario__")]
        assert scenario_keys == []

    def test_int_param_coerced(self):
        """A declared int parameter coerces its string CLI value to int."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        with self._patch_resolve(scenario_class):
            args = pyrit_scan.parse_args(["fake_scenario", "--max-turns", "10"])

        scenario_args = pyrit_scan._extract_scenario_args(parsed=args)
        assert scenario_args == {"max_turns": 10}

    def test_bool_param_uses_safe_coercion(self):
        """``--enabled false`` is correctly parsed to False (avoids the type=bool footgun)."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class([Parameter(name="enabled", description="d", param_type=bool)])
        with self._patch_resolve(scenario_class):
            args = pyrit_scan.parse_args(["fake_scenario", "--enabled", "false"])

        assert pyrit_scan._extract_scenario_args(parsed=args) == {"enabled": False}

    def test_list_param_collects_multiple_values(self):
        """A declared list[str] parameter uses nargs='+' to collect successive values."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class([Parameter(name="datasets", description="d", param_type=list[str])])
        with self._patch_resolve(scenario_class):
            args = pyrit_scan.parse_args(["fake_scenario", "--datasets", "a", "b", "c"])

        assert pyrit_scan._extract_scenario_args(parsed=args) == {"datasets": ["a", "b", "c"]}

    def test_choices_validated_by_argparse(self):
        """A value outside ``choices`` is rejected at parse time."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="mode", description="d", param_type=str, choices=("fast", "slow"))]
        )
        with self._patch_resolve(scenario_class):
            with pytest.raises(SystemExit):
                pyrit_scan.parse_args(["fake_scenario", "--mode", "medium"])

    def test_unset_scenario_flag_not_in_namespace(self):
        """``argparse.SUPPRESS`` keeps absent flags out of the parsed Namespace."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        with self._patch_resolve(scenario_class):
            args = pyrit_scan.parse_args(["fake_scenario"])

        assert pyrit_scan._extract_scenario_args(parsed=args) == {}

    def test_unknown_scenario_flag_rejected(self):
        """Argparse pass 2 rejects flags the scenario didn't declare."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        with self._patch_resolve(scenario_class):
            with pytest.raises(SystemExit):
                pyrit_scan.parse_args(["fake_scenario", "--unknown-flag", "value"])

    def test_collision_with_built_in_flag_raises_at_build_time(self):
        """A declared parameter colliding with a built-in flag fails at parser-build time."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_concurrency", description="d", param_type=int, default=10)]
        )
        with self._patch_resolve(scenario_class):
            with pytest.raises(ValueError, match="collides with an existing flag"):
                pyrit_scan.parse_args(["fake_scenario", "--max-concurrency", "5"])

    def test_two_scenario_params_with_same_kebab_form_raise(self):
        """Two declared parameters that normalize to the same kebab-case flag fail with our ValueError."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [
                Parameter(name="foo_bar", description="d", param_type=str),
                Parameter(name="foo-bar", description="d", param_type=str),
            ]
        )
        with self._patch_resolve(scenario_class):
            with pytest.raises(ValueError, match="collides with an existing flag"):
                pyrit_scan.parse_args(["fake_scenario", "--foo-bar", "x"])

    def test_scenario_flag_works_before_positional(self):
        """Pass 1 uses the full base parser so option order does not break positional ID."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        with self._patch_resolve(scenario_class):
            args = pyrit_scan.parse_args(["--config-file", "foo.yaml", "fake_scenario", "--max-turns", "7"])

        # config-file landed correctly + scenario name identified + scenario param parsed
        assert args.config_file == Path("foo.yaml")
        assert args.scenario_name == "fake_scenario"
        assert pyrit_scan._extract_scenario_args(parsed=args) == {"max_turns": 7}

    def test_help_after_scenario_lists_declared_flags(self, capsys):
        """`pyrit_scan <scenario> --help` shows scenario-declared flags inline."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="Conversation turn cap", param_type=int, default=5)]
        )
        with self._patch_resolve(scenario_class):
            with pytest.raises(SystemExit) as exc_info:
                pyrit_scan.parse_args(["fake_scenario", "--help"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--max-turns" in captured.out
        assert "Conversation turn cap" in captured.out

    def test_config_only_scenario_name_registers_scenario_flags(self):
        """When pass 1's positional doesn't resolve, fall back to ``scenario.name`` from the config file."""
        from pyrit.common import Parameter

        scenario_class = self._make_scenario_class(
            [Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )

        # Resolve only "fake_scenario"; anything pass 1 misclassifies as the positional
        # (e.g. the "7" from "--max-turns 7") returns None and triggers the config peek.
        def fake_resolve(name):
            return scenario_class if name == "fake_scenario" else None

        with (
            patch.object(pyrit_scan, "_peek_scenario_name_from_config", return_value="fake_scenario") as peek,
            patch.object(pyrit_scan, "_resolve_scenario_class", side_effect=fake_resolve),
        ):
            args = pyrit_scan.parse_args(["--config-file", "foo.yaml", "--max-turns", "7"])

        peek.assert_called_once()
        assert args.scenario_name is None
        assert pyrit_scan._extract_scenario_args(parsed=args) == {"max_turns": 7}


class TestExtractScenarioArgs:
    """Tests for the namespaced-dest extraction helper."""

    def test_no_scenario_keys_returns_empty(self):
        from argparse import Namespace

        result = pyrit_scan._extract_scenario_args(parsed=Namespace(scenario_name="x", config_file=None, log_level=20))
        assert result == {}

    def test_scenario_keys_extracted_with_prefix_stripped(self):
        from argparse import Namespace

        result = pyrit_scan._extract_scenario_args(
            parsed=Namespace(
                scenario_name="x",
                config_file=None,
                scenario__max_turns=10,
                scenario__mode="fast",
            )
        )
        assert result == {"max_turns": 10, "mode": "fast"}


class TestConfigScenarioMerge:
    """Tests for the CLI/config scenario_args merge in pyrit_scan.main()."""

    @staticmethod
    def _patch_resolve(scenario_class):
        return patch.object(pyrit_scan, "_resolve_scenario_class", return_value=scenario_class)

    @staticmethod
    def _make_scenario_class(declared_params):
        class _FakeScenario:
            @classmethod
            def supported_parameters(cls):
                return list(declared_params)

        return _FakeScenario

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_cli_args_override_config_args_per_key(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """When CLI and config both set max_turns, CLI wins per-key."""
        from pyrit.common import Parameter
        from pyrit.setup.configuration_loader import ScenarioConfig

        # Config sets max_turns=5, mode=slow; CLI overrides max_turns=10.
        mock_context = MagicMock()
        mock_context._scenario_config = ScenarioConfig(name="scam", args={"max_turns": 5, "mode": "slow"})
        mock_frontend_core.return_value = mock_context

        scenario_class = self._make_scenario_class(
            [
                Parameter(name="max_turns", description="d", param_type=int, default=5),
                Parameter(name="mode", description="d", param_type=str, default="slow"),
            ]
        )
        with self._patch_resolve(scenario_class):
            pyrit_scan.main(["scam", "--max-turns", "10"])

        # Inspect the scenario_args kwarg passed into run_scenario_async
        call_kwargs = mock_run_scenario.call_args.kwargs
        assert call_kwargs["scenario_args"] == {"max_turns": 10, "mode": "slow"}

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_config_scenario_used_when_no_positional(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Config-only scenario invocation: pyrit_scan --config-file my.yaml."""
        from pyrit.setup.configuration_loader import ScenarioConfig

        mock_context = MagicMock()
        mock_context._scenario_config = ScenarioConfig(name="scam", args={"max_turns": 5})
        mock_frontend_core.return_value = mock_context

        # No positional, no scenario flags (would require pass-2 augmentation,
        # which is a documented v1 limitation).
        with self._patch_resolve(None):
            result = pyrit_scan.main([])

        assert result == 0
        call_kwargs = mock_run_scenario.call_args.kwargs
        assert call_kwargs["scenario_name"] == "scam"
        assert call_kwargs["scenario_args"] == {"max_turns": 5}

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_config_args_ignored_when_cli_specifies_different_scenario(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """CLI scenario name differs from config: config args silently dropped (CLI-wins)."""
        from pyrit.setup.configuration_loader import ScenarioConfig

        mock_context = MagicMock()
        mock_context._scenario_config = ScenarioConfig(name="scam", args={"max_turns": 5})
        mock_frontend_core.return_value = mock_context

        with self._patch_resolve(None):
            pyrit_scan.main(["other_scenario"])

        call_kwargs = mock_run_scenario.call_args.kwargs
        assert call_kwargs["scenario_name"] == "other_scenario"
        assert call_kwargs["scenario_args"] == {}

    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_no_scenario_anywhere_returns_error(
        self,
        mock_frontend_core: MagicMock,
    ):
        """No CLI positional and no config scenario: explicit error message + nonzero exit."""
        mock_context = MagicMock()
        mock_context._scenario_config = None
        mock_frontend_core.return_value = mock_context

        with self._patch_resolve(None):
            result = pyrit_scan.main([])

        assert result == 1

    @patch("pyrit.cli.pyrit_scan.asyncio.run")
    @patch("pyrit.cli.frontend_core.run_scenario_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.FrontendCore")
    def test_config_args_deep_copied(
        self,
        mock_frontend_core: MagicMock,
        mock_run_scenario: AsyncMock,
        mock_asyncio_run: MagicMock,
    ):
        """Mutating scenario_args on one run must not leak into the config block."""
        from pyrit.setup.configuration_loader import ScenarioConfig

        original_args = {"datasets": ["a", "b"]}
        mock_context = MagicMock()
        mock_context._scenario_config = ScenarioConfig(name="scam", args=original_args)
        mock_frontend_core.return_value = mock_context

        with self._patch_resolve(None):
            pyrit_scan.main(["scam"])

        call_kwargs = mock_run_scenario.call_args.kwargs
        # Mutate the passed dict
        call_kwargs["scenario_args"]["datasets"].append("c")
        # Original config block must be untouched
        assert original_args == {"datasets": ["a", "b"]}
