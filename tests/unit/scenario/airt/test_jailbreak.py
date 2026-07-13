# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Jailbreak class."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.converter import TextJailbreakConverter
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.scenarios.airt.jailbreak import (
    _DEFAULT_JAILBREAK_NAMES,
    _JAILBREAK_TEMPLATES_METADATA_KEY,
    _PROMPT_SENDING_TECHNIQUE_NAME,
    Jailbreak,
    _build_jailbreak_technique,
)
from pyrit.score.true_false.true_false_inverter_scorer import TrueFalseInverterScorer
from pyrit.setup.initializers.techniques import build_technique_factories

# Synthetic many-shot examples — prevents reading the real JSON if a factory is constructed.
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


def _technique_class():
    """Get the dynamically-generated JailbreakTechnique class."""
    return _build_jailbreak_technique()


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Populate the attack-technique registry so the dynamic technique class can be built.

    Mirrors the RapidResponse test setup: reset the registries, register a mock adversarial
    target (so factory construction does not fall back to a real target), and register the core
    technique factories. The build cache is cleared around each test so the class reflects the
    freshly-registered factories.
    """
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_jailbreak_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_jailbreak_technique.cache_clear()


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full bundled dataset."""
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


@pytest.fixture
def mock_scenario_result_id() -> str:
    return "mock-scenario-result-id"


@pytest.fixture
def mock_memory_seed_groups() -> list[AttackSeedGroup]:
    """Create mock seed groups that the dataset resolution would return."""
    return [
        AttackSeedGroup(seeds=[SeedObjective(value=prompt)])
        for prompt in ["sample objective 1", "sample objective 2", "sample objective 3"]
    ]


@pytest.fixture
def mock_objective_target() -> PromptTarget:
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveTarget", class_module="test")
    return mock


@pytest.fixture
def mock_objective_scorer() -> TrueFalseInverterScorer:
    """Create a mock scorer for testing."""
    mock = MagicMock(spec=TrueFalseInverterScorer)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveScorer", class_module="test")
    return mock


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


FIXTURES = ["patch_central_database", "mock_runtime_env"]


def _patch_seed_groups(seed_groups):
    return patch.object(
        Jailbreak,
        "_resolve_seed_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"harmbench": seed_groups},
    )


