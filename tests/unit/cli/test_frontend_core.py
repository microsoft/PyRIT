# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the frontend_core module.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import frontend_core
from pyrit.registry import InitializerMetadata, ScenarioMetadata


class TestFrontendCore:
    """Tests for FrontendCore class."""

    @patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", Path("/nonexistent/.pyrit_conf"))
    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        context = frontend_core.FrontendCore()

        assert context._database == frontend_core.SQLITE
        assert context._initialization_scripts is None
        assert context._initializer_configs is None
        assert context._log_level == logging.WARNING
        assert context._initialized is False

    def test_init_with_all_parameters(self):
        """Test initialization with all parameters."""
        scripts = [Path("/test/script.py")]
        initializers = ["alpha_init", "beta_init", "gamma_init"]

        context = frontend_core.FrontendCore(
            database=frontend_core.IN_MEMORY,
            initialization_scripts=scripts,
            initializer_names=initializers,
            log_level=logging.DEBUG,
        )

        assert context._database == frontend_core.IN_MEMORY
        # Check path ends with expected components (Windows adds drive letter to Unix-style paths)
        assert context._initialization_scripts is not None
        assert len(context._initialization_scripts) == 1
        assert context._initialization_scripts[0].parts[-2:] == ("test", "script.py")
        assert context._initializer_configs is not None
        assert [ic.name for ic in context._initializer_configs] == initializers
        assert context._log_level == logging.DEBUG

    def test_init_with_invalid_database(self):
        """Test initialization with invalid database raises ValueError."""
        with pytest.raises(ValueError, match="Invalid database type"):
            frontend_core.FrontendCore(database="InvalidDB")

    @patch("pyrit.cli.frontend_core.ScenarioRegistry")
    @patch("pyrit.cli.frontend_core.InitializerRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    def test_initialize_loads_registries(
        self,
        mock_init_pyrit: AsyncMock,
        mock_init_registry: MagicMock,
        mock_scenario_registry: MagicMock,
    ):
        """Test initialize method loads registries."""
        context = frontend_core.FrontendCore()
        import asyncio

        asyncio.run(context.initialize_async())

        assert context._initialized is True
        mock_init_pyrit.assert_called_once()
        mock_scenario_registry.get_registry_singleton.assert_called_once()
        mock_init_registry.assert_called_once()

    @patch("pyrit.cli.frontend_core.ScenarioRegistry")
    @patch("pyrit.cli.frontend_core.InitializerRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_scenario_registry_property_initializes(
        self,
        mock_init_pyrit: AsyncMock,
        mock_init_registry: MagicMock,
        mock_scenario_registry: MagicMock,
    ):
        """Test scenario_registry property triggers initialization."""
        context = frontend_core.FrontendCore()
        assert context._initialized is False

        await context.initialize_async()
        registry = context.scenario_registry

        assert context._initialized is True
        assert registry is not None

    @patch("pyrit.cli.frontend_core.ScenarioRegistry")
    @patch("pyrit.cli.frontend_core.InitializerRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_initializer_registry_property_initializes(
        self,
        mock_init_pyrit: AsyncMock,
        mock_init_registry: MagicMock,
        mock_scenario_registry: MagicMock,
    ):
        """Test initializer_registry property triggers initialization."""
        context = frontend_core.FrontendCore()
        assert context._initialized is False

        await context.initialize_async()
        registry = context.initializer_registry

        assert context._initialized is True
        assert registry is not None


