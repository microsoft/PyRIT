# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Cyber class."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedDataset, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget, PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.airt import Cyber, CyberStrategy
from pyrit.score import TrueFalseCompositeScorer


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


@pytest.fixture
def mock_memory_seed_groups():
    """Create mock seed groups that _get_default_seed_groups() would return."""
    malware_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
    seed_prompts = list(SeedDataset.from_yaml_file(malware_path / "malware.prompt").get_values())
    return [SeedAttackGroup(seeds=[SeedObjective(value=prompt)]) for prompt in seed_prompts]


@pytest.fixture
def mock_dataset_config(mock_memory_seed_groups):
    """Create a mock dataset config that returns the seed groups."""
    mock_config = MagicMock(spec=DatasetConfiguration)
    mock_config.get_all_seed_attack_groups.return_value = mock_memory_seed_groups
    mock_config.get_default_dataset_names.return_value = ["airt_malware"]
    mock_config.has_data_source.return_value = True
    return mock_config


@pytest.fixture
def fast_cyberstrategy():
    return CyberStrategy.SINGLE_TURN


@pytest.fixture
def slow_cyberstrategy():
    return CyberStrategy.MULTI_TURN


@pytest.fixture
def malware_prompts():
    """The default malware prompts."""
    malware_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
    return list(SeedDataset.from_yaml_file(malware_path / "malware.prompt").get_values())


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


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    """Create a mock objective scorer for testing."""
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def mock_adversarial_target():
    """Create a mock adversarial target for testing."""
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = _mock_target_id("MockAdversarialTarget")
    return mock


@pytest.fixture
def sample_objectives() -> list[str]:
    """Create sample objectives for testing."""
    return ["test prompt 1", "test prompt 2"]


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberInitialization:
    """Tests for Cyber initialization."""

    def test_init_with_custom_objectives(self, mock_objective_scorer, sample_objectives):
        """Test initialization with custom objectives."""

        scenario = Cyber(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        # objectives are stored as _deprecated_objectives; _seed_groups is resolved lazily
        assert scenario._deprecated_objectives == sample_objectives
        assert scenario.name == "Cyber"
        assert scenario.VERSION == 1

    def test_init_with_default_objectives(self, mock_objective_scorer, malware_prompts, mock_memory_seed_groups):
        """Test initialization with default objectives."""

        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(objective_scorer=mock_objective_scorer)

            # seed_groups are resolved lazily; _deprecated_objectives should be None
            assert scenario._deprecated_objectives is None
            assert scenario.name == "Cyber"
            assert scenario.VERSION == 1

    def test_init_with_default_scorer(self, mock_memory_seed_groups):
        """Test initialization with default scorer."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with custom scorer."""
        scorer = MagicMock(TrueFalseCompositeScorer)
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(objective_scorer=scorer)
            assert isinstance(scenario._scorer_config, AttackScoringConfig)

    def test_init_default_adversarial_chat(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with default adversarial chat."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            assert isinstance(scenario._adversarial_chat, OpenAIChatTarget)
            assert scenario._adversarial_chat._temperature == 1.2

    def test_init_with_adversarial_chat(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with adversarial chat (for red teaming attack variation)."""
        adversarial_chat = MagicMock(OpenAIChatTarget)
        adversarial_chat.get_identifier.return_value = _mock_target_id("CustomAdversary")

        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            assert scenario._adversarial_chat == adversarial_chat
            assert scenario._adversarial_config.target == adversarial_chat

    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Cyber(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberAttackGeneration:
    """Tests for Cyber attack generation."""

    @pytest.mark.asyncio
    async def test_attack_generation_for_all(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_attack_generation_for_singleturn(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_memory_seed_groups,
        mock_dataset_config,
        fast_cyberstrategy,
    ):
        """Test that the single turn attack generation works."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[fast_cyberstrategy],
                dataset_config=mock_dataset_config,
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()
            for run in atomic_attacks:
                assert isinstance(run._attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attack_generation_for_multiturn(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_memory_seed_groups,
        mock_dataset_config,
        slow_cyberstrategy,
    ):
        """Test that the multi turn attack generation works."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[slow_cyberstrategy],
                dataset_config=mock_dataset_config,
            )
            atomic_attacks = await scenario._get_atomic_attacks_async()

            for run in atomic_attacks:
                assert isinstance(run._attack, RedTeamingAttack)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that attack runs include objectives for each seed prompt."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            # Check that objectives are created for each seed prompt
            for run in atomic_attacks:
                assert len(run.objectives) > 0

    @pytest.mark.asyncio
    async def test_get_atomic_attacks_async_returns_attacks(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()
            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberLifecycle:
    """
    Tests for Cyber lifecycle, including initialize_async and execution.
    """

    @pytest.mark.asyncio
    async def test_initialize_async_with_max_concurrency(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test initialization with custom max_concurrency."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                objective_target=mock_objective_target, max_concurrency=20, dataset_config=mock_dataset_config
            )
            assert scenario._max_concurrency == 20

    @pytest.mark.asyncio
    async def test_initialize_async_with_memory_labels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test initialization with memory labels."""
        memory_labels = {"test": "cyber", "category": "scenario"}

        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )
            await scenario.initialize_async(
                memory_labels=memory_labels,
                objective_target=mock_objective_target,
                dataset_config=mock_dataset_config,
            )

            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestCyberProperties:
    """
    Tests for Cyber properties and attributes.
    """

    def test_scenario_version_is_set(self, mock_objective_scorer, mock_memory_seed_groups):
        """Test that scenario version is properly set."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber(
                objective_scorer=mock_objective_scorer,
            )

            assert scenario.VERSION == 1

    @pytest.mark.asyncio
    async def test_no_target_duplication(self, mock_objective_target, mock_memory_seed_groups, mock_dataset_config):
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(Cyber, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Cyber()
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

            objective_target = scenario._objective_target

            # this works because TrueFalseCompositeScorer subclasses TrueFalseScorer,
            # but TrueFalseScorer itself (the type for ScorerConfig) does not have ._scorers.
            scorer_target = scenario._scorer_config.objective_scorer._scorers[0]  # type: ignore[arg-type]
            adversarial_target = scenario._adversarial_chat

            assert objective_target != scorer_target
            assert objective_target != adversarial_target
            assert scorer_target != adversarial_target
