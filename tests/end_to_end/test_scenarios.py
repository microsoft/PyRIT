# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end tests for PyRIT scenarios using pyrit_scan CLI.

These tests dynamically discover all available scenarios and run each one
using the pyrit_scan command. Most scenarios run with the
:data:`DEFAULT_INITIALIZERS` list; scenarios that need additional setup
(e.g. ``benchmark.adversarial`` needs ``BenchmarkInitializer`` to fan
adversarial techniques out across registry-discovered targets) declare
their full initializer list in :data:`SCENARIO_INITIALIZERS`.

Note: e2e tests are not part of CI; they run via ``make end-to-end-test``
on developer machines that have the appropriate env vars set
(``ADVERSARIAL_CHAT_*`` for the benchmark scenario, in particular).
``BenchmarkInitializer`` surfaces a clear error pointing at the env vars
when they are absent.
"""

from pathlib import Path

import pytest

from pyrit.cli.pyrit_scan import main as pyrit_scan_main
from pyrit.registry import ScenarioRegistry

CONFIG_FILE = Path(__file__).parent / "test_config.yaml"

#: Initializers run for every scenario unless overridden in :data:`SCENARIO_INITIALIZERS`.
#: ``target`` populates ``TargetRegistry`` from env vars; ``load_default_datasets``
#: fetches each scenario's declared default datasets into memory.
DEFAULT_INITIALIZERS: list[str] = ["target", "load_default_datasets"]

#: Per-scenario override map. A scenario named here uses this list verbatim
#: (no implicit merge with ``DEFAULT_INITIALIZERS``); a scenario absent here
#: falls back to ``DEFAULT_INITIALIZERS``. Keys use the dotted registry name
#: (``<module>.<scenario>``) returned by ``ScenarioRegistry.get_names()``.
SCENARIO_INITIALIZERS: dict[str, list[str]] = {
    # benchmark.adversarial depends on BenchmarkInitializer to fan
    # adversarial-capable scenario techniques out across every
    # ADVERSARIAL-tagged target in TargetRegistry. Without the
    # benchmark initializer, the scenario's strategy enum is empty.
    "benchmark.adversarial": [*DEFAULT_INITIALIZERS, "benchmark"],
}


def get_all_scenarios():
    """
    Dynamically discover all available scenarios from the scenario registry.

    Returns:
        List[str]: Sorted list of scenario names.
    """
    registry = ScenarioRegistry.get_registry_singleton()
    return registry.get_names()


def _initializers_for(scenario_name: str) -> list[str]:
    """Return the initializer name list for ``scenario_name``, defaulting to ``DEFAULT_INITIALIZERS``."""
    return SCENARIO_INITIALIZERS.get(scenario_name, DEFAULT_INITIALIZERS)


@pytest.mark.timeout(7200)  # 2 hour timeout per scenario
@pytest.mark.parametrize("scenario_name", get_all_scenarios())
def test_scenario_with_pyrit_scan(scenario_name):
    """
    Test each scenario runs successfully using pyrit_scan with its declared initializer list.

    Args:
        scenario_name: Name of the scenario to test (dynamically discovered).
    """
    initializers = _initializers_for(scenario_name)
    try:
        result = pyrit_scan_main(
            [
                scenario_name,
                "--initializers",
                *initializers,
                "--target",
                "openai_chat",
                "--config-file",
                str(CONFIG_FILE),
                "--max-dataset-size",
                "1",
                "--log-level",
                "WARNING",
            ]
        )

        assert result == 0, f"Scenario '{scenario_name}' failed with exit code {result}"

    except Exception as e:
        # Re-raise with scenario context while preserving full traceback
        raise AssertionError(f"Scenario '{scenario_name}' raised an exception") from e
