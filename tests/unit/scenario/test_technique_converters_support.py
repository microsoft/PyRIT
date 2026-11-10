# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenarios either apply ``technique_converters`` or don't accept it, so it's never silently ignored."""

from unittest.mock import MagicMock

import pytest

from pyrit.converter import Base64Converter
from pyrit.models import ComponentIdentifier
from pyrit.scenario.scenarios.adaptive import AdaptiveScenario, TextAdaptive
from pyrit.scenario.scenarios.airt import Cyber, Psychosocial, Scam
from pyrit.scenario.scenarios.benchmark import AdversarialBenchmark
from pyrit.scenario.scenarios.foundry import RedTeamAgent
from pyrit.scenario.scenarios.garak import (
    AudioAchillesHeel,
    Doctor,
    Encoding,
    PackageHallucination,
    SystemPromptExtraction,
    WebInjection,
)
from pyrit.scenario.scenarios.technique import Execute
from pyrit.score import TrueFalseScorer

_IGNORING_SCENARIOS = [
    AdaptiveScenario,
    TextAdaptive,
    AudioAchillesHeel,
    Encoding,
    PackageHallucination,
    Psychosocial,
    RedTeamAgent,
    Scam,
    SystemPromptExtraction,
    WebInjection,
]


def _declares_technique_converters(scenario_class: type) -> bool:
    return any(parameter.name == "technique_converters" for parameter in scenario_class.supported_parameters())


@pytest.mark.parametrize("scenario_class", _IGNORING_SCENARIOS, ids=lambda cls: cls.__name__)
def test_scenarios_that_dont_apply_technique_converters_dont_declare_them(scenario_class: type) -> None:
    assert not _declares_technique_converters(scenario_class)


@pytest.mark.parametrize("scenario_class", [AdversarialBenchmark, Cyber, Doctor, Execute], ids=lambda cls: cls.__name__)
def test_scenarios_that_apply_technique_converters_declare_them(scenario_class: type) -> None:
    assert _declares_technique_converters(scenario_class)


@pytest.mark.usefixtures("patch_central_database")
def test_passing_technique_converters_to_a_scenario_that_ignores_them_raises() -> None:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="Scorer", class_module="test")
    scenario = Encoding(objective_scorer=scorer)

    with pytest.raises(ValueError, match="unknown parameter.*technique_converters"):
        scenario.set_params_from_args(args={"technique_converters": {"base64": [Base64Converter()]}})
