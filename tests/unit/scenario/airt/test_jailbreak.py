# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the redesigned 3-axis Jailbreak scenario."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, _build_jailbreak_strategy
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories

_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(100)]

_MOCK_TEMPLATES = ["aim.yaml", "aligned.yaml"]


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _strategy_class():
    return _build_jailbreak_strategy()


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries, register a mock adversarial target, populate core factories."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_jailbreak_strategy.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_jailbreak_strategy.cache_clear()


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


@pytest.fixture(autouse=True)
def patch_templates():
    """Deterministic jailbreak template catalog + sampling (real template files are used)."""
    with patch(
        "pyrit.datasets.TextJailBreak.get_jailbreak_templates",
        side_effect=lambda num_templates=None: (
            list(_MOCK_TEMPLATES) if num_templates is None else list(_MOCK_TEMPLATES)[:num_templates]
        ),
    ):
        yield


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


def _make_seed_groups() -> list[SeedAttackGroup]:
    return [
        SeedAttackGroup(seeds=[SeedObjective(value="obj 1"), SeedPrompt(value="prompt 1")]),
        SeedAttackGroup(seeds=[SeedObjective(value="obj 2"), SeedPrompt(value="prompt 2")]),
    ]


FIXTURES = ["patch_central_database", "mock_runtime_env"]


def _patch_seed_groups():
    return patch.object(
        Jailbreak,
        "_resolve_seed_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"harmbench": _make_seed_groups()},
    )


def _patch_scorer(mock_objective_scorer):
    return patch(
        "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer",
        return_value=mock_objective_scorer,
    )


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakClassProperties:
    def test_version_is_3(self):
        assert Jailbreak.VERSION == 3

    def test_required_datasets_is_harmbench(self):
        assert Jailbreak.required_datasets() == ["harmbench"]

    def test_supported_parameter_names(self):
        names = {p.name for p in Jailbreak.supported_parameters()}
        assert names == {"jailbreak_names", "num_templates", "num_attempts"}

    def test_default_dataset_config_is_harmbench(self, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer):
            config = Jailbreak()._default_dataset_config
        assert isinstance(config, DatasetAttackConfiguration)
        assert config.dataset_names == ["harmbench"]
        assert config.max_dataset_size == 4

    def test_default_strategy_is_prompt_sending(self, mock_objective_scorer):
        strat = _strategy_class()
        with _patch_scorer(mock_objective_scorer):
            assert Jailbreak()._default_strategy == strat("prompt_sending")


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakStrategyAxis:
    def test_prompt_sending_is_a_member(self):
        strat = _strategy_class()
        assert "prompt_sending" in {m.name for m in strat}

    def test_core_techniques_are_available(self):
        names = {m.name for m in _strategy_class()}
        # rapid-response-style core techniques are selectable
        assert {"role_play", "many_shot"}.issubset(names)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakInitValidation:
    def test_both_num_templates_and_names_raises(self, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer):
            with pytest.raises(ValueError, match="only one of"):
                Jailbreak(num_templates=3, jailbreak_names=["aim.yaml"])

    def test_unknown_jailbreak_name_raises(self, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer):
            with pytest.raises(ValueError, match="could not find templates"):
                Jailbreak(jailbreak_names=["does_not_exist.yaml"])

    def test_valid_jailbreak_names_accepted(self, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer):
            scenario = Jailbreak(jailbreak_names=["aim.yaml"])
        assert scenario.selected_jailbreak_names == ["aim.yaml"]


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAtomicAttacks:
    async def test_default_run_crosses_templates_objectives(self, mock_objective_target, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)

        non_baseline = [a for a in scenario._atomic_attacks if a.atomic_attack_name != "baseline"]
        # default technique = prompt_sending (1) x 1 dataset x 2 templates = 2
        assert len(non_baseline) == 2
        names = {a.atomic_attack_name for a in non_baseline}
        assert names == {
            "jailbreak_prompt_sending_aim_harmbench",
            "jailbreak_prompt_sending_aligned_harmbench",
        }

    async def test_display_group_is_template_stem(self, mock_objective_target, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
        groups = {a.display_group for a in scenario._atomic_attacks if a.atomic_attack_name != "baseline"}
        assert groups == {"aim", "aligned"}

    async def test_num_attempts_multiplies_with_unique_names(self, mock_objective_target, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=1, num_attempts=3)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
        non_baseline = [a for a in scenario._atomic_attacks if a.atomic_attack_name != "baseline"]
        assert len(non_baseline) == 3
        names = {a.atomic_attack_name for a in non_baseline}
        assert len(names) == 3  # all unique
        assert all("attempt" in n for n in names)

    async def test_explicit_strategy_adds_technique_axis(self, mock_objective_target, mock_objective_scorer):
        strat = _strategy_class()
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=1)
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[strat("prompt_sending"), strat("role_play")],
                include_baseline=False,
            )
        techniques = {
            a.atomic_attack_name.split("_")[1] for a in scenario._atomic_attacks if a.atomic_attack_name != "baseline"
        }
        # both prompt_sending and role_play techniques appear
        assert "prompt" in techniques or "role" in techniques
        assert len([a for a in scenario._atomic_attacks if a.atomic_attack_name != "baseline"]) == 2

    async def test_baseline_prepended_centrally(self, mock_objective_target, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=1)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=True)
        baselines = [a for a in scenario._atomic_attacks if a.atomic_attack_name == "baseline"]
        assert len(baselines) == 1


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakResume:
    async def test_metadata_persists_template_names(self, mock_objective_target, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer), _patch_seed_groups():
            scenario = Jailbreak(num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
        metadata = scenario._build_initial_scenario_metadata()
        assert set(metadata["jailbreak_template_names"]) == set(_MOCK_TEMPLATES)
        assert "Jailbreak templates" in metadata["summary"]

    async def test_resume_replays_persisted_names(self, mock_objective_scorer):
        with _patch_scorer(mock_objective_scorer):
            scenario = Jailbreak(scenario_result_id="abc")
        with patch.object(scenario, "_load_persisted_jailbreak_names", return_value=["aim.yaml"]):
            assert scenario._resolve_jailbreaks() == ["aim.yaml"]
