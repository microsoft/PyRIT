# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validate scenario E2E launch settings without a backend or live targets."""

from unittest.mock import patch

import pytest
from end_to_end import test_scenarios as scenario_tests

from pyrit.cli.pyrit_scan import _build_run_request, parse_args
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial
from pyrit.score import SubStringScorer


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("scenario_name", "expected_max_total"),
    [
        ("airt.psychosocial", 2),
        ("garak.latent_injection", 23),
        ("garak.prompt_inject", 3),
        ("garak.web_injection", 6),
        ("garak.encoding", 1),
        ("technique.execute", None),
    ],
)
def test_e2e_launch_uses_bounded_supported_dataset_flags(
    *, scenario_name: str, expected_max_total: int | None, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch.object(scenario_tests, "pyrit_scan_main", return_value=0) as run:
        scenario_tests.test_scenario_with_pyrit_scan(scenario_name=scenario_name, capsys=capsys)
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[:2] == ["run", scenario_name]
    assert "--max-dataset-size" not in argv
    request = _build_run_request(parsed_args=parse_args(argv), scenario_name=scenario_name)
    if expected_max_total is None:
        assert request.datasets is None
        assert "--max-total" not in argv
    else:
        assert request.datasets is not None
        assert request.datasets.max_total == expected_max_total


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_name", list(scenario_tests.SCENARIO_MAX_TOTAL))
async def test_e2e_coverage_limits_pass_real_scenario_validation_async(scenario_name: str) -> None:
    scenario_class = ScenarioRegistry.get_registry_singleton().get_class(scenario_name)
    if scenario_class is Psychosocial:
        scenario = Psychosocial(
            imminent_crisis_scorer=SubStringScorer(substring="crisis"),
            licensed_therapist_scorer=SubStringScorer(substring="therapist"),
        )
    else:
        scenario = scenario_class()
    scenario.set_params_from_args(
        args={"dataset_overrides": {"max_total": scenario_tests.SCENARIO_MAX_TOTAL[scenario_name]}}
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.effective_datasets is not None
    assert estimate.effective_datasets.max_total == scenario_tests.SCENARIO_MAX_TOTAL[scenario_name]