class TestValidationFunctions:
    """Tests for validation functions."""

    def test_validate_database_valid_values(self):
        """Test validate_database with valid values."""
        assert frontend_core.validate_database(database=frontend_core.IN_MEMORY) == frontend_core.IN_MEMORY
        assert frontend_core.validate_database(database=frontend_core.SQLITE) == frontend_core.SQLITE
        assert frontend_core.validate_database(database=frontend_core.AZURE_SQL) == frontend_core.AZURE_SQL

    def test_validate_database_invalid_value(self):
        """Test validate_database with invalid value."""
        with pytest.raises(ValueError, match="Invalid database type"):
            frontend_core.validate_database(database="InvalidDB")

    def test_validate_log_level_valid_values(self):
        """Test validate_log_level with valid values."""
        assert frontend_core.validate_log_level(log_level="DEBUG") == logging.DEBUG
        assert frontend_core.validate_log_level(log_level="INFO") == logging.INFO
        assert frontend_core.validate_log_level(log_level="warning") == logging.WARNING  # Case-insensitive
        assert frontend_core.validate_log_level(log_level="error") == logging.ERROR
        assert frontend_core.validate_log_level(log_level="CRITICAL") == logging.CRITICAL

    def test_validate_log_level_invalid_value(self):
        """Test validate_log_level with invalid value."""
        with pytest.raises(ValueError, match="Invalid log level"):
            frontend_core.validate_log_level(log_level="INVALID")

    def test_validate_integer_valid(self):
        """Test validate_integer with valid values."""
        assert frontend_core.validate_integer("42") == 42
        assert frontend_core.validate_integer("0") == 0
        assert frontend_core.validate_integer("-5") == -5

    def test_validate_integer_with_min_value(self):
        """Test validate_integer with min_value constraint."""
        assert frontend_core.validate_integer("5", min_value=1) == 5
        assert frontend_core.validate_integer("1", min_value=1) == 1

    def test_validate_integer_below_min_value(self):
        """Test validate_integer below min_value raises ValueError."""
        with pytest.raises(ValueError, match="must be at least"):
            frontend_core.validate_integer("0", min_value=1)

    def test_validate_integer_invalid_string(self):
        """Test validate_integer with non-integer string."""
        with pytest.raises(ValueError, match="must be an integer"):
            frontend_core.validate_integer("not_a_number")

    def test_validate_integer_custom_name(self):
        """Test validate_integer with custom parameter name."""
        with pytest.raises(ValueError, match="max_retries must be an integer"):
            frontend_core.validate_integer("invalid", name="max_retries")

    def test_positive_int_valid(self):
        """Test positive_int with valid values."""
        assert frontend_core.positive_int("1") == 1
        assert frontend_core.positive_int("100") == 100

    def test_positive_int_zero(self):
        """Test positive_int with zero raises error."""
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            frontend_core.positive_int("0")

    def test_positive_int_negative(self):
        """Test positive_int with negative value raises error."""
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            frontend_core.positive_int("-1")

    def test_non_negative_int_valid(self):
        """Test non_negative_int with valid values."""
        assert frontend_core.non_negative_int("0") == 0
        assert frontend_core.non_negative_int("5") == 5

    def test_non_negative_int_negative(self):
        """Test non_negative_int with negative value raises error."""
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            frontend_core.non_negative_int("-1")

    def test_validate_database_argparse(self):
        """Test validate_database_argparse wrapper."""
        assert frontend_core.validate_database_argparse(frontend_core.IN_MEMORY) == frontend_core.IN_MEMORY

        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            frontend_core.validate_database_argparse("InvalidDB")

    def test_validate_log_level_argparse(self):
        """Test validate_log_level_argparse wrapper."""
        assert frontend_core.validate_log_level_argparse("DEBUG") == logging.DEBUG

        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            frontend_core.validate_log_level_argparse("INVALID")


class TestParseMemoryLabels:
    """Tests for parse_memory_labels function."""

    def test_parse_memory_labels_valid(self):
        """Test parsing valid JSON labels."""
        json_str = '{"key1": "value1", "key2": "value2"}'
        result = frontend_core.parse_memory_labels(json_string=json_str)

        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_memory_labels_empty(self):
        """Test parsing empty JSON object."""
        result = frontend_core.parse_memory_labels(json_string="{}")
        assert result == {}

    def test_parse_memory_labels_invalid_json(self):
        """Test parsing invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            frontend_core.parse_memory_labels(json_string="not valid json")

    def test_parse_memory_labels_not_dict(self):
        """Test parsing JSON array raises ValueError."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            frontend_core.parse_memory_labels(json_string='["array", "not", "dict"]')

    def test_parse_memory_labels_non_string_key(self):
        """Test parsing with non-string values raises ValueError."""
        with pytest.raises(ValueError, match="All label keys and values must be strings"):
            frontend_core.parse_memory_labels(json_string='{"key": 123}')


