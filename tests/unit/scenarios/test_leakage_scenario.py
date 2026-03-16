# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Leakage class."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import CrescendoAttack, PromptSendingAttack, RolePlayAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedDataset, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget, PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.airt import Leakage, LeakageStrategy
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
def mock_memory_seeds():
    leakage_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
    seed_prompts = list(SeedDataset.from_yaml_file(leakage_path / "leakage.prompt").get_values())
    return [SeedObjective(value=prompt) for prompt in seed_prompts]


@pytest.fixture
def mock_dataset_config(mock_memory_seeds):
    """Create a mock dataset config that returns the seed groups."""
    seed_groups = [SeedAttackGroup(seeds=[seed]) for seed in mock_memory_seeds]
    mock_config = MagicMock(spec=DatasetConfiguration)
    mock_config.get_all_seed_attack_groups.return_value = seed_groups
    mock_config.get_default_dataset_names.return_value = ["airt_leakage"]
    mock_config.has_data_source.return_value = True
    return mock_config


@pytest.fixture
def first_letter_strategy():
    return LeakageStrategy.FirstLetter


@pytest.fixture
def crescendo_strategy():
    return LeakageStrategy.Crescendo


@pytest.fixture
def image_strategy():
    return LeakageStrategy.Image


@pytest.fixture
def role_play_strategy():
    return LeakageStrategy.RolePlay


@pytest.fixture
def leakage_prompts():
    """The default leakage prompts."""
    leakage_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
    return list(SeedDataset.from_yaml_file(leakage_path / "leakage.prompt").get_values())


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
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


@pytest.fixture
def mock_adversarial_target():
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = _mock_target_id("MockAdversarialTarget")
    return mock