def _default_args(target, **extra):
    """Run args selecting the default technique (``prompt_sending``, "just send")."""
    technique_class = _build_jailbreak_technique()
    args = {
        "objective_target": target,
        "scenario_techniques": [technique_class("default")],
        "include_baseline": False,
    }
    args.update(extra)
    return args


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakInitialization:
    """Tests for Jailbreak initialization."""

    def test_init_with_scenario_result_id(self, mock_scenario_result_id, mock_memory_seed_groups):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(scenario_result_id=mock_scenario_result_id)
            assert scenario._scenario_result_id == mock_scenario_result_id

    def test_init_with_default_scorer(self, mock_memory_seed_groups):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, mock_objective_scorer, mock_memory_seed_groups):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            assert scenario._objective_scorer == mock_objective_scorer

    def test_declares_run_parameters(self):
        """num_templates / num_attempts / jailbreak_names are declared as run parameters."""
        names = {p.name for p in Jailbreak.additional_parameters()}
        assert names == {"num_templates", "num_attempts", "jailbreak_names"}
        assert set(names).issubset({p.name for p in Jailbreak.supported_parameters()})

    async def test_default_uses_curated_template_set(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A bare run resolves the small curated default template set, not the full catalog."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target))
            await scenario.initialize_async()
            assert scenario._resolved_jailbreaks == _DEFAULT_JAILBREAK_NAMES
            assert len(_DEFAULT_JAILBREAK_NAMES) < len(TextJailBreak.get_jailbreak_templates())

    async def test_num_templates_samples_that_many(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, num_templates=3))
            await scenario.initialize_async()
            assert len(scenario._resolved_jailbreaks) == 3

    async def test_mutually_exclusive_selectors_raise(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, num_templates=2, jailbreak_names=["aim.yaml"])
            )
            with pytest.raises(ValueError, match="only one of"):
                await scenario.initialize_async()

    async def test_unknown_jailbreak_name_raises(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["definitely_not_real.yaml"])
            )
            with pytest.raises(ValueError, match="could not find templates"):
                await scenario.initialize_async()

    async def test_accepts_subdirectory_jailbreak_names(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        all_templates = TextJailBreak.get_jailbreak_templates()
        top_level_names = {f.name for f in JAILBREAK_TEMPLATES_PATH.glob("*.yaml")}
        subdir_templates = [t for t in all_templates if t not in top_level_names]
        assert subdir_templates, "Expected at least one subdirectory template to exist"
        subdir_name = subdir_templates[0]

        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, jailbreak_names=[subdir_name]))
            await scenario.initialize_async()
            assert scenario._resolved_jailbreaks == [subdir_name]

    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

        scenario = Jailbreak(objective_scorer=mock_objective_scorer)
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(DatasetConstraintError, match="could not be loaded"):
                await scenario.initialize_async()

    def test_class_inherits_default_baseline_attack_policy(self):
        assert Jailbreak.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Disabled

    async def test_default_initialize_omits_baseline(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Baseline is off by default (BASELINE_ATTACK_POLICY = Disabled): a bare run has no baseline."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
            assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)

    async def test_include_baseline_true_opts_in(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A Disabled policy still honours an explicit ``include_baseline=True`` opt-in."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"objective_target": mock_objective_target, "include_baseline": True})
            await scenario.initialize_async()
            assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"

    async def test_explicit_include_baseline_false_omits_baseline(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target))
            await scenario.initialize_async()
            assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAttackGeneration:
    """Tests for Jailbreak atomic-attack generation."""

    async def test_default_builds_prompt_sending_per_template(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The default technique is ``prompt_sending`` ("just send"), one atomic attack per template."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"]))
            await scenario.initialize_async()
            attacks = scenario._atomic_attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)
            assert attacks[0].atomic_attack_name == f"{_PROMPT_SENDING_TECHNIQUE_NAME}_aim_harmbench"

    async def test_jailbreak_delivered_as_request_converter(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The crux: the jailbreak template reaches the target as a ``TextJailbreakConverter`` on
        the technique's outgoing requests (not as prepended framing on the seed group).

        Delivery via ``factory.create(extra_request_converters=...)`` is what keeps the scenario
        target-agnostic and composable with every technique. Also assert the seed groups carry no
        prepended jailbreak framing.
        """
        captured: list[Any] = []
        original_create = AttackTechniqueFactory.create

        def _spy_create(self, **kwargs):
            captured.append(kwargs.get("extra_request_converters"))
            return original_create(self, **kwargs)

        with _patch_seed_groups(mock_memory_seed_groups):
            with patch.object(AttackTechniqueFactory, "create", _spy_create):
                scenario = Jailbreak(objective_scorer=mock_objective_scorer)
                scenario.set_params_from_args(args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"]))
                await scenario.initialize_async()

            assert captured, "Expected factory.create to be called"
            converters = [c for extra in captured if extra for cc in extra for c in cc.converters]
            assert any(isinstance(c, TextJailbreakConverter) for c in converters), (
                "Expected a TextJailbreakConverter to be threaded to factory.create"
            )

            # The objective seed groups themselves carry no jailbreak framing (converter delivery only).
            for attack in scenario._atomic_attacks:
                for group in attack.seed_groups:
                    assert not group.prepended_conversation

    async def test_user_technique_converters_are_preserved_alongside_jailbreak(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A caller-supplied ``--techniques prompt_sending:converter`` stack is not dropped: the
        jailbreak converter is layered on top of (not in place of) the user's converters."""
        from pyrit.converter import Base64Converter

        technique_class = _build_jailbreak_technique()
        user_converter = Base64Converter()
        captured: list[Any] = []
        original_create = AttackTechniqueFactory.create

        def _spy_create(self, **kwargs):
            captured.append(kwargs.get("extra_request_converters"))
            return original_create(self, **kwargs)

        with _patch_seed_groups(mock_memory_seed_groups):
            with patch.object(AttackTechniqueFactory, "create", _spy_create):
                scenario = Jailbreak(objective_scorer=mock_objective_scorer)
                scenario.set_params_from_args(
                    args=_default_args(
                        mock_objective_target,
                        jailbreak_names=["aim.yaml"],
                        technique_converters={_PROMPT_SENDING_TECHNIQUE_NAME: [user_converter]},
                    )
                )
                await scenario.initialize_async()

            converters = [c for extra in captured if extra for cc in extra for c in cc.converters]
            jailbreak_indices = [i for i, c in enumerate(converters) if isinstance(c, TextJailbreakConverter)]
            user_indices = [i for i, c in enumerate(converters) if c is user_converter]
            assert jailbreak_indices, "Expected a TextJailbreakConverter in the stack"
            assert user_indices, "User-supplied converter must be preserved"
            # The jailbreak must wrap the raw objective before any caller converters transform it, so
            # the jailbreak converter has to precede the user converter in the extra-converter stack.
            assert max(jailbreak_indices) < min(user_indices), (
                "Jailbreak converter must be applied before caller-supplied converters"
            )

    async def test_simulated_conversation_techniques_produce_attacks_with_jailbreak(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Regression: simulated-conversation techniques (``role_play_*``, ``crescendo_*``) must still
        produce atomic attacks when crossed with a jailbreak template, and each must receive the
        jailbreak converter.

        Converter delivery leaves the objective seed group unframed, so it stays compatible with the
        simulated-conversation seed technique. (Delivering the jailbreak as a system-role framing seed
        instead collided with that technique's seed range and silently produced zero attacks.)
        """
        technique_class = _build_jailbreak_technique()
        techniques = [
            technique_class("role_play_movie_script"),
            technique_class("crescendo_simulated"),
        ]
        captured: list[Any] = []
        original_create = AttackTechniqueFactory.create

        def _spy_create(self, **kwargs):
            captured.append(kwargs.get("extra_request_converters"))
            return original_create(self, **kwargs)

        with _patch_seed_groups(mock_memory_seed_groups):
            with patch.object(AttackTechniqueFactory, "create", _spy_create):
                scenario = Jailbreak(objective_scorer=mock_objective_scorer)
                scenario.set_params_from_args(
                    args=_default_args(
                        mock_objective_target, scenario_techniques=techniques, jailbreak_names=["aim.yaml"]
                    )
                )
                await scenario.initialize_async()
            names = {a.atomic_attack_name for a in scenario._atomic_attacks}
            assert "role_play_movie_script_aim_harmbench" in names
            assert "crescendo_simulated_aim_harmbench" in names
            # Every build of a simulated-conversation technique must still carry the jailbreak
            # converter. Assert both techniques captured a non-empty converter stack (so the check
            # can't pass vacuously on a dropped/None stack) and each contains the jailbreak converter.
            populated = [extra for extra in captured if extra]
            assert len(populated) == 2, "Expected both simulated-conversation techniques to receive converters"
            assert all(
                any(isinstance(c, TextJailbreakConverter) for cc in extra for c in cc.converters) for extra in populated
            ), "Each simulated-conversation technique must receive the jailbreak converter"

    async def test_all_templates_produce_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Each selected template yields its own atomic attack, grouped by template stem."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"])
            )
            await scenario.initialize_async()
            names = {a.atomic_attack_name for a in scenario._atomic_attacks}
            assert names == {
                f"{_PROMPT_SENDING_TECHNIQUE_NAME}_aim_harmbench",
                f"{_PROMPT_SENDING_TECHNIQUE_NAME}_dan_11_harmbench",
            }

    async def test_display_group_is_template_stem(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"])
            )
            await scenario.initialize_async()
            groups = {a.display_group for a in scenario._atomic_attacks}
            assert groups == {"aim", "dan_11"}

    async def test_atomic_attack_names_are_unique(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"], num_attempts=2)
            )
            await scenario.initialize_async()
            names = [a.atomic_attack_name for a in scenario._atomic_attacks]
            assert len(names) == len(set(names))
            assert len(names) == 4  # 2 templates x 2 attempts

    async def test_memory_labels_propagate_to_atomic_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Run-level memory_labels reach every built atomic attack (matches the sibling convention)."""
        labels = {"experiment": "jb-run", "operator": "airt"}
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(
                    mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"], memory_labels=labels
                )
            )
            await scenario.initialize_async()
            assert scenario._atomic_attacks
            assert all(a._memory_labels == labels for a in scenario._atomic_attacks)

    async def test_num_attempts_multiplies_atomic_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"], num_attempts=3)
            )
            await scenario.initialize_async()
            assert len(scenario._atomic_attacks) == 3


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakResumePersistence:
    """Tests for resume-stable template persistence."""

    async def test_metadata_records_resolved_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"])
            )
            await scenario.initialize_async()
            metadata = scenario._build_initial_scenario_metadata()
            assert metadata[_JAILBREAK_TEMPLATES_METADATA_KEY] == ["aim.yaml", "dan_11.yaml"]

    async def test_resolve_templates_replays_persisted_set_on_resume(
        self, mock_scenario_result_id, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, scenario_result_id=mock_scenario_result_id)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, num_templates=3))
            persisted = ["persisted_a.yaml", "persisted_b.yaml"]
            stored = MagicMock()
            stored.metadata = {_JAILBREAK_TEMPLATES_METADATA_KEY: persisted}
            with patch.object(scenario._memory, "get_scenario_results", return_value=[stored]):
                assert scenario._resolve_templates() == persisted


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakTechniqueModel:
    """Tests for the dynamically-built JailbreakTechnique class."""

    def test_prompt_sending_is_the_default_technique(self):
        technique_class = _technique_class()
        default_values = {t.value for t in technique_class.get_techniques_by_tag("default")}
        assert default_values == {_PROMPT_SENDING_TECHNIQUE_NAME}

    def test_registry_techniques_are_available(self):
        technique_class = _technique_class()
        available = {t.value for t in technique_class.get_all_techniques()}
        assert _PROMPT_SENDING_TECHNIQUE_NAME in available
        # The "normal ones available like from rapid response" are exposed as opt-in techniques.
        assert {"role_play_movie_script", "many_shot", "tap"}.issubset(available)

    def test_delivery_methods_are_not_techniques(self):
        """system/user delivery roles are not technique members (delivery role is orthogonal)."""
        values = {t.value for t in _technique_class()}
        assert "system" not in values
        assert "user" not in values

    def test_scenario_version_is_two(self):
        assert Jailbreak.VERSION == 2

    def test_default_dataset_is_harmbench(self):
        assert Jailbreak.required_datasets() == ["harmbench"]
