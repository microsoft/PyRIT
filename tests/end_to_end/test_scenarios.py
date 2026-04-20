# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end tests for PyRIT scenarios using pyrit_scan CLI.

These tests dynamically discover all available scenarios and run each one
using the pyrit_scan command with standard initializers.
"""

import pytest

from pyrit.cli.pyrit_scan import main as pyrit_scan_main
from pyrit.registry import ScenarioRegistry


def get_all_scenarios():
    """
    Dynamically discover all available scenarios from the scenario registry.

    Returns:
        List[str]: Sorted list of scenario names.
    """
    registry = ScenarioRegistry.get_registry_singleton()
    return registry.get_names()


@pytest.mark.timeout(7200)  # 2 hour timeout per scenario
@pytest.mark.parametrize("scenario_name", get_all_scenarios())
def test_scenario_with_pyrit_scan(scenario_name):
    """
    Test each scenario runs successfully using pyrit_scan with standard initializers.

    Args:
        scenario_name: Name of the scenario to test (dynamically discovered).
    """
    try:
        result = pyrit_scan_main(
            [
                scenario_name,
                "--initializers",
                "openai_objective_target",
                "load_default_datasets",
                "--database",
                "InMemory",
                "--log-level",
                "WARNING",
            ]
        )

        assert result == 0, f"Scenario '{scenario_name}' failed with exit code {result}"

    except Exception as e:
        # Re-raise with scenario context while preserving full traceback
        raise AssertionError(f"Scenario '{scenario_name}' raised an exception") from e
