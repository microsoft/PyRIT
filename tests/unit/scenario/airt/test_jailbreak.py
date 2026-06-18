# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Jailbreak class."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.role_play import RolePlayAttack
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak, JailbreakStrategy
from pyrit.score.true_false.true_false_inverter_scorer import TrueFalseInverterScorer


@pytest.fixture
def mock_templates() -> list[str]:
    """Mock constant for jailbreak subset."""
    return ["aim", "dan_1", "tuo"]


@pytest.fixture
def mock_random_num_attempts() -> int:
    """Mock constant for n-many attempts per jailbreak."""
    return 2


@pytest.fixture
def mock_random_num_templates() -> int:
    """Mock constant for k-many jailbreak templates to be used."""
    return 3


@pytest.fixture
def mock_scenario_result_id() -> str:
    return "mock-scenario-result-id"


@pytest.fixture
def mock_memory_seed_groups() -> list[SeedAttackGroup]:
    """Create mock seed groups that _get_default_seed_groups() would return."""
    return [
        SeedAttackGroup(seeds=[SeedObjective(value=prompt)])
        for prompt in [
            "sample objective 1",
            "sample objective 2",
            "sample objective 3",
        ]
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
def all_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.ALL


@pytest.fixture
def simple_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.SIMPLE


@pytest.fixture
def complex_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.COMPLEX


@pytest.fixture
def manyshot_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.ManyShot


@pytest.fixture
def promptsending_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.PromptSending


@pytest.fixture
def skeleton_jailbreak_attack() -> JailbreakStrategy:
    return JailbreakStrategy.SkeletonKey


@pytest.fixture
def roleplay_jailbreak_strategy() -> JailbreakStrategy:
    return JailbreakStrategy.RolePlay


# Synthetic many-shot examples used to prevent real HTTP requests to GitHub during tests
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full dataset during unit tests."""
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


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


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakInitialization:
    """Tests for Jailbreak initialization."""

    def test_init_with_scenario_result_id(self, mock_scenario_result_id):
        """Test initialization with a scenario result ID."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(scenario_result_id=mock_scenario_result_id)
            assert scenario._scenario_result_id == mock_scenario_result_id

    def test_init_with_default_scorer(self, mock_memory_seed_groups):
        """Test initialization with default scorer."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with custom scorer."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            assert scenario._objective_scorer == mock_objective_scorer

    def test_init_with_num_templates(self, mock_random_num_templates):
        """Test initialization with num_templates provided."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(num_templates=mock_random_num_templates)
            assert scenario._num_templates == mock_random_num_templates

    def test_init_with_num_attempts(self, mock_random_num_attempts):
        """Test initialization with n provided."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(num_attempts=mock_random_num_attempts)
            assert scenario._num_attempts == mock_random_num_attempts

    def test_init_raises_exception_when_both_num_and_which_jailbreaks(self, mock_random_num_templates, mock_templates):
        """Test failure on providing mutually exclusive arguments."""

        with pytest.raises(ValueError):
            Jailbreak(num_templates=mock_random_num_templates, jailbreak_names=mock_templates)

    def test_init_accepts_subdirectory_jailbreak_names(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test that explicit jailbreak names can reference templates stored in subdirectories."""
        # Pick a template that lives in a subdirectory (not top-level)
        all_templates = TextJailBreak.get_jailbreak_templates()
        top_level_names = {f.name for f in JAILBREAK_TEMPLATES_PATH.glob("*.yaml")}
        subdir_templates = [t for t in all_templates if t not in top_level_names]
        assert subdir_templates, "Expected at least one subdirectory template to exist"
        subdir_name = subdir_templates[0]

        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, jailbreak_names=[subdir_name])
            assert scenario._jailbreaks == [subdir_name]

    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises DatasetConstraintError when datasets are not available in memory."""
        from pyrit.scenario.core.dataset_configuration import DatasetConstraintError

        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Jailbreak(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups.
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            with pytest.raises(DatasetConstraintError, match="could not be loaded"):
                await scenario.initialize_async(objective_target=mock_objective_target)

    def test_class_inherits_default_baseline_attack_policy(self):
        """Jailbreak inherits the base default (Enabled) — baseline included by default."""
        assert Jailbreak.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled

    async def test_default_initialize_includes_baseline(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """initialize_async without include_baseline honors BASELINE_ATTACK_POLICY=Enabled."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"

    async def test_explicit_include_baseline_false_omits_baseline(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Caller can opt out of baseline by passing include_baseline=False."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                include_baseline=False,
            )
            assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAttackGeneration:
    """Tests for Jailbreak attack generation."""

    async def test_attack_generation_for_simple(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, simple_jailbreak_strategy
    ):
        """Test that the simple attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[simple_jailbreak_strategy]
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, PromptSendingAttack)

    async def test_attack_generation_for_complex(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, complex_jailbreak_strategy
    ):
        """Test that the complex attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[complex_jailbreak_strategy],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(
                    run.attack_technique.attack, (RolePlayAttack, ManyShotJailbreakAttack, SkeletonKeyAttack)
                )

    async def test_attack_generation_for_manyshot(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, manyshot_jailbreak_strategy
    ):
        """Test that the manyshot attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[manyshot_jailbreak_strategy],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, ManyShotJailbreakAttack)

    async def test_attack_generation_for_promptsending(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, promptsending_jailbreak_strategy
    ):
        """Test that the prompt sending attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[promptsending_jailbreak_strategy],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, PromptSendingAttack)

    async def test_attack_generation_for_skeleton(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, skeleton_jailbreak_attack
    ):
        """Test that the skelton key attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[skeleton_jailbreak_attack],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, SkeletonKeyAttack)

    async def test_attack_generation_for_roleplay(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, roleplay_jailbreak_strategy
    ):
        """Test that the roleplaying attack generation works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[roleplay_jailbreak_strategy],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, RolePlayAttack)

    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that attack runs include objectives for each seed prompt and that
        each atomic attack carries a valid attack_technique.

        Combined coverage previously split across test_get_atomic_attacks_async_returns_attacks.
        """
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = scenario._atomic_attacks

            assert len(atomic_attacks) > 0
            for run in atomic_attacks:
                assert run.attack_technique is not None
                assert len(run.objectives) > 0

    async def test_get_all_jailbreak_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that all jailbreak templates are found."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(
                objective_scorer=mock_objective_scorer,
            )
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) > 0

    async def test_get_some_jailbreak_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_random_num_templates
    ):
        """Test that random jailbreak template selection works."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=mock_random_num_templates)
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) == mock_random_num_templates

    async def test_custom_num_attempts(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_random_num_attempts
    ):
        """Test that n successfully tries each jailbreak template n-many times."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            base_scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await base_scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            atomic_attacks_1 = base_scenario._atomic_attacks

            mult_scenario = Jailbreak(
                objective_scorer=mock_objective_scorer,
                num_templates=2,
                num_attempts=mock_random_num_attempts,
            )
            await mult_scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            atomic_attacks_n = mult_scenario._atomic_attacks

            assert len(atomic_attacks_1) * mock_random_num_attempts == len(atomic_attacks_n)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakParameters:
    """Tests for the runtime parameters declared via supported_parameters()."""

    def test_supported_parameters_declares_num_templates_and_num_attempts(self) -> None:
        """Jailbreak exposes num_templates and num_attempts as runtime parameters."""
        params = {p.name: p for p in Jailbreak.supported_parameters()}
        assert "num_templates" in params
        assert "num_attempts" in params
        assert params["num_templates"].param_type is int
        assert params["num_templates"].default == Jailbreak.DEFAULT_NUM_TEMPLATES
        assert params["num_attempts"].param_type is int
        assert params["num_attempts"].default == 1

    async def test_default_num_templates_used_when_unset(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """With no constructor arg and no runtime param, the declared default is used."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) == Jailbreak.DEFAULT_NUM_TEMPLATES

    async def test_num_templates_param_overrides_default(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A num_templates runtime parameter (the CLI path) is honored."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"num_templates": 3})
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) == 3

    async def test_constructor_num_templates_wins_over_param(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """An explicit constructor num_templates takes precedence over the runtime parameter."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            scenario.set_params_from_args(args={"num_templates": 7})
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) == 2

    async def test_num_attempts_param_override(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A num_attempts runtime parameter multiplies the atomic attack count."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"num_templates": 2, "num_attempts": 2})
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            # 2 templates x 1 strategy (SIMPLE = prompt_sending) x 2 attempts
            assert len(scenario._atomic_attacks) == 4

    def test_jailbreak_names_ignores_num_templates_param_default(self, mock_objective_scorer, mock_memory_seed_groups):
        """The non-None num_templates parameter default must not trip the mutual-exclusion guard."""
        valid_name = TextJailBreak.get_jailbreak_templates()[0]
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, jailbreak_names=[valid_name])
            assert scenario._jailbreaks == [valid_name]

    async def test_fast_path_attack_count(self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups):
        """The documented fast path (``--strategies simple``), one template, no baseline, yields one atomic attack."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"num_templates": 1})
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[JailbreakStrategy.SIMPLE],
                include_baseline=False,
            )
            assert len(scenario._atomic_attacks) == 1

    def test_default_num_templates_is_ten(self) -> None:
        """The team-agreed default quick-path sample size is 10 templates."""
        assert Jailbreak.DEFAULT_NUM_TEMPLATES == 10

    async def test_explicit_none_num_templates_resolves_full_catalog(self, mock_objective_scorer):
        """Passing num_templates=None opts out of sampling and runs the full catalog."""
        scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=None)
        resolved = scenario._resolve_jailbreaks()
        assert len(resolved) == len(TextJailBreak.get_jailbreak_templates())

    async def test_metadata_persists_selected_template_names(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The chosen template names are persisted in ScenarioResult.metadata so resume can replay them."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            result_id = scenario._scenario_result_id
            assert result_id is not None
            stored = scenario._memory.get_scenario_results(scenario_result_ids=[result_id])[0]
            assert stored.metadata["jailbreak_template_names"] == list(scenario._jailbreaks)
            assert len(stored.metadata["jailbreak_template_names"]) == 2
            # The base objective_hashes persistence must be preserved alongside the template names.
            assert "objective_hashes" in stored.metadata

    async def test_metadata_summary_lists_templates_for_visibility(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """A human-readable 'summary' surfaces the templates tried (rendered under Scenario Inputs)."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            result_id = scenario._scenario_result_id
            assert result_id is not None
            stored = scenario._memory.get_scenario_results(scenario_result_ids=[result_id])[0]
            summary = stored.metadata["summary"]
            assert summary["Jailbreak templates"] == ", ".join(scenario._jailbreaks)

    async def test_selected_jailbreak_names_property_after_initialize(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """The public property exposes the resolved template names for programmatic inspection."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            assert scenario.selected_jailbreak_names == list(scenario._jailbreaks)
            assert len(scenario.selected_jailbreak_names) == 2

    async def test_resume_replays_persisted_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """On resume the original template sample is replayed, not a fresh random draw."""
        real_templates = TextJailBreak.get_jailbreak_templates()
        first_sample = real_templates[:3]
        different_sample = real_templates[3:6]
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            first_run = Jailbreak(objective_scorer=mock_objective_scorer)
            first_run.set_params_from_args(args={"num_templates": 3})
            with patch("pyrit.datasets.jailbreak.text_jailbreak.random.sample", return_value=list(first_sample)):
                await first_run.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            result_id = first_run._scenario_result_id
            assert result_id is not None
            assert list(first_run._jailbreaks) == list(first_sample)

            stored = first_run._memory.get_scenario_results(scenario_result_ids=[result_id])[0]
            assert stored.metadata["jailbreak_template_names"] == list(first_sample)

            # A second process resuming the same scenario would otherwise draw a different sample.
            resumed = Jailbreak(objective_scorer=mock_objective_scorer, scenario_result_id=result_id)
            resumed.set_params_from_args(args={"num_templates": 3})
            with patch("pyrit.datasets.jailbreak.text_jailbreak.random.sample", return_value=list(different_sample)):
                await resumed.initialize_async(objective_target=mock_objective_target, include_baseline=False)
            assert list(resumed._jailbreaks) == list(first_sample)

    async def test_num_templates_zero_param_raises(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """An explicit num_templates of 0 raises rather than silently running the full catalog."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"num_templates": 0})
            with pytest.raises(ValueError, match="positive integer"):
                await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakLifecycle:
    """Tests for Jailbreak lifecycle."""

    async def test_initialize_async_with_max_concurrency(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseInverterScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
    ) -> None:
        """Test initialization with custom max_concurrency."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=20)
            assert scenario._max_concurrency == 20

    async def test_initialize_async_with_memory_labels(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseInverterScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
    ) -> None:
        """Test initialization with memory labels."""
        memory_labels = {"type": "jailbreak", "category": "scenario"}

        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                memory_labels=memory_labels,
                objective_target=mock_objective_target,
            )
            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakProperties:
    """Tests for Jailbreak properties."""

    def test_scenario_version_is_set(
        self,
        *,
        mock_objective_scorer: TrueFalseInverterScorer,
    ) -> None:
        """Test that scenario version is properly set."""
        scenario = Jailbreak(
            objective_scorer=mock_objective_scorer,
        )

        assert scenario.VERSION == 2

    def test_scenario_default_dataset(self) -> None:
        """Test that scenario default dataset is correct."""

        assert Jailbreak.required_datasets() == ["airt_harms"]

    async def test_no_target_duplication_async(
        self, *, mock_objective_target: PromptTarget, mock_memory_seed_groups: list[SeedAttackGroup]
    ) -> None:
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak()
            await scenario.initialize_async(objective_target=mock_objective_target)

            objective_target = scenario._objective_target
            scorer_target = scenario._objective_scorer

            assert objective_target != scorer_target


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAdversarialTarget:
    """Tests for adversarial target creation and caching."""

    def test_get_or_create_adversarial_target_returns_prompt_target(self) -> None:
        """Test that _get_or_create_adversarial_target returns a PromptTarget."""
        from pyrit.prompt_target import PromptTarget

        scenario = Jailbreak()
        target = scenario._get_or_create_adversarial_target()
        assert isinstance(target, PromptTarget)

    def test_get_or_create_adversarial_target_reuses_instance(self) -> None:
        """Test that _get_or_create_adversarial_target returns the same instance on repeated calls."""
        scenario = Jailbreak()
        first = scenario._get_or_create_adversarial_target()
        second = scenario._get_or_create_adversarial_target()
        assert first is second

    def test_get_or_create_adversarial_target_creates_on_first_call(self) -> None:
        """Test that _adversarial_target starts as None and is populated after first access."""
        scenario = Jailbreak()
        assert scenario._adversarial_target is None
        target = scenario._get_or_create_adversarial_target()
        assert scenario._adversarial_target is target

    async def test_roleplay_attacks_share_adversarial_target(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseInverterScorer,
        mock_memory_seed_groups: list[SeedAttackGroup],
        roleplay_jailbreak_strategy: JailbreakStrategy,
    ) -> None:
        """Test that multiple role-play attacks share the same adversarial target instance."""
        with patch.object(
            Jailbreak,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[roleplay_jailbreak_strategy],
                include_baseline=False,
            )
            atomic_attacks = scenario._atomic_attacks
            assert len(atomic_attacks) >= 2

            # All role-play attacks should share the same adversarial target
            adversarial_targets = [run.attack_technique.attack._adversarial_chat for run in atomic_attacks]
            assert all(t is adversarial_targets[0] for t in adversarial_targets)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakBaselineUniformity:
    """ADO 9012 regression: baseline shares objectives with strategies under max_dataset_size."""

    async def test_one_resolution_call_baseline_matches_strategies(
        self, mock_objective_target, mock_objective_scorer, simple_jailbreak_strategy
    ):
        from pyrit.models import SeedAttackGroup, SeedObjective
        from pyrit.scenario import DatasetAttackConfiguration

        seed_groups = [SeedAttackGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]
        config = DatasetAttackConfiguration(seed_groups=seed_groups, max_dataset_size=3)

        first_sample = [("inline", group) for group in seed_groups[:3]]
        second_sample = [("inline", group) for group in seed_groups[5:8]]
        # Use an explicit jailbreak name so deferred template resolution does not consume the patched
        # random.sample; this test isolates seed-group sampling, not template selection.
        template_name = TextJailBreak.get_jailbreak_templates()[0]
        scenario = Jailbreak(objective_scorer=mock_objective_scorer, jailbreak_names=[template_name])
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            side_effect=[first_sample, second_sample],
        ) as mock_sample:
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[simple_jailbreak_strategy],
                dataset_config=config,
                include_baseline=True,
            )

        assert mock_sample.call_count == 1
        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"
        baseline_objs = set(scenario._atomic_attacks[0].objectives)
        for attack in scenario._atomic_attacks[1:]:
            assert set(attack.objectives) == baseline_objs
