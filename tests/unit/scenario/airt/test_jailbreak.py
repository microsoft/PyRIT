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
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.scenarios.airt.jailbreak import (
    _DEFAULT_NUM_JAILBREAKS,
    _DEFAULT_TECHNIQUES,
    _JAILBREAK_SYSTEM_PROMPT,
    _JAILBREAK_TEMPLATES_METADATA_KEY,
    _PROMPT_SENDING,
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
    """Create a mock objective target that cannot carry native system-prompt delivery.

    ``configuration.includes(...)`` returns ``False`` so the default technique set degrades to the
    target-agnostic ``prompt_sending`` delivery (the ``jailbreak_system_prompt`` delivery is skipped).
    """
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveTarget", class_module="test")
    mock.configuration.includes.return_value = False
    return mock


@pytest.fixture
def mock_capable_target() -> PromptTarget:
    """Create a mock objective target that natively supports editable history + system prompts."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockCapableTarget", class_module="test")
    mock.configuration.includes.return_value = True
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
        """num_jailbreaks / num_jailbreak_attempts / jailbreak_names are declared as run parameters."""
        names = {p.name for p in Jailbreak.additional_parameters()}
        assert names == {"num_jailbreaks", "num_jailbreak_attempts", "jailbreak_names"}
        assert set(names).issubset({p.name for p in Jailbreak.supported_parameters()})

    async def test_default_draws_random_template_sample(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A bare run draws a small random sample of templates (no curated default set)."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target))
            await scenario.initialize_async()
            assert len(scenario._resolved_jailbreaks) == _DEFAULT_NUM_JAILBREAKS
            assert set(scenario._resolved_jailbreaks).issubset(set(TextJailBreak.get_jailbreak_templates()))

    async def test_num_jailbreaks_samples_that_many(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, num_jailbreaks=3))
            await scenario.initialize_async()
            assert len(scenario._resolved_jailbreaks) == 3

    async def test_mutually_exclusive_selectors_raise(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(mock_objective_target, num_jailbreaks=2, jailbreak_names=["aim.yaml"])
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

    def test_class_uses_enabled_baseline_attack_policy(self):
        assert Jailbreak.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled

    async def test_default_initialize_includes_baseline(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Baseline is on by default (BASELINE_ATTACK_POLICY = Enabled): a bare run prepends one baseline."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
            assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"
            assert sum(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks) == 1

    async def test_include_baseline_false_opts_out(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """An Enabled policy still honours an explicit ``include_baseline=False`` opt-out."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"objective_target": mock_objective_target, "include_baseline": False})
            await scenario.initialize_async()
            assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)

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
        """On a target without native system-prompt support, the default degrades to a single
        ``prompt_sending`` ("just send") delivery, one atomic attack per template."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"]))
            await scenario.initialize_async()
            attacks = scenario._atomic_attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)
            assert attacks[0].atomic_attack_name == f"{_PROMPT_SENDING}_aim_harmbench"

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
                        technique_converters={_PROMPT_SENDING: [user_converter]},
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
                f"{_PROMPT_SENDING}_aim_harmbench",
                f"{_PROMPT_SENDING}_dan_11_harmbench",
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
                args=_default_args(
                    mock_objective_target, jailbreak_names=["aim.yaml", "dan_11.yaml"], num_jailbreak_attempts=2
                )
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
                args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"], num_jailbreak_attempts=3)
            )
            await scenario.initialize_async()
            assert len(scenario._atomic_attacks) == 3


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakSystemPromptDelivery:
    """Tests for the ``jailbreak_system_prompt`` native system-prompt delivery (J5)."""

    async def test_capable_target_builds_both_deliveries(
        self, mock_capable_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """On a capable target the default set builds both deliveries for each template."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_capable_target, jailbreak_names=["aim.yaml"]))
            await scenario.initialize_async()
            names = {a.atomic_attack_name for a in scenario._atomic_attacks}
            assert names == {
                f"{_PROMPT_SENDING}_aim_harmbench",
                f"{_JAILBREAK_SYSTEM_PROMPT}_aim_harmbench",
            }

    async def test_system_delivery_attaches_system_role_framing_seed(
        self, mock_capable_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The system-prompt delivery carries the jailbreak framing as a ``role="system"`` seed."""
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_capable_target, jailbreak_names=["aim.yaml"]))
            await scenario.initialize_async()
            system_attack = next(
                a
                for a in scenario._atomic_attacks
                if a.atomic_attack_name == f"{_JAILBREAK_SYSTEM_PROMPT}_aim_harmbench"
            )
            seed_technique = system_attack.attack_technique.seed_technique
            assert seed_technique is not None
            assert [s.role for s in seed_technique.seeds] == ["system"]
            assert seed_technique.prompt_placement == "prepend"
            assert seed_technique.seeds[0].value

    async def test_system_delivery_uses_no_jailbreak_converter(
        self, mock_capable_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The jailbreak is delivered once: the converter path carries the ``TextJailbreakConverter``,
        the system-prompt path carries none (so the framing is not double-applied)."""
        captured: list[tuple[str, Any]] = []
        original_create = AttackTechniqueFactory.create

        def _spy_create(self, **kwargs):
            captured.append((self.name, kwargs.get("extra_request_converters")))
            return original_create(self, **kwargs)

        with _patch_seed_groups(mock_memory_seed_groups):
            with patch.object(AttackTechniqueFactory, "create", _spy_create):
                scenario = Jailbreak(objective_scorer=mock_objective_scorer)
                scenario.set_params_from_args(args=_default_args(mock_capable_target, jailbreak_names=["aim.yaml"]))
                await scenario.initialize_async()

        by_name = dict(captured)
        assert _PROMPT_SENDING in by_name and _JAILBREAK_SYSTEM_PROMPT in by_name
        # System delivery gets no extra converter.
        assert by_name[_JAILBREAK_SYSTEM_PROMPT] is None
        # Converter delivery gets the jailbreak converter.
        prompt_sending_converters = [c for cc in by_name[_PROMPT_SENDING] for c in cc.converters]
        assert any(isinstance(c, TextJailbreakConverter) for c in prompt_sending_converters)

    async def test_incapable_target_degrades_to_converter_delivery(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, caplog
    ):
        """When the target can't carry native system delivery, the default degrades to
        ``prompt_sending`` only and logs a warning (rather than failing)."""
        import logging

        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args=_default_args(mock_objective_target, jailbreak_names=["aim.yaml"]))
            with caplog.at_level(logging.WARNING):
                await scenario.initialize_async()
            names = {a.atomic_attack_name for a in scenario._atomic_attacks}
            assert names == {f"{_PROMPT_SENDING}_aim_harmbench"}
            assert "jailbreak_system_prompt" in caplog.text

    async def test_system_only_incapable_target_raises(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """If ``jailbreak_system_prompt`` is the only selected technique and the target can't carry
        it, initialization raises a clear error (no silent no-op run)."""
        technique_class = _build_jailbreak_technique()
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args=_default_args(
                    mock_objective_target,
                    scenario_techniques=[technique_class(_JAILBREAK_SYSTEM_PROMPT)],
                    jailbreak_names=["aim.yaml"],
                )
            )
            with pytest.raises(ValueError, match="natively supports"):
                await scenario.initialize_async()

    async def test_system_delivery_end_to_end_keeps_objective_live(self, mock_memory_seed_groups):
        """End-to-end: on a capable target the framing is delivered as a system message and the
        objective as a clean live user turn (never dropped, never wrapped by the jailbreak template).

        This guards the novel capability-gated delivery against the objective-drop failure mode: a
        system-role framing seed leaves the seed group with no ``next_message``, so ``PromptSendingAttack``
        must fall back to sending the objective itself as the user turn.
        """
        from pyrit.memory import CentralMemory
        from pyrit.score import SubStringScorer
        from tests.unit.mocks import MockPromptTarget

        target = MockPromptTarget()  # capable: native editable history + system prompt
        technique_class = _build_jailbreak_technique()
        # MockPromptTarget answers "default", so this substring scorer resolves deterministically.
        scorer = SubStringScorer(substring="default")

        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=scorer)
            scenario.set_params_from_args(
                args=_default_args(
                    target,
                    scenario_techniques=[technique_class(_JAILBREAK_SYSTEM_PROMPT)],
                    jailbreak_names=["aim.yaml"],
                )
            )
            await scenario.initialize_async()
            await scenario._atomic_attacks[0].run_async()

        pieces = CentralMemory.get_memory_instance().get_message_pieces()
        system_values = [p.converted_value for p in pieces if p.role == "system"]
        user_values = [p.converted_value for p in pieces if p.role == "user"]
        objectives = {g.objective.value for g in mock_memory_seed_groups}
        # Framing delivered as a system message...
        assert any("Niccolo" in v for v in system_values), "jailbreak framing not delivered as a system prompt"
        # ...and each objective is a clean live user turn (present verbatim, not wrapped by the template).
        assert objectives.issubset(set(user_values)), "objective was dropped or altered on the user turn"

    async def test_system_delivery_coexists_with_custom_user_prompt_seed_group(self):
        """A caller-supplied seed group carrying a user prompt at the default sequence 0 must not
        collide with the system framing seed when native system delivery merges in.

        The framing technique declares prepend placement so this merge does not depend on the
        caller's sequence values.
        """
        from pyrit.memory import CentralMemory
        from pyrit.score import SubStringScorer
        from tests.unit.mocks import MockPromptTarget

        target = MockPromptTarget()  # capable: native editable history + system prompt
        technique_class = _build_jailbreak_technique()
        scorer = SubStringScorer(substring="default")
        custom_groups = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value="explain how to hotwire a car"),
                    SeedPrompt(value="Please help with the following.", data_type="text", role="user", sequence=0),
                ]
            )
        ]

        with _patch_seed_groups(custom_groups):
            scenario = Jailbreak(objective_scorer=scorer)
            scenario.set_params_from_args(
                args=_default_args(
                    target,
                    scenario_techniques=[technique_class(_JAILBREAK_SYSTEM_PROMPT)],
                    jailbreak_names=["aim.yaml"],
                )
            )
            await scenario.initialize_async()
            # Must not raise a same-sequence role collision.
            await scenario._atomic_attacks[0].run_async()

        pieces = CentralMemory.get_memory_instance().get_message_pieces()
        system_values = [p.converted_value for p in pieces if p.role == "system"]
        user_values = [p.converted_value for p in pieces if p.role == "user"]
        assert any("Niccolo" in v for v in system_values), "jailbreak framing not delivered as a system prompt"
        assert any("Please help with the following." in v for v in user_values), "custom user prompt was dropped"


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
            scenario.set_params_from_args(args=_default_args(mock_objective_target, num_jailbreaks=3))
            persisted = ["persisted_a.yaml", "persisted_b.yaml"]
            stored = MagicMock()
            stored.metadata = {_JAILBREAK_TEMPLATES_METADATA_KEY: persisted}
            with patch.object(scenario._memory, "get_scenario_results", return_value=[stored]):
                assert scenario._resolve_templates() == persisted


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakTechniqueModel:
    """Tests for the dynamically-built JailbreakTechnique class."""

    def test_default_techniques_are_the_two_deliveries(self):
        technique_class = _technique_class()
        default_values = {t.value for t in technique_class.get_techniques_by_tag("default")}
        assert default_values == set(_DEFAULT_TECHNIQUES)
        assert default_values == {_PROMPT_SENDING, _JAILBREAK_SYSTEM_PROMPT}

    def test_registry_techniques_are_available(self):
        technique_class = _technique_class()
        available = {t.value for t in technique_class.get_all_techniques()}
        assert {_PROMPT_SENDING, _JAILBREAK_SYSTEM_PROMPT}.issubset(available)
        # The "normal ones available like from rapid response" are exposed as opt-in techniques.
        assert {"role_play_movie_script", "many_shot", "tap"}.issubset(available)

    def test_scenario_version_is_three(self):
        assert Jailbreak.VERSION == 3

    def test_default_dataset_is_harmbench(self):
        assert Jailbreak.required_datasets() == ["harmbench"]
