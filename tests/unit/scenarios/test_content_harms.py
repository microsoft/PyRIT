# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ContentHarms class."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.airt import (
    ContentHarms,
    ContentHarmsStrategy,
)
from pyrit.scenario.scenarios.airt.content_harms import (
    ContentHarmsDatasetConfiguration,
)
from pyrit.score import TrueFalseScorer


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test",
        class_description="",
        identifier_type="instance",
    )


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
def sample_objectives():
    """Create sample objectives for testing."""
    return ["objective1", "objective2", "objective3"]


@pytest.fixture(scope="class")
def mock_seed_groups():
    """Create mock seed groups for testing."""

    def create_seed_groups_for_strategy(strategy_name: str):
        """Helper to create seed groups for a given strategy."""
        return [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value=f"{strategy_name} objective 1"),
                    SeedPrompt(value=f"{strategy_name} prompt 1"),
                ]
            ),
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value=f"{strategy_name} objective 2"),
                    SeedPrompt(value=f"{strategy_name} prompt 2"),
                ]
            ),
        ]

    return create_seed_groups_for_strategy


@pytest.fixture(scope="class")
def mock_all_harm_objectives(mock_seed_groups):
    """Class-scoped fixture for all harm category objectives to reduce test code duplication."""
    return {
        "hate": mock_seed_groups("hate"),
        "fairness": mock_seed_groups("fairness"),
        "violence": mock_seed_groups("violence"),
        "sexual": mock_seed_groups("sexual"),
        "harassment": mock_seed_groups("harassment"),
        "misinformation": mock_seed_groups("misinformation"),
        "leakage": mock_seed_groups("leakage"),
    }


class TestContentHarmsStrategy:
    """Tests for the ContentHarmsStrategy enum."""

    def test_all_harm_categories_exist(self):
        """Test that all expected harm categories exist as strategies."""
        expected_categories = ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]
        strategy_values = [s.value for s in ContentHarmsStrategy if s != ContentHarmsStrategy.ALL]

        for category in expected_categories:
            assert category in strategy_values, f"Expected harm category '{category}' not found in strategies"

    def test_strategy_tags_are_sets(self):
        """Test that all strategy tags are set objects."""
        for strategy in ContentHarmsStrategy:
            assert isinstance(strategy.tags, set), f"Tags for {strategy.name} are not a set"

    def test_enum_members_count(self):
        """Test that we have the expected number of strategy members."""
        # ALL + 7 harm categories = 8 total
        assert len(list(ContentHarmsStrategy)) == 8

    def test_all_strategies_can_be_accessed_by_name(self):
        """Test that all strategies can be accessed by their name."""
        assert ContentHarmsStrategy.ALL == ContentHarmsStrategy["ALL"]
        assert ContentHarmsStrategy.Hate == ContentHarmsStrategy["Hate"]
        assert ContentHarmsStrategy.Fairness == ContentHarmsStrategy["Fairness"]
        assert ContentHarmsStrategy.Violence == ContentHarmsStrategy["Violence"]
        assert ContentHarmsStrategy.Sexual == ContentHarmsStrategy["Sexual"]
        assert ContentHarmsStrategy.Harassment == ContentHarmsStrategy["Harassment"]
        assert ContentHarmsStrategy.Misinformation == ContentHarmsStrategy["Misinformation"]
        assert ContentHarmsStrategy.Leakage == ContentHarmsStrategy["Leakage"]

    def test_all_strategies_can_be_accessed_by_value(self):
        """Test that all strategies can be accessed by their value."""
        assert ContentHarmsStrategy("all") == ContentHarmsStrategy.ALL
        assert ContentHarmsStrategy("hate") == ContentHarmsStrategy.Hate
        assert ContentHarmsStrategy("fairness") == ContentHarmsStrategy.Fairness
        assert ContentHarmsStrategy("violence") == ContentHarmsStrategy.Violence
        assert ContentHarmsStrategy("sexual") == ContentHarmsStrategy.Sexual
        assert ContentHarmsStrategy("harassment") == ContentHarmsStrategy.Harassment
        assert ContentHarmsStrategy("misinformation") == ContentHarmsStrategy.Misinformation
        assert ContentHarmsStrategy("leakage") == ContentHarmsStrategy.Leakage

    def test_strategies_are_unique(self):
        """Test that all strategy values are unique."""
        values = [s.value for s in ContentHarmsStrategy]
        assert len(values) == len(set(values)), "Strategy values are not unique"

    def test_strategy_iteration(self):
        """Test that we can iterate over all strategies."""
        strategies = list(ContentHarmsStrategy)
        assert len(strategies) == 8
        assert ContentHarmsStrategy.ALL in strategies
        assert ContentHarmsStrategy.Hate in strategies

    def test_strategy_comparison(self):
        """Test that strategy comparison works correctly."""
        assert ContentHarmsStrategy.Hate == ContentHarmsStrategy.Hate
        assert ContentHarmsStrategy.Hate != ContentHarmsStrategy.Violence
        assert ContentHarmsStrategy.ALL != ContentHarmsStrategy.Hate

    def test_strategy_hash(self):
        """Test that strategies can be hashed and used in sets/dicts."""
        strategy_set = {ContentHarmsStrategy.Hate, ContentHarmsStrategy.Violence}
        assert len(strategy_set) == 2
        assert ContentHarmsStrategy.Hate in strategy_set

        strategy_dict = {ContentHarmsStrategy.Hate: "hate_value"}
        assert strategy_dict[ContentHarmsStrategy.Hate] == "hate_value"

    def test_strategy_string_representation(self):
        """Test string representation of strategies."""
        assert "Hate" in str(ContentHarmsStrategy.Hate)
        assert "ALL" in str(ContentHarmsStrategy.ALL)

    def test_invalid_strategy_value_raises_error(self):
        """Test that accessing invalid strategy value raises ValueError."""
        with pytest.raises(ValueError):
            ContentHarmsStrategy("invalid_strategy")

    def test_invalid_strategy_name_raises_error(self):
        """Test that accessing invalid strategy name raises KeyError."""
        with pytest.raises(KeyError):
            ContentHarmsStrategy["InvalidStrategy"]

    def test_get_aggregate_tags_includes_harm_categories(self):
        """Test that get_aggregate_tags includes 'all' tag."""
        aggregate_tags = ContentHarmsStrategy.get_aggregate_tags()

        # The simple implementation only returns the 'all' tag
        assert "all" in aggregate_tags
        assert isinstance(aggregate_tags, set)

    def test_get_aggregate_tags_returns_set(self):
        """Test that get_aggregate_tags returns a set."""
        aggregate_tags = ContentHarmsStrategy.get_aggregate_tags()
        assert isinstance(aggregate_tags, set)

    def test_get_aggregate_strategies(self):
        """Test that ALL aggregate expands to all individual harm strategies."""
        # The ALL strategy should include all individual harm categories
        all_strategies = list(ContentHarmsStrategy)
        assert len(all_strategies) == 8  # ALL + 7 harm categories