class TestResolveInitializationScripts:
    """Tests for resolve_initialization_scripts function."""

    @patch("pyrit.cli.frontend_core.InitializerRegistry.resolve_script_paths")
    def test_resolve_initialization_scripts(self, mock_resolve: MagicMock):
        """Test resolve_initialization_scripts calls InitializerRegistry."""
        mock_resolve.return_value = [Path("/test/script.py")]

        result = frontend_core.resolve_initialization_scripts(script_paths=["script.py"])

        mock_resolve.assert_called_once_with(script_paths=["script.py"])
        assert result == [Path("/test/script.py")]


class TestListFunctions:
    """Tests for list_scenarios_async and list_initializers_async functions."""

    def test_discover_builtin_scenarios_uses_dotted_names(self):
        """Built-in scenario names should be dotted (package.module) lowercase names."""
        from pyrit.registry.class_registries.scenario_registry import ScenarioRegistry

        registry = ScenarioRegistry()
        registry._discover_builtin_scenarios()

        names = list(registry._class_entries.keys())
        assert len(names) > 0, "Should discover at least one built-in scenario"
        for name in names:
            assert "." in name, f"Scenario name '{name}' should be a dotted name (package.module)"
            assert name == name.lower(), f"Scenario name '{name}' should be lowercase"

    async def test_list_scenarios(self):
        """Test list_scenarios_async returns scenarios from registry."""
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = [{"name": "test_scenario"}]

        context = frontend_core.FrontendCore()
        context._scenario_registry = mock_registry
        context._initialized = True

        result = await frontend_core.list_scenarios_async(context=context)

        assert result == [{"name": "test_scenario"}]
        mock_registry.list_metadata.assert_called_once()

    async def test_list_initializers(self):
        """Test list_initializers_async returns initializers from context registry."""
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = [{"name": "test_init"}]

        context = frontend_core.FrontendCore()
        context._initializer_registry = mock_registry
        context._initialized = True

        result = await frontend_core.list_initializers_async(context=context)

        assert result == [{"name": "test_init"}]
        mock_registry.list_metadata.assert_called_once()


class TestPrintFunctions:
    """Tests for print functions."""

    async def test_print_scenarios_list_with_scenarios(self, capsys):
        """Test print_scenarios_list with scenarios."""
        context = frontend_core.FrontendCore()
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = [
            ScenarioMetadata(
                class_name="TestScenario",
                class_module="test.scenarios",
                class_description="Test description",
                registry_name="test",
                default_strategy="default",
                all_strategies=(),
                aggregate_strategies=(),
                default_datasets=(),
                max_dataset_size=None,
            )
        ]
        context._scenario_registry = mock_registry
        context._initialized = True

        result = await frontend_core.print_scenarios_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "Available Scenarios" in captured.out
        assert "test" in captured.out

    async def test_print_scenarios_list_empty(self, capsys):
        """Test print_scenarios_list with no scenarios."""
        context = frontend_core.FrontendCore()
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = []
        context._scenario_registry = mock_registry
        context._initialized = True

        result = await frontend_core.print_scenarios_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "No scenarios found" in captured.out

    async def test_print_initializers_list_with_initializers(self, capsys):
        """Test print_initializers_list_async with initializers."""
        context = frontend_core.FrontendCore()
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = [
            InitializerMetadata(
                class_name="TestInit",
                class_module="test.initializers",
                class_description="Test initializer",
                registry_name="test",
                display_name="test",
                execution_order=100,
                required_env_vars=(),
            )
        ]
        context._initializer_registry = mock_registry
        context._initialized = True

        result = await frontend_core.print_initializers_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "Available Initializers" in captured.out
        assert "test" in captured.out

    async def test_print_initializers_list_empty(self, capsys):
        """Test print_initializers_list_async with no initializers."""
        context = frontend_core.FrontendCore()
        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = []
        context._initializer_registry = mock_registry
        context._initialized = True

        result = await frontend_core.print_initializers_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "No initializers found" in captured.out


