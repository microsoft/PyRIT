# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Jailbreak class."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.role_play import RolePlayAttack
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedGroup, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
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
def mock_memory_seed_groups() -> list[SeedGroup]:
    """Create mock seed groups that _get_default_seed_groups() would return."""
    return [
        SeedGroup(seeds=[SeedObjective(value=prompt)])
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
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(scenario_result_id=mock_scenario_result_id)
            assert scenario._scenario_result_id == mock_scenario_result_id

    def test_init_with_default_scorer(self, mock_memory_seed_groups):
        """Test initialization with default scorer."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with custom scorer."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            assert scenario._objective_scorer == mock_objective_scorer

    def test_init_with_num_templates(self, mock_random_num_templates):
        """Test initialization with num_templates provided."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(num_templates=mock_random_num_templates)
            assert scenario._num_templates == mock_random_num_templates

    def test_init_with_num_attempts(self, mock_random_num_attempts):
        """Test initialization with n provided."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
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

        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, jailbreak_names=[subdir_name])
            assert scenario._jailbreaks == [subdir_name]

    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Jailbreak(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAttackGeneration:
    """Tests for Jailbreak attack generation."""

    async def test_attack_generation_for_all(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            assert len(atomic_attacks) > 0
            assert all(run.attack_technique is not None for run in atomic_attacks)

    async def test_attack_generation_for_simple(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, simple_jailbreak_strategy
    ):
        """Test that the simple attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[simple_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, PromptSendingAttack)

    async def test_attack_generation_for_complex(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, complex_jailbreak_strategy
    ):
        """Test that the complex attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[complex_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(
                    run.attack_technique.attack, (RolePlayAttack, ManyShotJailbreakAttack, SkeletonKeyAttack)
                )

    async def test_attack_generation_for_manyshot(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, manyshot_jailbreak_strategy
    ):
        """Test that the manyshot attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[manyshot_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, ManyShotJailbreakAttack)

    async def test_attack_generation_for_promptsending(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, promptsending_jailbreak_strategy
    ):
        """Test that the prompt sending attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[promptsending_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, PromptSendingAttack)

    async def test_attack_generation_for_skeleton(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, skeleton_jailbreak_attack
    ):
        """Test that the skelton key attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[skeleton_jailbreak_attack]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, SkeletonKeyAttack)

    async def test_attack_generation_for_roleplay(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, roleplay_jailbreak_strategy
    ):
        """Test that the roleplaying attack generation works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[roleplay_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run.attack_technique.attack, RolePlayAttack)

    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that attack runs include objectives for each seed prompt."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            # Check that objectives are created for each seed prompt
            for run in atomic_attacks:
                assert len(run.objectives) > 0

    async def test_get_atomic_attacks_async_returns_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = await scenario._get_atomic_attacks_async()
            assert len(atomic_attacks) > 0
            assert all(run.attack_technique is not None for run in atomic_attacks)

    async def test_get_all_jailbreak_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that all jailbreak templates are found."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(
                objective_scorer=mock_objective_scorer,
            )
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) > 0

    async def test_get_some_jailbreak_templates(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_random_num_templates
    ):
        """Test that random jailbreak template selection works."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=mock_random_num_templates)
            await scenario.initialize_async(objective_target=mock_objective_target)
            assert len(scenario._jailbreaks) == mock_random_num_templates

    async def test_custom_num_attempts(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_random_num_attempts
    ):
        """Test that n successfully tries each jailbreak template n-many times."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            base_scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await base_scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks_1 = await base_scenario._get_atomic_attacks_async()

            mult_scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_attempts=mock_random_num_attempts)
            await mult_scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks_n = await mult_scenario._get_atomic_attacks_async()

            assert len(atomic_attacks_1) * mock_random_num_attempts == len(atomic_attacks_n)


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakLifecycle:
    """Tests for Jailbreak lifecycle."""

    async def test_initialize_async_with_max_concurrency(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseInverterScorer,
        mock_memory_seed_groups: list[SeedGroup],
    ) -> None:
        """Test initialization with custom max_concurrency."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=20)
            assert scenario._max_concurrency == 20

    async def test_initialize_async_with_memory_labels(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseInverterScorer,
        mock_memory_seed_groups: list[SeedGroup],
    ) -> None:
        """Test initialization with memory labels."""
        memory_labels = {"type": "jailbreak", "category": "scenario"}

        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
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

        assert scenario.VERSION == 1

    def test_scenario_default_dataset(self) -> None:
        """Test that scenario default dataset is correct."""

        assert Jailbreak.required_datasets() == ["airt_harms"]

    async def test_no_target_duplication_async(
        self, *, mock_objective_target: PromptTarget, mock_memory_seed_groups: list[SeedGroup]
    ) -> None:
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak()
            await scenario.initialize_async(objective_target=mock_objective_target)

            objective_target = scenario._objective_target
            scorer_target = scenario._objective_scorer

            assert objective_target != scorer_target


@pytest.mark.usefixtures(*FIXTURES)
class TestJailbreakAdversarialTarget:
    """Tests for adversarial target creation and caching."""

    def test_create_adversarial_target_returns_openai_chat_target(self) -> None:
        """Test that _create_adversarial_target returns a new OpenAIChatTarget."""
        scenario = Jailbreak()
        target = scenario._create_adversarial_target()
        assert isinstance(target, OpenAIChatTarget)

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
        mock_memory_seed_groups: list[SeedGroup],
        roleplay_jailbreak_strategy: JailbreakStrategy,
    ) -> None:
        """Test that multiple role-play attacks share the same adversarial target instance."""
        with patch.object(Jailbreak, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Jailbreak(objective_scorer=mock_objective_scorer, num_templates=2)
            await scenario.initialize_async(
                objective_target=mock_objective_target, scenario_strategies=[roleplay_jailbreak_strategy]
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            assert len(atomic_attacks) >= 2

            # All role-play attacks should share the same adversarial chat target
            adversarial_targets = [run.attack_technique.attack._adversarial_chat for run in atomic_attacks]
            assert all(t is adversarial_targets[0] for t in adversarial_targets)
