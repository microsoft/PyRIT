# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenarios either apply ``technique_converters`` or don't accept it, so it's never silently ignored."""

from unittest.mock import MagicMock

import pytest

from pyrit.converter import Base64Converter
from pyrit.models import ComponentIdentifier
from pyrit.scenario.scenarios.adaptive.adaptive_scenario import AdaptiveScenario
from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt.cyber import Cyber
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial
from pyrit.scenario.scenarios.airt.scam import Scam
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.scenario.scenarios.foundry.red_team_agent import RedTeamAgent
from pyrit.scenario.scenarios.garak.audio_achilles_heel import AudioAchillesHeel
from pyrit.scenario.scenarios.garak.doctor import Doctor
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination
from pyrit.scenario.scenarios.garak.system_prompt_extraction import SystemPromptExtraction
from pyrit.scenario.scenarios.garak.web_injection import WebInjection
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


@pytest.mark.parametrize("scenario_class", [AdversarialBenchmark, Cyber, Doctor], ids=lambda cls: cls.__name__)
def test_scenarios_that_apply_technique_converters_declare_them(scenario_class: type) -> None:
    assert _declares_technique_converters(scenario_class)


@pytest.mark.usefixtures("patch_central_database")
def test_passing_technique_converters_to_a_scenario_that_ignores_them_raises() -> None:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="Scorer", class_module="test")
    scenario = Encoding(objective_scorer=scorer)

    with pytest.raises(ValueError, match="unknown parameter.*technique_converters"):
        scenario.set_params_from_args(args={"technique_converters": {"base64": [Base64Converter()]}})