class TestFormatFunctions:
    """Tests for format_scenario_metadata and format_initializer_metadata."""

    def test_format_scenario_metadata_basic(self, capsys):
        """Test format_scenario_metadata with basic metadata."""

        scenario_metadata = ScenarioMetadata(
            class_name="TestScenario",
            class_module="test.scenarios",
            class_description="",
            registry_name="test",
            default_strategy="",
            all_strategies=(),
            aggregate_strategies=(),
            default_datasets=(),
            max_dataset_size=None,
        )

        frontend_core.format_scenario_metadata(scenario_metadata=scenario_metadata)

        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "TestScenario" in captured.out

    def test_format_scenario_metadata_with_description(self, capsys):
        """Test format_scenario_metadata with description."""

        scenario_metadata = ScenarioMetadata(
            class_name="TestScenario",
            class_module="test.scenarios",
            class_description="This is a test scenario",
            registry_name="test",
            default_strategy="",
            all_strategies=(),
            aggregate_strategies=(),
            default_datasets=(),
            max_dataset_size=None,
        )

        frontend_core.format_scenario_metadata(scenario_metadata=scenario_metadata)

        captured = capsys.readouterr()
        assert "This is a test scenario" in captured.out

    def test_format_scenario_metadata_with_strategies(self, capsys):
        """Test format_scenario_metadata with strategies."""
        scenario_metadata = ScenarioMetadata(
            class_name="TestScenario",
            class_module="test.scenarios",
            class_description="",
            registry_name="test",
            default_strategy="strategy1",
            all_strategies=("strategy1", "strategy2"),
            aggregate_strategies=(),
            default_datasets=(),
            max_dataset_size=None,
        )

        frontend_core.format_scenario_metadata(scenario_metadata=scenario_metadata)

        captured = capsys.readouterr()
        assert "strategy1" in captured.out
        assert "strategy2" in captured.out
        assert "Default Strategy" in captured.out

    def test_format_initializer_metadata_basic(self, capsys) -> None:
        """Test format_initializer_metadata with basic metadata."""
        initializer_metadata = InitializerMetadata(
            class_name="TestInit",
            class_module="test.initializers",
            class_description="",
            registry_name="test",
            display_name="test",
            required_env_vars=(),
            execution_order=100,
        )

        frontend_core.format_initializer_metadata(initializer_metadata=initializer_metadata)

        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "TestInit" in captured.out
        assert "100" in captured.out

    def test_format_initializer_metadata_with_env_vars(self, capsys) -> None:
        """Test format_initializer_metadata with environment variables."""
        initializer_metadata = InitializerMetadata(
            class_name="TestInit",
            class_module="test.initializers",
            class_description="",
            registry_name="test",
            display_name="test",
            required_env_vars=("VAR1", "VAR2"),
            execution_order=100,
        )

        frontend_core.format_initializer_metadata(initializer_metadata=initializer_metadata)

        captured = capsys.readouterr()
        assert "VAR1" in captured.out
        assert "VAR2" in captured.out

    def test_format_initializer_metadata_with_description(self, capsys) -> None:
        """Test format_initializer_metadata with description."""
        initializer_metadata = InitializerMetadata(
            class_name="TestInit",
            class_module="test.initializers",
            class_description="Test description",
            registry_name="test",
            display_name="test",
            required_env_vars=(),
            execution_order=100,
        )

        frontend_core.format_initializer_metadata(initializer_metadata=initializer_metadata)

        captured = capsys.readouterr()
        assert "Test description" in captured.out