@pytest.mark.usefixtures("patch_central_database")
class TestContentHarmsBasic:
    """Basic tests for ContentHarms initialization and properties."""

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_minimal_parameters(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test initialization with only required parameters."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        # Constructor should set adversarial chat and basic metadata
        assert scenario._adversarial_chat == mock_adversarial_target
        assert scenario.name == "Content Harms"
        assert scenario.version == 1

        # Initialization populates objective target and scenario composites
        await scenario.initialize_async(objective_target=mock_objective_target)

        assert scenario._objective_target == mock_objective_target

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_custom_strategies(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_seed_groups,
    ):
        """Test initialization with custom harm strategies."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = {
            "hate": mock_seed_groups("hate"),
            "fairness": mock_seed_groups("fairness"),
        }

        strategies = [ContentHarmsStrategy.Hate, ContentHarmsStrategy.Fairness]

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=strategies)

        # Prepared composites should match provided strategies
        assert len(scenario._scenario_composites) == 2

    def test_initialization_with_custom_scorer(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Test initialization with custom objective scorer."""
        scenario = ContentHarms(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )

        # The scorer is stored in _scorer_config.objective_scorer
        assert scenario._scorer_config.objective_scorer == mock_objective_scorer

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_custom_max_concurrency(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test initialization with custom max concurrency."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=10)

        assert scenario._max_concurrency == 10

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_custom_dataset_path(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test initialization with custom seed dataset prefix."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target)

        # Just verify it initializes without error
        assert scenario is not None

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_defaults_to_all_strategy(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test that initialization defaults to ALL strategy when none provided."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target)

        # Should have strategies from the ALL aggregate
        assert len(scenario._scenario_composites) > 0

    def test_get_default_strategy_returns_all(self):
        """Test that get_default_strategy returns ALL strategy."""
        assert ContentHarms.get_default_strategy() == ContentHarmsStrategy.ALL

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.endpoint",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test_key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_get_default_adversarial_target(self, mock_objective_target):
        """Test default adversarial target creation."""
        scenario = ContentHarms()

        assert scenario._adversarial_chat is not None

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.endpoint",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test_key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    def test_get_default_scorer(self, mock_objective_target):
        """Test default scorer creation."""
        scenario = ContentHarms()

        assert scenario._objective_scorer is not None

    def test_scenario_version(self):
        """Test that scenario has correct version."""
        assert ContentHarms.version == 1

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.endpoint",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test_key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    @pytest.mark.asyncio
    async def test_initialize_raises_exception_when_no_datasets_available(
        self, mock_objective_target, mock_adversarial_target
    ):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _get_objectives_by_harm, let it try to load from empty memory
        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_max_retries(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test initialization with max_retries parameter."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target, max_retries=3)

        assert scenario._max_retries == 3

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarms._get_default_scorer")
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_memory_labels_are_stored(
        self,
        mock_get_seed_attack_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_all_harm_objectives,
    ):
        """Test that memory labels are properly stored."""
        mock_get_scorer.return_value = mock_objective_scorer
        mock_get_seed_attack_groups.return_value = mock_all_harm_objectives

        memory_labels = {"test_run": "123", "category": "harm"}

        scenario = ContentHarms(adversarial_chat=mock_adversarial_target)

        await scenario.initialize_async(objective_target=mock_objective_target, memory_labels=memory_labels)

        assert scenario._memory_labels == memory_labels

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    async def test_initialization_with_all_parameters(
        self,
        mock_get_seed_attack_groups,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_seed_groups,
    ):
        """Test initialization with all possible parameters."""
        mock_get_seed_attack_groups.return_value = {
            "hate": mock_seed_groups("hate"),
            "violence": mock_seed_groups("violence"),
        }

        memory_labels = {"test": "value"}
        strategies = [ContentHarmsStrategy.Hate, ContentHarmsStrategy.Violence]

        scenario = ContentHarms(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=strategies,
            memory_labels=memory_labels,
            max_concurrency=5,
            max_retries=2,
        )

        assert scenario._objective_target == mock_objective_target
        assert scenario._adversarial_chat == mock_adversarial_target
        assert scenario._scorer_config.objective_scorer == mock_objective_scorer
        assert scenario._memory_labels == memory_labels
        assert scenario._max_concurrency == 5
        assert scenario._max_retries == 2

    @pytest.mark.asyncio
    @patch("pyrit.scenario.scenarios.airt.content_harms.ContentHarmsDatasetConfiguration.get_seed_attack_groups")
    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.endpoint",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test_key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
        },
    )
    async def test_initialization_with_objectives_by_harm(
        self, mock_get_seed_attack_groups, mock_objective_target, mock_adversarial_target, mock_seed_groups
    ):
        """Test initialization with custom objectives_by_harm parameter."""
        # Setup custom objectives by harm
        custom_objectives = {
            "hate": mock_seed_groups("hate"),
            "violence": mock_seed_groups("violence"),
        }

        mock_get_seed_attack_groups.return_value = custom_objectives

        scenario = ContentHarms(
            adversarial_chat=mock_adversarial_target,
            objectives_by_harm=custom_objectives,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[ContentHarmsStrategy.Hate, ContentHarmsStrategy.Violence],
        )

        # Verify the objectives_by_harm is stored
        assert scenario._objectives_by_harm == custom_objectives

    @pytest.mark.parametrize(
        "harm_category", ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]
    )
    def test_harm_category_prompt_file_exists(self, harm_category):
        harm_dataset_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
        file_path = harm_dataset_path / f"{harm_category}.prompt"
        assert file_path.exists(), f"Missing file: {file_path}"  # Fails if file does not exist


class TestContentHarmsDatasetConfiguration:
    """Tests for the ContentHarmsDatasetConfiguration class."""

    def test_get_seed_attack_groups_returns_all_datasets_when_no_composites(self):
        """Test that get_seed_attack_groups returns all datasets when scenario_composites is None."""
        # Create mock seed groups for each dataset
        mock_groups = {
            "airt_hate": [SeedAttackGroup(seeds=[SeedObjective(value="hate obj")])],
            "airt_violence": [SeedAttackGroup(seeds=[SeedObjective(value="violence obj")])],
        }

        config = ContentHarmsDatasetConfiguration(
            dataset_names=["airt_hate", "airt_violence"],
        )

        with patch.object(config, "_load_seed_groups_for_dataset") as mock_load:
            mock_load.side_effect = lambda dataset_name: mock_groups.get(dataset_name, [])

            result = config.get_seed_attack_groups()

            # Without scenario_composites, returns dataset names as keys
            assert "airt_hate" in result
            assert "airt_violence" in result
            assert len(result) == 2

    def test_get_seed_attack_groups_filters_by_selected_harm_strategy(self):
        """Test that get_seed_attack_groups filters datasets by selected harm strategies."""
        mock_groups = {
            "airt_hate": [SeedAttackGroup(seeds=[SeedObjective(value="hate obj")])],
            "airt_violence": [SeedAttackGroup(seeds=[SeedObjective(value="violence obj")])],
            "airt_sexual": [SeedAttackGroup(seeds=[SeedObjective(value="sexual obj")])],
        }

        config = ContentHarmsDatasetConfiguration(
            dataset_names=["airt_hate", "airt_violence", "airt_sexual"],
            scenario_composites=[ScenarioCompositeStrategy(strategies=[ContentHarmsStrategy.Hate])],
        )

        with patch.object(config, "_load_seed_groups_for_dataset") as mock_load:
            mock_load.side_effect = lambda dataset_name: mock_groups.get(dataset_name, [])

            result = config.get_seed_attack_groups()

            # Should only return "hate" key (mapped from "airt_hate")
            assert "hate" in result
            assert "violence" not in result
            assert "sexual" not in result
            assert len(result) == 1

    def test_get_seed_attack_groups_maps_dataset_names_to_harm_names(self):
        """Test that dataset names are mapped to harm strategy names."""
        mock_groups = {
            "airt_hate": [SeedAttackGroup(seeds=[SeedObjective(value="hate obj")])],
            "airt_fairness": [SeedAttackGroup(seeds=[SeedObjective(value="fairness obj")])],
        }

        config = ContentHarmsDatasetConfiguration(
            dataset_names=["airt_hate", "airt_fairness"],
            scenario_composites=[
                ScenarioCompositeStrategy(strategies=[ContentHarmsStrategy.Hate]),
                ScenarioCompositeStrategy(strategies=[ContentHarmsStrategy.Fairness]),
            ],
        )

        with patch.object(config, "_load_seed_groups_for_dataset") as mock_load:
            mock_load.side_effect = lambda dataset_name: mock_groups.get(dataset_name, [])

            result = config.get_seed_attack_groups()

            # Keys should be harm names, not dataset names
            assert "hate" in result
            assert "fairness" in result
            assert "airt_hate" not in result
            assert "airt_fairness" not in result

    def test_get_seed_attack_groups_with_all_strategy_returns_all_harms(self):
        """Test that ALL strategy returns all harm categories."""
        all_datasets = [
            "airt_hate",
            "airt_fairness",
            "airt_violence",
            "airt_sexual",
            "airt_harassment",
            "airt_misinformation",
            "airt_leakage",
        ]
        mock_groups = {name: [SeedAttackGroup(seeds=[SeedObjective(value=f"{name} obj")])] for name in all_datasets}

        # ALL strategy expands to all individual harm strategies
        all_harms = ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]
        composites = [ScenarioCompositeStrategy(strategies=[ContentHarmsStrategy(harm)]) for harm in all_harms]

        config = ContentHarmsDatasetConfiguration(
            dataset_names=all_datasets,
            scenario_composites=composites,
        )

        with patch.object(config, "_load_seed_groups_for_dataset") as mock_load:
            mock_load.side_effect = lambda dataset_name: mock_groups.get(dataset_name, [])

            result = config.get_seed_attack_groups()

            # Should have all 7 harm categories
            assert len(result) == 7
            for harm in all_harms:
                assert harm in result

    def test_get_seed_attack_groups_applies_max_dataset_size(self):
        """Test that max_dataset_size is applied per dataset."""
        # Create 5 seed groups for the dataset
        mock_groups = {
            "airt_hate": [SeedAttackGroup(seeds=[SeedObjective(value=f"hate obj {i}")]) for i in range(5)],
        }

        config = ContentHarmsDatasetConfiguration(
            dataset_names=["airt_hate"],
            max_dataset_size=2,
            scenario_composites=[ScenarioCompositeStrategy(strategies=[ContentHarmsStrategy.Hate])],
        )

        with patch.object(config, "_load_seed_groups_for_dataset") as mock_load:
            mock_load.side_effect = lambda dataset_name: mock_groups.get(dataset_name, [])

            result = config.get_seed_attack_groups()

            # Should have at most 2 seed groups due to max_dataset_size
            assert "hate" in result
            assert len(result["hate"]) == 2

    def test_default_dataset_config_has_all_harm_datasets(self):
        """Test that default_dataset_config includes all 7 harm category datasets."""
        config = ContentHarms.default_dataset_config()

        assert isinstance(config, ContentHarmsDatasetConfiguration)
        dataset_names = config.get_default_dataset_names()

        expected_datasets = [
            "airt_hate",
            "airt_fairness",
            "airt_violence",
            "airt_sexual",
            "airt_harassment",
            "airt_misinformation",
            "airt_leakage",
        ]

        for expected in expected_datasets:
            assert expected in dataset_names

        assert len(dataset_names) == 7

    def test_default_dataset_config_has_max_dataset_size(self):
        """Test that default_dataset_config has max_dataset_size set to 4."""
        config = ContentHarms.default_dataset_config()

        assert config.max_dataset_size == 4
