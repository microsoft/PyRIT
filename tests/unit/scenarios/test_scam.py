# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Scam class."""

import pathlib
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    ContextComplianceAttack,
    RedTeamingAttack,
    RolePlayAttack,
)
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import SeedDataset, SeedGroup, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget, PromptTarget
from pyrit.scenario.scenarios.airt.scam import Scam, ScamStrategy
from pyrit.score import TrueFalseCompositeScorer

SEED_DATASETS_PATH = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
SEED_PROMPT_LIST = list(SeedDataset.from_yaml_file(SEED_DATASETS_PATH / "scams.prompt").get_values())


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test",
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_memory_seed_groups() -> List[SeedGroup]:
    """Create mock seed groups that _get_default_seed_groups() would return."""
    return [SeedGroup(seeds=[SeedObjective(value=prompt)]) for prompt in SEED_PROMPT_LIST]


@pytest.fixture
def single_turn_strategy() -> ScamStrategy:
    return ScamStrategy.SINGLE_TURN


@pytest.fixture
def multi_turn_strategy() -> ScamStrategy:
    return ScamStrategy.MULTI_TURN


@pytest.fixture
def scam_prompts() -> List[str]:
    return SEED_PROMPT_LIST


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
def mock_objective_target() -> PromptTarget:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = {"__type__": "MockObjectiveTarget", "__module__": "test"}
    return mock


@pytest.fixture
def mock_objective_scorer() -> TrueFalseCompositeScorer:
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def mock_adversarial_target() -> PromptChatTarget:
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = {"__type__": "MockAdversarialTarget", "__module__": "test"}
    return mock


@pytest.fixture
def sample_objectives() -> List[str]:
    return ["scam prompt 1", "scam prompt 2"]


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestScamInitialization:
    """Tests for Scam initialization."""

    def test_init_with_custom_objectives(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        sample_objectives: List[str],
    ) -> None:
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        # objectives are stored as _deprecated_objectives; _seed_groups is resolved lazily
        assert scenario._deprecated_objectives == sample_objectives
        assert scenario.name == "Scam"
        assert scenario.version == 1

    def test_init_with_default_objectives(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: List[SeedGroup],
    ) -> None:
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            # seed_groups are resolved lazily; _deprecated_objectives should be None
            assert scenario._deprecated_objectives is None
            assert scenario.name == "Scam"
            assert scenario.version == 1

    def test_init_with_default_scorer(self, mock_memory_seed_groups) -> None:
        """Test initialization with default scorer."""
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam()
            assert scenario._objective_scorer_identifier

    def test_init_with_custom_scorer(self, *, mock_memory_seed_groups: List[SeedGroup]) -> None:
        """Test initialization with custom scorer."""
        scorer = MagicMock(spec=TrueFalseCompositeScorer)

        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=scorer)
            assert isinstance(scenario._scorer_config, AttackScoringConfig)

    def test_init_default_adversarial_chat(
        self, *, mock_objective_scorer: TrueFalseCompositeScorer, mock_memory_seed_groups: List[SeedGroup]
    ) -> None:
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            assert isinstance(scenario._adversarial_chat, OpenAIChatTarget)
            assert scenario._adversarial_chat._temperature == 1.2

    def test_init_with_adversarial_chat(
        self, *, mock_objective_scorer: TrueFalseCompositeScorer, mock_memory_seed_groups: List[SeedGroup]
    ) -> None:
        adversarial_chat = MagicMock(OpenAIChatTarget)
        adversarial_chat.get_identifier.return_value = {"type": "CustomAdversary"}

        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            assert scenario._adversarial_chat == adversarial_chat
            assert scenario._adversarial_config.target == adversarial_chat

    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available_async(
        self, mock_objective_target, mock_objective_scorer
    ):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = Scam(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)


@pytest.mark.usefixtures(*FIXTURES)
class TestScamAttackGeneration:
    """Tests for Scam attack generation."""

    @pytest.mark.asyncio
    async def test_attack_generation_for_all(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(objective_target=mock_objective_target)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_attack_generation_for_singleturn_async(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        single_turn_strategy: ScamStrategy,
        sample_objectives: List[str],
    ) -> None:
        """Test that the single turn strategy attack generation works."""
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target, scenario_strategies=[single_turn_strategy]
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()

        for run in atomic_attacks:
            assert isinstance(run._attack, ContextComplianceAttack) or isinstance(run._attack, RolePlayAttack)

    @pytest.mark.asyncio
    async def test_attack_generation_for_multiturn_async(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, multi_turn_strategy
    ):
        """Test that the multi turn attack generation works."""
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target, scenario_strategies=[multi_turn_strategy]
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()

        for run in atomic_attacks:
            assert isinstance(run._attack, RedTeamingAttack)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives_async(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        sample_objectives: List[str],
    ) -> None:
        """Test that attack runs include objectives for each seed prompt."""
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(objective_target=mock_objective_target)
        atomic_attacks = await scenario._get_atomic_attacks_async()

        for run in atomic_attacks:
            assert len(run.objectives) == len(sample_objectives)
            for index, objective in enumerate(run.objectives):
                assert sample_objectives[index] in objective

    @pytest.mark.asyncio
    async def test_get_atomic_attacks_async_returns_attacks(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        sample_objectives: List[str],
    ) -> None:
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(objective_target=mock_objective_target)
        atomic_attacks = await scenario._get_atomic_attacks_async()
        assert len(atomic_attacks) > 0
        assert all(hasattr(run, "_attack") for run in atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestScamLifecycle:
    """Tests for Scam lifecycle behavior."""

    @pytest.mark.asyncio
    async def test_initialize_async_with_max_concurrency(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: List[SeedGroup],
    ) -> None:
        """Test initialization with custom max_concurrency."""
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=20)
            assert scenario._max_concurrency == 20

    @pytest.mark.asyncio
    async def test_initialize_async_with_memory_labels(
        self,
        *,
        mock_objective_target: PromptTarget,
        mock_objective_scorer: TrueFalseCompositeScorer,
        mock_memory_seed_groups: List[SeedGroup],
    ) -> None:
        """Test initialization with memory labels."""
        memory_labels = {"type": "scam", "category": "scenario"}

        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                memory_labels=memory_labels,
                objective_target=mock_objective_target,
            )
            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestScamProperties:
    """Tests for Scam properties."""

    def test_scenario_version_is_set(
        self,
        *,
        mock_objective_scorer: TrueFalseCompositeScorer,
        sample_objectives: List[str],
    ) -> None:
        """Test that scenario version is properly set."""
        scenario = Scam(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        assert scenario.version == 1

    @pytest.mark.asyncio
    async def test_no_target_duplication_async(
        self, *, mock_objective_target: PromptTarget, mock_memory_seed_groups: List[SeedGroup]
    ) -> None:
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(Scam, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = Scam()
            await scenario.initialize_async(objective_target=mock_objective_target)

            objective_target = scenario._objective_target
            scorer_target = scenario._scorer_config.objective_scorer  # type: ignore
            adversarial_target = scenario._adversarial_chat

            assert objective_target != scorer_target
            assert objective_target != adversarial_target
            assert scorer_target != adversarial_target