class TestParseInitializerArg:
    """Tests for _parse_initializer_arg function."""

    def test_simple_name_returns_string(self) -> None:
        """Test that a plain name without ':' returns the string as-is."""
        assert frontend_core._parse_initializer_arg("simple") == "simple"

    def test_name_with_single_param(self) -> None:
        """Test name:key=value parsing."""
        result = frontend_core._parse_initializer_arg("target:tags=default")
        assert result == {"name": "target", "args": {"tags": ["default"]}}

    def test_name_with_comma_separated_values(self) -> None:
        """Test that comma-separated values are split into a list."""
        result = frontend_core._parse_initializer_arg("target:tags=default,scorer")
        assert result == {"name": "target", "args": {"tags": ["default", "scorer"]}}

    def test_name_with_multiple_params(self) -> None:
        """Test semicolon-separated multiple params."""
        result = frontend_core._parse_initializer_arg("target:tags=default;mode=strict")
        assert result == {"name": "target", "args": {"tags": ["default"], "mode": ["strict"]}}

    def test_missing_name_before_colon_raises(self) -> None:
        """Test that ':key=val' with no name raises ValueError."""
        with pytest.raises(ValueError, match="missing name before ':'"):
            frontend_core._parse_initializer_arg(":tags=default")

    def test_missing_equals_in_param_raises(self) -> None:
        """Test that 'name:badparam' without '=' raises ValueError."""
        with pytest.raises(ValueError, match="expected key=value format"):
            frontend_core._parse_initializer_arg("target:badparam")

    def test_empty_key_raises(self) -> None:
        """Test that 'name:=value' with empty key raises ValueError."""
        with pytest.raises(ValueError, match="empty key"):
            frontend_core._parse_initializer_arg("target:=value")

    def test_colon_but_no_params_returns_string(self) -> None:
        """Test that 'name:' with trailing colon but no params returns the name string."""
        result = frontend_core._parse_initializer_arg("target:")
        assert result == "target"


