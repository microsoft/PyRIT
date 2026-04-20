# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RedTeamAgent class."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.multi_turn.crescendo import CrescendoAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective
from pyrit.prompt_converter import Base64Converter
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.scenario import AtomicAttack
from pyrit.scenario.foundry import FoundryStrategy, RedTeamAgent
from pyrit.score import FloatScaleThresholdScorer, TrueFalseScorer


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test",
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_memory_seed_groups():
    """Create mock seed groups that _get_default_seed_groups() would return."""
    objectives = [
        "test objective 1",
        "test objective 2",
        "test objective 3",
        "test objective 4",
    ]
    return [SeedAttackGroup(seeds=[SeedObjective(value=obj)]) for obj in objectives]


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = {"__type__": "MockObjectiveTarget", "__module__": "test"}
    return mock


@pytest.fixture
def mock_adversarial_target():
    """Create a mock adversarial target for testing."""
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = {"__type__": "MockAdversarialTarget", "__module__": "test"}
    return mock


@pytest.fixture
def mock_objective_scorer():
    """Create a mock objective scorer for testing."""
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def mock_float_threshold_scorer():
    """Create a mock FloatScaleThresholdScorer for TAP tests."""
    mock = MagicMock(spec=FloatScaleThresholdScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockFloatScaleThresholdScorer")
    mock.threshold = 0.7
    return mock


@pytest.fixture
def sample_objectives():
    """Create sample objectives for testing."""
    return ["objective1", "objective2", "objective3"]


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryInitialization:
    """Tests for RedTeamAgent initialization."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_init_with_single_strategy(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test initialization with a single attack strategy."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.Base64],
            )
            assert scenario.atomic_attack_count > 0
            assert scenario.name == "RedTeamAgent"

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_init_with_multiple_strategies(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test initialization with multiple attack strategies."""
        strategies = [
            FoundryStrategy.Base64,
            FoundryStrategy.ROT13,
            FoundryStrategy.Leetspeak,
        ]

        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=strategies,
            )
            assert scenario.atomic_attack_count >= len(strategies)

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_init_with_custom_objectives(self, mock_objective_target, mock_objective_scorer, sample_objectives):
        """Test initialization with custom objectives."""
        scenario = RedTeamAgent(
            objectives=sample_objectives,
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        # objectives are stored but _seed_groups is resolved lazily
        assert scenario._objectives == sample_objectives

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_init_with_custom_adversarial_target(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Test initialization with custom adversarial target."""
        scenario = RedTeamAgent(
            adversarial_chat=mock_adversarial_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario._adversarial_chat == mock_adversarial_target

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_init_with_custom_scorer(self, mock_objective_target, mock_objective_scorer):
        """Test initialization with custom objective scorer."""
        scenario = RedTeamAgent(
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario._attack_scoring_config.objective_scorer == mock_objective_scorer

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_init_with_memory_labels(self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups):
        """Test initialization with memory labels."""
        memory_labels = {"test": "foundry", "category": "attack"}

        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            assert scenario._memory_labels == {}

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                memory_labels=memory_labels,
            )

            assert scenario._memory_labels == memory_labels

    @patch("pyrit.scenario.scenarios.foundry.red_team_agent.TrueFalseCompositeScorer")
    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            "AZURE_CONTENT_SAFETY_API_ENDPOINT": "https://test.content.azure.com/",
            "AZURE_CONTENT_SAFETY_API_KEY": "test-content-key",
        },
    )
    def test_init_creates_default_scorer_when_not_provided(
        self, mock_composite, mock_objective_target, mock_memory_seed_groups
    ):
        """Test that initialization creates default scorer when not provided."""
        # Mock the composite scorer
        mock_composite_instance = MagicMock(spec=TrueFalseScorer)
        mock_composite.return_value = mock_composite_instance

        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent()

            # Verify default scorer was created
            mock_composite.assert_called_once()

            # seed_groups are resolved lazily during _get_atomic_attacks_async
            assert scenario._objectives is None

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = RedTeamAgent(attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer))

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryStrategyNormalization:
    """Tests for attack strategy normalization."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_normalize_easy_strategies(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that EASY strategy expands to easy attack strategies."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.EASY],
            )
            # EASY should expand to multiple attack strategies
            assert scenario.atomic_attack_count > 1

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_normalize_moderate_strategies(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that MODERATE strategy expands to moderate attack strategies."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.MODERATE],
            )
            # MODERATE should expand to moderate attack strategies (currently only 1: Tense)
            assert scenario.atomic_attack_count >= 1

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_normalize_difficult_strategies(
        self, mock_objective_target, mock_float_threshold_scorer, mock_memory_seed_groups
    ):
        """Test that DIFFICULT strategy expands to difficult attack strategies."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            # DIFFICULT strategy includes TAP which requires FloatScaleThresholdScorer
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_float_threshold_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.DIFFICULT],
            )
            # DIFFICULT should expand to multiple attack strategies
            assert scenario.atomic_attack_count > 1

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_normalize_mixed_difficulty_levels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that multiple difficulty levels expand correctly."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.EASY, FoundryStrategy.MODERATE],
            )
            # Combined difficulty levels should expand to multiple strategies
            assert scenario.atomic_attack_count > 5  # EASY has 20, MODERATE has 1, combined should have more

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_normalize_with_specific_and_difficulty_levels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that specific strategies combined with difficulty levels work correctly."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[
                    FoundryStrategy.EASY,
                    FoundryStrategy.Base64,  # Specific strategy
                ],
            )
            # EASY expands to 20 strategies, but Base64 might already be in EASY, so at least 20
            assert scenario.atomic_attack_count >= 20


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryAttackCreation:
    """Tests for attack creation from strategies."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_get_attack_from_single_turn_strategy(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test creating an attack from a single-turn strategy."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.Base64],
            )

            # Get the composite strategy that was created during initialization
            composite_strategy = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_strategy(composite_strategy)

            assert isinstance(atomic_attack, AtomicAttack)
            assert atomic_attack.seed_groups == mock_memory_seed_groups

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_get_attack_from_multi_turn_strategy(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test creating a multi-turn attack strategy."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                adversarial_chat=mock_adversarial_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.Crescendo],
            )

            # Get the composite strategy that was created during initialization
            composite_strategy = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_strategy(composite_strategy)

            assert isinstance(atomic_attack, AtomicAttack)
            assert atomic_attack.seed_groups == mock_memory_seed_groups


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryGetAttack:
    """Tests for the _get_attack method."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_get_attack_single_turn_with_converters(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test creating a single-turn attack with converters."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.Base64],
            )

            attack = scenario._get_attack(
                attack_type=PromptSendingAttack,
                converters=[Base64Converter()],
            )

            assert isinstance(attack, PromptSendingAttack)

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_get_attack_multi_turn_with_adversarial_target(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test creating a multi-turn attack."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                adversarial_chat=mock_adversarial_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[FoundryStrategy.Crescendo],
            )

            attack = scenario._get_attack(
                attack_type=CrescendoAttack,
                converters=[],
            )

            assert isinstance(attack, CrescendoAttack)


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryAllStrategies:
    """Tests that all strategies can be instantiated."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.parametrize(
        "strategy",
        [
            FoundryStrategy.AnsiAttack,
            FoundryStrategy.AsciiArt,
            FoundryStrategy.AsciiSmuggler,
            FoundryStrategy.Atbash,
            FoundryStrategy.Base64,
            FoundryStrategy.Binary,
            FoundryStrategy.Caesar,
            FoundryStrategy.CharacterSpace,
            FoundryStrategy.CharSwap,
            FoundryStrategy.Diacritic,
            FoundryStrategy.Flip,
            FoundryStrategy.Leetspeak,
            FoundryStrategy.Morse,
            FoundryStrategy.ROT13,
            FoundryStrategy.SuffixAppend,
            FoundryStrategy.StringJoin,
            FoundryStrategy.Tense,
            FoundryStrategy.UnicodeConfusable,
            FoundryStrategy.UnicodeSubstitution,
            FoundryStrategy.Url,
            FoundryStrategy.Jailbreak,
        ],
    )
    @pytest.mark.asyncio
    async def test_all_single_turn_strategies_create_attack_runs(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, strategy
    ):
        """Test that all single-turn strategies can create attack runs."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[strategy],
            )

            # Get the composite strategy that was created during initialization
            composite_strategy = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_strategy(composite_strategy)
            assert isinstance(atomic_attack, AtomicAttack)

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.parametrize(
        "strategy",
        [
            FoundryStrategy.MultiTurn,
            FoundryStrategy.Crescendo,
        ],
    )
    @pytest.mark.asyncio
    async def test_all_multi_turn_strategies_create_attack_runs(
        self,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_memory_seed_groups,
        strategy,
    ):
        """Test that all multi-turn strategies can create attack runs."""
        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                adversarial_chat=mock_adversarial_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[strategy],
            )

            # Get the composite strategy that was created during initialization
            composite_strategy = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_strategy(composite_strategy)
            assert isinstance(atomic_attack, AtomicAttack)


@pytest.mark.usefixtures("patch_central_database")
class TestFoundryProperties:
    """Tests for RedTeamAgent properties and attributes."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_scenario_composites_set_after_initialize(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that scenario composites are set after initialize_async."""
        strategies = [FoundryStrategy.Base64, FoundryStrategy.ROT13]

        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
                include_baseline=False,
            )

            # Before initialize_async, composites should be empty
            assert len(scenario._scenario_composites) == 0

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=strategies,
            )

            # After initialize_async, composites should be set
            assert len(scenario._scenario_composites) == len(strategies)
            assert scenario.atomic_attack_count == len(strategies)

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_scenario_version_is_set(self, mock_objective_target, mock_objective_scorer):
        """Test that scenario version is properly set."""
        scenario = RedTeamAgent(
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario.version == 1

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_scenario_atomic_attack_count_matches_strategies(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups
    ):
        """Test that atomic attack count is reasonable for the number of strategies."""
        strategies = [
            FoundryStrategy.Base64,
            FoundryStrategy.ROT13,
            FoundryStrategy.Leetspeak,
        ]

        with patch.object(RedTeamAgent, "_resolve_seed_groups", return_value=mock_memory_seed_groups):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=strategies,
            )
            # Should have at least as many runs as specific strategies provided
            assert scenario.atomic_attack_count >= len(strategies)