@pytest.fixture
def sample_objectives() -> list[str]:
    return ["test leakage prompt 1", "test leakage prompt 2"]


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageInitialization:
    """Tests for Leakage initialization."""

    def test_init_with_custom_objectives(self, mock_objective_scorer, sample_objectives):
        """Test initialization with custom objectives."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        assert len(scenario._objectives) == len(sample_objectives)
        assert scenario.name == "Leakage"
        assert scenario.VERSION == 1

    def test_init_with_default_objectives(self, mock_objective_scorer, leakage_prompts, mock_memory_seeds):
        """Test initialization with default objectives."""
        with patch.object(Leakage, "_get_default_objectives", return_value=leakage_prompts):
            scenario = Leakage(objective_scorer=mock_objective_scorer)

            assert scenario._objectives == leakage_prompts
            assert scenario.name == "Leakage"
            assert scenario.VERSION == 1

    def test_init_with_default_scorer(self, mock_memory_seeds):
        """Test initialization with default scorer."""
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage()
            assert scenario._objective_scorer_identifier

    def test_default_scorer_uses_leakage_yaml(self):
        """Test that the default scorer uses leakage.yaml, not privacy.yaml."""
        scorer_path = DATASETS_PATH / "score" / "true_false_question" / "leakage.yaml"
        assert scorer_path.exists(), f"Expected leakage.yaml scorer at {scorer_path}"

    def test_init_with_custom_scorer(self, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with custom scorer."""
        scorer = MagicMock(TrueFalseCompositeScorer)
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(objective_scorer=scorer)
            assert isinstance(scenario._scorer_config, AttackScoringConfig)

    def test_init_default_adversarial_chat(self, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with default adversarial chat."""
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(
                objective_scorer=mock_objective_scorer,
            )

            assert isinstance(scenario._adversarial_chat, OpenAIChatTarget)
            assert scenario._adversarial_chat._temperature == 1.2

    def test_init_with_adversarial_chat(self, mock_objective_scorer, mock_memory_seeds):
        """Test initialization with adversarial chat (for multi-turn attack variations)."""
        adversarial_chat = MagicMock(OpenAIChatTarget)
        adversarial_chat.get_identifier.return_value = _mock_target_id("CustomAdversary")

        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            assert scenario._adversarial_chat == adversarial_chat
            assert scenario._adversarial_config.target == adversarial_chat

    def test_init_raises_exception_when_no_datasets_available(self, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _get_default_objectives, let it try to load from empty memory
        with pytest.raises(ValueError, match="Dataset is not available or failed to load"):
            Leakage(objective_scorer=mock_objective_scorer)

    def test_init_include_baseline_true_by_default(self, mock_objective_scorer, sample_objectives):
        """Test that include_baseline defaults to True."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._include_baseline is True

    def test_init_include_baseline_false(self, mock_objective_scorer, sample_objectives):
        """Test that include_baseline can be set to False."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
            include_baseline=False,
        )
        assert scenario._include_baseline is False


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageAttackGeneration:
    """Tests for Leakage attack generation."""

    @pytest.mark.asyncio
    async def test_attack_generation_for_all(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            assert len(atomic_attacks) > 0
            assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_attack_generation_for_first_letter(
        self,
        mock_objective_target,
        mock_objective_scorer,
        sample_objectives,
        first_letter_strategy,
        mock_dataset_config,
    ):
        """Test that the first letter attack generation works."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[first_letter_strategy],
            dataset_config=mock_dataset_config,
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()
        for run in atomic_attacks:
            assert isinstance(run._attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attack_generation_for_crescendo(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, crescendo_strategy, mock_dataset_config
    ):
        """Test that the crescendo attack generation works."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[crescendo_strategy],
            dataset_config=mock_dataset_config,
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()

        for run in atomic_attacks:
            assert isinstance(run._attack, CrescendoAttack)

    @pytest.mark.asyncio
    async def test_attack_generation_for_image(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, image_strategy, mock_dataset_config
    ):
        """Test that the image attack generation works."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[image_strategy],
            dataset_config=mock_dataset_config,
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()
        for run in atomic_attacks:
            assert isinstance(run._attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attack_generation_for_role_play(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, role_play_strategy, mock_dataset_config
    ):
        """Test that the role play attack generation works."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[role_play_strategy],
            dataset_config=mock_dataset_config,
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()
        for run in atomic_attacks:
            assert isinstance(run._attack, RolePlayAttack)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, mock_dataset_config
    ):
        """Test that attack runs include objectives for each seed prompt."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
        atomic_attacks = await scenario._get_atomic_attacks_async()

        # Check that objectives are created for each seed prompt
        for run in atomic_attacks:
            assert len(run.objectives) == len(sample_objectives)
            for i, objective in enumerate(run.objectives):
                assert sample_objectives[i] in objective

    @pytest.mark.asyncio
    async def test_get_atomic_attacks_async_returns_attacks(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, mock_dataset_config
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
        atomic_attacks = await scenario._get_atomic_attacks_async()
        assert len(atomic_attacks) > 0
        assert all(hasattr(run, "_attack") for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_unknown_strategy_raises_value_error(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, mock_dataset_config
    ):
        """Test that an unknown strategy raises ValueError."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )
        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

        with pytest.raises(ValueError, match="Unknown LeakageStrategy"):
            await scenario._get_atomic_attack_from_strategy_async("unknown_strategy")


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageLifecycle:
    """
    Tests for Leakage lifecycle, including initialize_async and execution.
    """

    @pytest.mark.asyncio
    async def test_initialize_async_with_max_concurrency(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds, mock_dataset_config
    ):
        """Test initialization with custom max_concurrency."""
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                objective_target=mock_objective_target, max_concurrency=20, dataset_config=mock_dataset_config
            )
            assert scenario._max_concurrency == 20

    @pytest.mark.asyncio
    async def test_initialize_async_with_memory_labels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seeds, mock_dataset_config
    ):
        """Test initialization with memory labels."""
        memory_labels = {"test": "leakage", "category": "scenario"}

        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage(
                objective_scorer=mock_objective_scorer,
            )
            await scenario.initialize_async(
                memory_labels=memory_labels,
                objective_target=mock_objective_target,
                dataset_config=mock_dataset_config,
            )

            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageProperties:
    """
    Tests for Leakage properties and attributes.
    """

    def test_scenario_version_is_set(self, mock_objective_scorer, sample_objectives):
        """Test that scenario version is properly set."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        assert scenario.VERSION == 1

    def test_get_strategy_class_returns_leakage_strategy(self):
        """Test that get_strategy_class returns LeakageStrategy."""
        assert Leakage.get_strategy_class() == LeakageStrategy

    def test_get_default_strategy_returns_all(self):
        """Test that get_default_strategy returns LeakageStrategy.ALL."""
        assert Leakage.get_default_strategy() == LeakageStrategy.ALL

    def test_required_datasets_returns_airt_leakage(self):
        """Test that required_datasets returns airt_leakage."""
        assert Leakage.required_datasets() == ["airt_leakage"]

    @pytest.mark.asyncio
    async def test_no_target_duplication(self, mock_objective_target, mock_memory_seeds, mock_dataset_config):
        """Test that all three targets (adversarial, object, scorer) are distinct."""
        with patch.object(Leakage, "_get_default_objectives", return_value=[seed.value for seed in mock_memory_seeds]):
            scenario = Leakage()
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

            objective_target = scenario._objective_target

            # This works because TrueFalseCompositeScorer subclasses TrueFalseScorer,
            # but TrueFalseScorer itself (the type for ScorerConfig) does not have ._scorers.
            scorer_target = scenario._scorer_config.objective_scorer._scorers[0]  # type: ignore[arg-type]
            adversarial_target = scenario._adversarial_chat

            assert objective_target != scorer_target
            assert objective_target != adversarial_target
            assert scorer_target != adversarial_target


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageStrategyEnum:
    """Tests for LeakageStrategy enum."""

    def test_strategy_all_exists(self):
        """Test that ALL strategy exists."""
        assert LeakageStrategy.ALL is not None
        assert LeakageStrategy.ALL.value == "all"
        assert "all" in LeakageStrategy.ALL.tags

    def test_strategy_first_letter_exists(self):
        """Test that FirstLetter strategy exists."""
        assert LeakageStrategy.FirstLetter is not None
        assert LeakageStrategy.FirstLetter.value == "first_letter"
        assert "single_turn" in LeakageStrategy.FirstLetter.tags

    def test_strategy_crescendo_exists(self):
        """Test that Crescendo strategy exists."""
        assert LeakageStrategy.Crescendo is not None
        assert LeakageStrategy.Crescendo.value == "crescendo"
        assert "multi_turn" in LeakageStrategy.Crescendo.tags

    def test_strategy_image_exists(self):
        """Test that Image strategy exists."""
        assert LeakageStrategy.Image is not None
        assert LeakageStrategy.Image.value == "image"
        assert "single_turn" in LeakageStrategy.Image.tags

    def test_strategy_role_play_exists(self):
        """Test that RolePlay strategy exists."""
        assert LeakageStrategy.RolePlay is not None
        assert LeakageStrategy.RolePlay.value == "role_play"
        assert "single_turn" in LeakageStrategy.RolePlay.tags

    def test_strategy_single_turn_aggregate_exists(self):
        """Test that SINGLE_TURN aggregate strategy exists."""
        assert LeakageStrategy.SINGLE_TURN is not None
        assert LeakageStrategy.SINGLE_TURN.value == "single_turn"
        assert "single_turn" in LeakageStrategy.SINGLE_TURN.tags

    def test_strategy_multi_turn_aggregate_exists(self):
        """Test that MULTI_TURN aggregate strategy exists."""
        assert LeakageStrategy.MULTI_TURN is not None
        assert LeakageStrategy.MULTI_TURN.value == "multi_turn"
        assert "multi_turn" in LeakageStrategy.MULTI_TURN.tags

    def test_strategy_ip_aggregate_exists(self):
        """Test that IP aggregate strategy exists for intellectual property focused attacks."""
        assert LeakageStrategy.IP is not None
        assert LeakageStrategy.IP.value == "ip"
        assert "ip" in LeakageStrategy.IP.tags

    def test_strategy_sensitive_data_aggregate_exists(self):
        """Test that SENSITIVE_DATA aggregate strategy exists for credentials/secrets attacks."""
        assert LeakageStrategy.SENSITIVE_DATA is not None
        assert LeakageStrategy.SENSITIVE_DATA.value == "sensitive_data"
        assert "sensitive_data" in LeakageStrategy.SENSITIVE_DATA.tags

    def test_first_letter_has_ip_tag(self):
        """Test that FirstLetter has ip tag for copyright extraction."""
        assert "ip" in LeakageStrategy.FirstLetter.tags

    def test_role_play_has_sensitive_data_tag(self):
        """Test that RolePlay has sensitive_data tag for system prompt extraction."""
        assert "sensitive_data" in LeakageStrategy.RolePlay.tags


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageImageStrategy:
    """Tests for Leakage image strategy implementation."""

    def test_ensure_blank_image_exists_creates_image(self, mock_objective_scorer, sample_objectives, tmp_path):
        """Test that _ensure_blank_image_exists creates a blank image file."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        test_image_path = str(tmp_path / "test_blank.png")
        scenario._ensure_blank_image_exists(test_image_path)

        # Verify the image was created
        from pathlib import Path

        assert Path(test_image_path).exists()

        # Verify it's a valid image with correct dimensions
        from PIL import Image

        with Image.open(test_image_path) as img:
            assert img.size == (800, 600)
            assert img.mode == "RGB"

    def test_ensure_blank_image_exists_does_not_overwrite(self, mock_objective_scorer, sample_objectives, tmp_path):
        """Test that _ensure_blank_image_exists doesn't overwrite existing image."""
        from pathlib import Path

        from PIL import Image

        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        # Create a different-sized image first
        test_image_path = str(tmp_path / "existing.png")
        existing_img = Image.new("RGB", (100, 100), color=(255, 0, 0))  # Red 100x100
        existing_img.save(test_image_path)
        original_mtime = Path(test_image_path).stat().st_mtime

        # Call _ensure_blank_image_exists - it should not modify the existing file
        scenario._ensure_blank_image_exists(test_image_path)

        # Verify the file was not modified
        assert Path(test_image_path).stat().st_mtime == original_mtime

        # Verify it's still the original image
        with Image.open(test_image_path) as img:
            assert img.size == (100, 100)  # Original size, not 800x600

    def test_ensure_blank_image_exists_creates_parent_directories(
        self, mock_objective_scorer, sample_objectives, tmp_path
    ):
        """Test that _ensure_blank_image_exists creates parent directories."""
        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        nested_path = str(tmp_path / "nested" / "dirs" / "image.png")
        scenario._ensure_blank_image_exists(nested_path)

        from pathlib import Path

        assert Path(nested_path).exists()

    @pytest.mark.asyncio
    async def test_image_strategy_uses_add_image_text_converter(
        self, mock_objective_target, mock_objective_scorer, sample_objectives, image_strategy, mock_dataset_config
    ):
        """Test that the image strategy uses AddImageTextConverter (not AddTextImageConverter)."""
        from pyrit.prompt_converter import AddImageTextConverter

        scenario = Leakage(
            objectives=sample_objectives,
            objective_scorer=mock_objective_scorer,
        )

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=[image_strategy],
            dataset_config=mock_dataset_config,
        )
        atomic_attacks = await scenario._get_atomic_attacks_async()

        # Verify the attack uses AddImageTextConverter
        for attack in atomic_attacks:
            converters = attack._attack._request_converters
            assert len(converters) > 0
            # Check that the first converter is AddImageTextConverter
            first_converter = converters[0].converters[0]
            assert isinstance(first_converter, AddImageTextConverter)