class TestParseRunArguments:
    """Tests for parse_run_arguments function."""

    def test_parse_run_arguments_basic(self):
        """Test parsing basic scenario name."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario")

        assert result["scenario_name"] == "test_scenario"
        assert result["initializers"] is None
        assert result["scenario_strategies"] is None

    def test_parse_run_arguments_with_initializers(self):
        """Test parsing with initializers."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --initializers init1 init2")

        assert result["scenario_name"] == "test_scenario"
        assert result["initializers"] == ["init1", "init2"]

    def test_parse_run_arguments_with_initializer_params(self):
        """Test parsing initializers with key=value params."""
        result = frontend_core.parse_run_arguments(
            args_string="test_scenario --initializers simple target:tags=default"
        )

        assert result["initializers"][0] == "simple"
        assert result["initializers"][1] == {"name": "target", "args": {"tags": ["default"]}}

    def test_parse_run_arguments_with_initializer_multiple_params(self):
        """Test parsing initializers with multiple key=value params separated by semicolons."""
        result = frontend_core.parse_run_arguments(
            args_string="test_scenario --initializers target:tags=default;mode=strict"
        )

        assert result["initializers"][0] == {"name": "target", "args": {"tags": ["default"], "mode": ["strict"]}}

    def test_parse_run_arguments_with_initializer_comma_list(self):
        """Test parsing initializer params with comma-separated values into lists."""
        result = frontend_core.parse_run_arguments(
            args_string="test_scenario --initializers target:tags=default,scorer"
        )

        assert result["initializers"][0] == {"name": "target", "args": {"tags": ["default", "scorer"]}}

    def test_parse_run_arguments_with_strategies(self):
        """Test parsing with strategies."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --strategies s1 s2")

        assert result["scenario_strategies"] == ["s1", "s2"]

    def test_parse_run_arguments_with_short_strategies(self):
        """Test parsing with -s flag."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario -s s1 s2")

        assert result["scenario_strategies"] == ["s1", "s2"]

    def test_parse_run_arguments_with_max_concurrency(self):
        """Test parsing with max-concurrency."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --max-concurrency 5")

        assert result["max_concurrency"] == 5

    def test_parse_run_arguments_with_max_retries(self):
        """Test parsing with max-retries."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --max-retries 3")

        assert result["max_retries"] == 3

    def test_parse_run_arguments_with_memory_labels(self):
        """Test parsing with memory-labels."""
        result = frontend_core.parse_run_arguments(args_string='test_scenario --memory-labels {"key":"value"}')

        assert result["memory_labels"] == {"key": "value"}

    def test_parse_run_arguments_with_log_level(self):
        """Test parsing with log-level override."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --log-level DEBUG")

        assert result["log_level"] == logging.DEBUG

    def test_parse_run_arguments_with_initialization_scripts(self):
        """Test parsing with initialization-scripts."""
        result = frontend_core.parse_run_arguments(
            args_string="test_scenario --initialization-scripts script1.py script2.py"
        )

        assert result["initialization_scripts"] == ["script1.py", "script2.py"]

    def test_parse_run_arguments_complex(self):
        """Test parsing complex argument combination."""
        args = "test_scenario --initializers init1 --strategies s1 s2 --max-concurrency 10"
        result = frontend_core.parse_run_arguments(args_string=args)

        assert result["scenario_name"] == "test_scenario"
        assert result["initializers"] == ["init1"]
        assert result["scenario_strategies"] == ["s1", "s2"]
        assert result["max_concurrency"] == 10

    def test_parse_run_arguments_empty_raises(self):
        """Test parsing empty string raises ValueError."""
        with pytest.raises(ValueError, match="No scenario name provided"):
            frontend_core.parse_run_arguments(args_string="")

    def test_parse_run_arguments_invalid_max_concurrency(self):
        """Test parsing with invalid max-concurrency."""
        with pytest.raises(ValueError):
            frontend_core.parse_run_arguments(args_string="test_scenario --max-concurrency 0")

    def test_parse_run_arguments_invalid_max_retries(self):
        """Test parsing with invalid max-retries."""
        with pytest.raises(ValueError):
            frontend_core.parse_run_arguments(args_string="test_scenario --max-retries -1")

    def test_parse_run_arguments_missing_value(self):
        """Test parsing with missing argument value."""
        with pytest.raises(ValueError, match="requires a value"):
            frontend_core.parse_run_arguments(args_string="test_scenario --max-concurrency")


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
class TestRunScenarioAsync:
    """Tests for run_scenario_async function."""

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_basic(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running a basic scenario."""
        # Mock context
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        # Run scenario
        result = await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
        )

        assert result == mock_result
        # Verify scenario was instantiated with no arguments (runtime params go to initialize_async)
        mock_scenario_class.assert_called_once_with()
        mock_scenario_instance.initialize_async.assert_called_once_with()
        mock_scenario_instance.run_async.assert_called_once()
        mock_printer.print_summary_async.assert_called_once_with(mock_result)

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_run_scenario_async_not_found(self, mock_init: AsyncMock):
        """Test running non-existent scenario raises ValueError."""
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_registry.get_class.return_value = None
        mock_scenario_registry.get_names.return_value = ["other_scenario"]

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        with pytest.raises(ValueError, match="Scenario 'test_scenario' not found"):
            await frontend_core.run_scenario_async(
                scenario_name="test_scenario",
                context=context,
            )

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_with_strategies(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running scenario with strategies."""
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        # Mock strategy enum
        from enum import Enum

        class MockStrategy(Enum):
            strategy1 = "strategy1"

        mock_scenario_class.get_strategy_class.return_value = MockStrategy
        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        # Run with strategies
        await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
            scenario_strategies=["strategy1"],
        )

        # Verify scenario was instantiated with no arguments
        mock_scenario_class.assert_called_once_with()
        # Verify strategy was passed to initialize_async
        call_kwargs = mock_scenario_instance.initialize_async.call_args[1]
        assert "scenario_strategies" in call_kwargs

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_with_initializers(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running scenario with initializers."""
        context = frontend_core.FrontendCore(initializer_names=["test_init"])
        mock_scenario_registry = MagicMock()
        mock_initializer_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        mock_initializer_class = MagicMock()
        mock_initializer_registry.get_class.return_value = mock_initializer_class

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = mock_initializer_registry
        context._initialized = True

        # Run with initializers
        await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
        )

        # Verify initializer was retrieved
        mock_initializer_registry.get_class.assert_called_once_with("test_init")

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_with_max_concurrency(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running scenario with max_concurrency."""
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        # Run with max_concurrency
        await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
            max_concurrency=5,
        )

        # Verify scenario was instantiated with no arguments
        mock_scenario_class.assert_called_once_with()
        # Verify max_concurrency was passed to initialize_async
        call_kwargs = mock_scenario_instance.initialize_async.call_args[1]
        assert call_kwargs["max_concurrency"] == 5

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_without_print_summary(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running scenario without printing summary."""
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        # Run without printing
        await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
            print_summary=False,
        )

        # Verify printer was not called
        assert mock_printer.print_summary_async.call_count == 0


