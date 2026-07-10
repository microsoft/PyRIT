# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end tests for PyRIT scenarios using pyrit_scan CLI.

These tests dynamically discover all available scenarios and run each one
using the pyrit_scan command. Most scenarios run with the
``DEFAULT_INITIALIZERS`` list; scenarios that need additional setup
declare their full initializer list in ``SCENARIO_INITIALIZERS`` and
extra CLI args in ``SCENARIO_EXTRA_ARGS``.

Note: e2e tests are not part of CI; they run via ``make end-to-end-test``
on developer machines that have the appropriate env vars set
(``ADVERSARIAL_CHAT_*`` for the benchmark scenario, in particular). The
benchmark scenario reads its adversarial targets from ``--adversarial-targets``,
which resolves names via ``TargetRegistry`` (populated by
``TargetInitializer`` from those env vars).
"""

from pathlib import Path

import pytest

from pyrit.cli.pyrit_scan import main as pyrit_scan_main
from pyrit.registry import ScenarioRegistry

CONFIG_FILE = Path(__file__).parent / "test_config.yaml"

#: Initializers run for every scenario unless overridden in ``SCENARIO_INITIALIZERS``.
#: ``target`` populates ``TargetRegistry`` from env vars; ``load_default_datasets``
#: fetches each scenario's declared default datasets into memory.
DEFAULT_INITIALIZERS: list[str] = ["target", "load_default_datasets"]

#: Per-scenario override map for initializers. A scenario absent here falls back
#: to ``DEFAULT_INITIALIZERS``. Keys use the dotted registry name
#: (``<module>.<scenario>``) returned by ``ScenarioRegistry.get_class_names()``.
SCENARIO_INITIALIZERS: dict[str, list[str]] = {}

#: Per-scenario extra CLI args appended after the standard flag block. Keys use
#: the same dotted registry name as ``SCENARIO_INITIALIZERS``. Values are
#: lists already split into argv tokens.
SCENARIO_EXTRA_ARGS: dict[str, list[str]] = {
    # benchmark.adversarial requires --adversarial-targets at run time
    # (see AdversarialBenchmark.supported_parameters); without it the scenario
    # raises ValueError before any attack is built.
    "benchmark.adversarial": ["--adversarial-targets", "adversarial_chat"],
}


def get_all_scenarios():
    """
    Dynamically discover all available scenarios from the scenario registry.

    Returns:
        list[str]: Sorted list of scenario names.
    """
    registry = ScenarioRegistry.get_registry_singleton()
    return registry.get_class_names()


def _initializers_for(scenario_name: str) -> list[str]:
    """Return the initializer name list for ``scenario_name``, defaulting to ``DEFAULT_INITIALIZERS``."""
    return SCENARIO_INITIALIZERS.get(scenario_name, DEFAULT_INITIALIZERS)


def _extra_args_for(scenario_name: str) -> list[str]:
    """Return scenario-specific extra CLI argv tokens, defaulting to none."""
    return SCENARIO_EXTRA_ARGS.get(scenario_name, [])


@pytest.mark.timeout(7200)  # 2 hour timeout per scenario
@pytest.mark.flaky(reruns=3, reruns_delay=90)
@pytest.mark.parametrize("scenario_name", get_all_scenarios())
def test_scenario_with_pyrit_scan(scenario_name):
    """
    Test each scenario runs successfully using pyrit_scan with its declared initializer list.

    Args:
        scenario_name: Name of the scenario to test (dynamically discovered).
    """
    initializers = _initializers_for(scenario_name)
    extra_args = _extra_args_for(scenario_name)
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
                *extra_args,
            ]
        )

        assert result == 0, f"Scenario '{scenario_name}' failed with exit code {result}"

    except Exception as e:
        # Re-raise with scenario context while preserving full traceback
        raise AssertionError(f"Scenario '{scenario_name}' raised an exception") from e