class TestArgHelp:
    """Tests for frontend_core.ARG_HELP dictionary."""

    def test_arg_help_contains_all_keys(self):
        """Test frontend_core.ARG_HELP contains expected keys."""
        expected_keys = [
            "initializers",
            "initialization_scripts",
            "scenario_strategies",
            "max_concurrency",
            "max_retries",
            "memory_labels",
            "database",
            "log_level",
            "target",
        ]

        for key in expected_keys:
            assert key in frontend_core.ARG_HELP
            assert isinstance(frontend_core.ARG_HELP[key], str)
            assert len(frontend_core.ARG_HELP[key]) > 0


class TestParseRunArgumentsTarget:
    """Tests for --target parsing in parse_run_arguments."""

    def test_parse_run_arguments_with_target(self):
        """Test parsing with --target."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario --target my_target")

        assert result["target"] == "my_target"

    def test_parse_run_arguments_target_with_other_args(self):
        """Test parsing --target alongside other arguments."""
        result = frontend_core.parse_run_arguments(
            args_string="test_scenario --target my_target --initializers init1 --max-concurrency 5"
        )

        assert result["target"] == "my_target"
        assert result["initializers"] == ["init1"]
        assert result["max_concurrency"] == 5

    def test_parse_run_arguments_target_missing_value(self):
        """Test parsing --target without a value raises ValueError."""
        with pytest.raises(ValueError, match="--target requires a value"):
            frontend_core.parse_run_arguments(args_string="test_scenario --target")

    def test_parse_run_arguments_no_target(self):
        """Test parsing without --target returns None."""
        result = frontend_core.parse_run_arguments(args_string="test_scenario")

        assert result["target"] is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
class TestRunScenarioAsyncTarget:
    """Tests for target resolution in run_scenario_async."""

    @patch("pyrit.cli.frontend_core.TargetRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_with_valid_target(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
        mock_target_registry_class: MagicMock,
    ):
        """Test running scenario with a valid target name resolves from registry."""
        # Setup mocks
        mock_target = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_instance_by_name.return_value = mock_target
        mock_target_registry_class.get_registry_singleton.return_value = mock_registry

        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        result = await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
            target_name="my_target",
        )

        assert result == mock_result
        mock_registry.get_instance_by_name.assert_called_once_with("my_target")
        # Verify objective_target was passed to initialize_async
        call_kwargs = mock_scenario_instance.initialize_async.call_args[1]
        assert call_kwargs["objective_target"] is mock_target

    @patch("pyrit.cli.frontend_core.TargetRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_run_scenario_async_with_invalid_target(
        self,
        mock_init: AsyncMock,
        mock_target_registry_class: MagicMock,
    ):
        """Test running scenario with an invalid target name raises ValueError."""
        mock_registry = MagicMock()
        mock_registry.get_instance_by_name.return_value = None
        mock_registry.get_names.return_value = ["target_a", "target_b"]
        mock_target_registry_class.get_registry_singleton.return_value = mock_registry

        context = frontend_core.FrontendCore()
        context._scenario_registry = MagicMock()
        context._initializer_registry = MagicMock()
        context._initialized = True

        with pytest.raises(ValueError, match="Target 'bad_target' not found in registry"):
            await frontend_core.run_scenario_async(
                scenario_name="test_scenario",
                context=context,
                target_name="bad_target",
            )

    @patch("pyrit.cli.frontend_core.TargetRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_run_scenario_async_with_empty_target_registry(
        self,
        mock_init: AsyncMock,
        mock_target_registry_class: MagicMock,
    ):
        """Test running scenario with target name when registry is empty gives helpful error."""
        mock_registry = MagicMock()
        mock_registry.get_instance_by_name.return_value = None
        mock_registry.get_names.return_value = []
        mock_target_registry_class.get_registry_singleton.return_value = mock_registry

        context = frontend_core.FrontendCore()
        context._scenario_registry = MagicMock()
        context._initializer_registry = MagicMock()
        context._initialized = True

        with pytest.raises(ValueError, match="target registry is empty"):
            await frontend_core.run_scenario_async(
                scenario_name="test_scenario",
                context=context,
                target_name="my_target",
            )

    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    @patch("pyrit.cli.frontend_core.ConsoleScenarioResultPrinter")
    async def test_run_scenario_async_without_target(
        self,
        mock_printer_class: MagicMock,
        mock_init: AsyncMock,
    ):
        """Test running scenario without target_name does not add objective_target to kwargs."""
        context = frontend_core.FrontendCore()
        mock_scenario_registry = MagicMock()
        mock_scenario_class = MagicMock()
        mock_scenario_instance = MagicMock()
        mock_result = MagicMock()
        mock_printer = MagicMock()
        mock_printer.print_summary_async = AsyncMock()

        mock_scenario_instance.initialize_async = AsyncMock()
        mock_scenario_instance.run_async = AsyncMock(return_value=mock_result)
        mock_scenario_class.return_value = mock_scenario_instance
        mock_scenario_registry.get_class.return_value = mock_scenario_class
        mock_printer_class.return_value = mock_printer

        context._scenario_registry = mock_scenario_registry
        context._initializer_registry = MagicMock()
        context._initialized = True

        await frontend_core.run_scenario_async(
            scenario_name="test_scenario",
            context=context,
        )

        # Verify no objective_target was passed
        call_kwargs = mock_scenario_instance.initialize_async.call_args[1]
        assert "objective_target" not in call_kwargs


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
class TestPrintTargetsList:
    """Tests for print_targets_list_async function."""

    @patch("pyrit.cli.frontend_core.TargetRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_print_targets_list_with_targets(
        self,
        mock_init: AsyncMock,
        mock_target_registry_class: MagicMock,
        capsys,
    ):
        """Test print_targets_list_async displays target names."""
        mock_registry = MagicMock()
        mock_registry.get_names.return_value = ["target_a", "target_b"]
        mock_target_registry_class.get_registry_singleton.return_value = mock_registry

        context = frontend_core.FrontendCore()
        context._scenario_registry = MagicMock()
        context._initializer_registry = MagicMock()
        context._initialized = True

        result = await frontend_core.print_targets_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "target_a" in captured.out
        assert "target_b" in captured.out
        assert "Total targets: 2" in captured.out

    @patch("pyrit.cli.frontend_core.TargetRegistry")
    @patch("pyrit.cli.frontend_core.initialize_pyrit_async", new_callable=AsyncMock)
    async def test_print_targets_list_empty(
        self,
        mock_init: AsyncMock,
        mock_target_registry_class: MagicMock,
        capsys,
    ):
        """Test print_targets_list_async with no targets gives helpful hint."""
        mock_registry = MagicMock()
        mock_registry.get_names.return_value = []
        mock_target_registry_class.get_registry_singleton.return_value = mock_registry

        context = frontend_core.FrontendCore()
        context._scenario_registry = MagicMock()
        context._initializer_registry = MagicMock()
        context._initialized = True

        result = await frontend_core.print_targets_list_async(context=context)

        assert result == 0
        captured = capsys.readouterr()
        assert "No targets found" in captured.out
        assert "--initializers target" in captured.out
