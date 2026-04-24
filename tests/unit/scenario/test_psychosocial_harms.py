# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Psychosocial class."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedDataset, SeedGroup, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget, PromptChatTarget
from pyrit.scenario.scenarios.airt import (
    Psychosocial,
    PsychosocialStrategy,
)
from pyrit.scenario.scenarios.airt.psychosocial import ResolvedSeedData, SubharmConfig
from pyrit.score import FloatScaleThresholdScorer

SEED_DATASETS_PATH = DATASETS_PATH / "seed_datasets" / "local" / "airt"
SEED_PROMPT_LIST = list(SeedDataset.from_yaml_file(SEED_DATASETS_PATH / "psychosocial.prompt").get_values())


@pytest.fixture
def mock_memory_seed_groups() -> list[SeedGroup]:
    """Create mock seed groups that _get_default_seed_groups() would return."""
    return [SeedAttackGroup(seeds=[SeedObjective(value=prompt)]) for prompt in SEED_PROMPT_LIST]


@pytest.fixture
def mock_resolved_seed_data(mock_memory_seed_groups) -> ResolvedSeedData:
    """Create mock ResolvedSeedData for patching _resolve_seed_groups."""
    return ResolvedSeedData(seed_groups=mock_memory_seed_groups, subharm=None)


@pytest.fixture
def mock_dataset_config(mock_memory_seed_groups):
    """Create a mock dataset config that returns the seed groups."""
    from pyrit.scenario import DatasetConfiguration

    mock_config = MagicMock(spec=DatasetConfiguration)
    mock_config.get_all_seed_attack_groups.return_value = mock_memory_seed_groups
    mock_config.get_default_dataset_names.return_value = ["airt_psychosocial"]
    mock_config.has_data_source.return_value = True
    return mock_config


@pytest.fixture
def psychosocial_prompts() -> list[str]:
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
def mock_objective_target() -> PromptChatTarget:
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveTarget", class_module="test")
    return mock


@pytest.fixture
def mock_objective_scorer() -> FloatScaleThresholdScorer:
    mock = MagicMock(spec=FloatScaleThresholdScorer)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockObjectiveScorer", class_module="test")
    return mock


@pytest.fixture
def mock_adversarial_target() -> PromptChatTarget:
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = ComponentIdentifier(class_name="MockAdversarialTarget", class_module="test")
    return mock


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialInitialization:
    """Tests for Psychosocial initialization."""

    def test_init_with_default_objectives(
        self,
        *,
        mock_objective_scorer: FloatScaleThresholdScorer,
    ) -> None:
        """Test initialization with default objectives."""
        scenario = Psychosocial(objective_scorer=mock_objective_scorer)

        assert scenario.name == "Psychosocial"
        assert scenario.VERSION == 1

    def test_init_with_default_scorer(self) -> None:
        """Test initialization with default scorer."""
        scenario = Psychosocial()
        assert scenario._objective_scorer is not None

    def test_init_with_custom_scorer(self) -> None:
        """Test initialization with custom scorer."""
        scorer = MagicMock(spec=FloatScaleThresholdScorer)

        scenario = Psychosocial(objective_scorer=scorer)
        assert scenario._objective_scorer == scorer

    def test_init_default_adversarial_chat(self, *, mock_objective_scorer: FloatScaleThresholdScorer) -> None:
        scenario = Psychosocial(objective_scorer=mock_objective_scorer)
        assert isinstance(scenario._adversarial_chat, OpenAIChatTarget)

    def test_init_with_adversarial_chat(self, *, mock_objective_scorer: FloatScaleThresholdScorer) -> None:
        adversarial_chat = MagicMock(OpenAIChatTarget)
        adversarial_chat.get_identifier.return_value = ComponentIdentifier(
            class_name="CustomAdversary", class_module="test"
        )

        scenario = Psychosocial(
            adversarial_chat=adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._adversarial_chat == adversarial_chat

    def test_init_with_custom_subharm_configs(self, *, mock_objective_scorer: FloatScaleThresholdScorer) -> None:
        """Test initialization with custom subharm configurations."""

        custom_configs = {
            "imminent_crisis": SubharmConfig(
                crescendo_system_prompt_path="custom/crisis_crescendo.yaml",
                scoring_rubric_path="custom/crisis_rubric.yaml",
            ),
        }

        scenario = Psychosocial(
            subharm_configs=custom_configs,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._subharm_configs["imminent_crisis"].scoring_rubric_path == "custom/crisis_rubric.yaml"
        assert (
            scenario._subharm_configs["imminent_crisis"].crescendo_system_prompt_path == "custom/crisis_crescendo.yaml"
        )

    def test_init_with_custom_max_turns(self, *, mock_objective_scorer: FloatScaleThresholdScorer) -> None:
        """Test initialization with custom max_turns."""
        scenario = Psychosocial(max_turns=10, objective_scorer=mock_objective_scorer)
        assert scenario._max_turns == 10

    @pytest.mark.asyncio
    async def test_init_raises_exception_when_no_datasets_available_async(
        self, mock_objective_target, mock_objective_scorer
    ):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't provide objectives, let it try to load from empty memory
        scenario = Psychosocial(objective_scorer=mock_objective_scorer)

        # Error should occur during initialize_async when _get_atomic_attacks_async resolves seed groups
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialAttackGeneration:
    """Tests for Psychosocial attack generation."""

    @pytest.mark.asyncio
    async def test_attack_generation_for_all(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(objective_scorer=mock_objective_scorer)

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            assert len(atomic_attacks) > 0
            assert all(run.attack_technique is not None for run in atomic_attacks)

    @pytest.mark.asyncio
    async def test_attack_runs_include_objectives_async(
        self,
        *,
        mock_objective_target: PromptChatTarget,
        mock_objective_scorer: FloatScaleThresholdScorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ) -> None:
        """Test that attack runs include objectives for each seed prompt."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()

            for run in atomic_attacks:
                assert len(run.objectives) > 0

    @pytest.mark.asyncio
    async def test_get_atomic_attacks_async_returns_attacks(
        self,
        *,
        mock_objective_target: PromptChatTarget,
        mock_objective_scorer: FloatScaleThresholdScorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ) -> None:
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(
                objective_scorer=mock_objective_scorer,
            )

            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
            atomic_attacks = await scenario._get_atomic_attacks_async()
            assert len(atomic_attacks) > 0
            assert all(run.attack_technique is not None for run in atomic_attacks)


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialHarmsLifecycle:
    """Tests for Psychosocial lifecycle behavior."""

    @pytest.mark.asyncio
    async def test_initialize_async_with_max_concurrency(
        self,
        *,
        mock_objective_target: PromptChatTarget,
        mock_objective_scorer: FloatScaleThresholdScorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ) -> None:
        """Test initialization with custom max_concurrency."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                objective_target=mock_objective_target, max_concurrency=20, dataset_config=mock_dataset_config
            )
            assert scenario._max_concurrency == 20

    @pytest.mark.asyncio
    async def test_initialize_async_with_memory_labels(
        self,
        *,
        mock_objective_target: PromptChatTarget,
        mock_objective_scorer: FloatScaleThresholdScorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ) -> None:
        """Test initialization with memory labels."""
        memory_labels = {"type": "psychosocial", "category": "crisis"}

        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(objective_scorer=mock_objective_scorer)
            await scenario.initialize_async(
                memory_labels=memory_labels,
                objective_target=mock_objective_target,
                dataset_config=mock_dataset_config,
            )
            assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialProperties:
    """Tests for Psychosocial properties."""

    def test_scenario_version_is_set(
        self,
        *,
        mock_objective_scorer: FloatScaleThresholdScorer,
    ) -> None:
        """Test that scenario version is properly set."""
        scenario = Psychosocial(
            objective_scorer=mock_objective_scorer,
        )

        assert scenario.VERSION == 1

    def test_get_strategy_class(self) -> None:
        """Test that the strategy class is PsychosocialStrategy."""
        assert Psychosocial.get_strategy_class() == PsychosocialStrategy

    def test_get_default_strategy(self) -> None:
        """Test that the default strategy is ALL."""
        assert Psychosocial.get_default_strategy() == PsychosocialStrategy.ALL

    @pytest.mark.asyncio
    async def test_no_target_duplication_async(
        self,
        *,
        mock_objective_target: PromptChatTarget,
        mock_resolved_seed_data,
        mock_dataset_config,
    ) -> None:
        """Test that all three targets (adversarial, objective, scorer) are distinct."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial()
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)

            objective_target = scenario._objective_target
            adversarial_target = scenario._adversarial_chat

            assert objective_target != adversarial_target
            # Scorer target is embedded in the scorer itself
            assert scenario._objective_scorer is not None


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialTargetRequirements:
    """Tests for Psychosocial TARGET_REQUIREMENTS declaration and enforcement."""

    def test_target_requirements_declares_editable_history_natively(self):
        """Psychosocial runs CrescendoAttack, so it must require EDITABLE_HISTORY natively."""
        from pyrit.prompt_target.common.target_capabilities import CapabilityName

        assert CapabilityName.EDITABLE_HISTORY in Psychosocial.TARGET_REQUIREMENTS.native_required

    @pytest.mark.asyncio
    async def test_initialize_async_invokes_target_requirements_validate(
        self,
        mock_objective_target,
        mock_objective_scorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ):
        """initialize_async must delegate capability validation to TARGET_REQUIREMENTS.validate."""
        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(objective_scorer=mock_objective_scorer)
            with patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate") as mock_validate:
                await scenario.initialize_async(
                    objective_target=mock_objective_target,
                    dataset_config=mock_dataset_config,
                )

            # Scorers / attacks also validate; ensure the scenario itself validated objective_target.
            assert any(call.kwargs.get("target") is mock_objective_target for call in mock_validate.call_args_list), (
                "Expected TARGET_REQUIREMENTS.validate to be called with objective_target"
            )

    @pytest.mark.asyncio
    async def test_initialize_async_rejects_target_missing_editable_history(
        self,
        mock_objective_scorer,
        mock_resolved_seed_data,
        mock_dataset_config,
    ):
        """A target that does not natively support EDITABLE_HISTORY must be rejected."""
        from pyrit.prompt_target import PromptTarget
        from pyrit.prompt_target.common.target_capabilities import CapabilityName

        non_chat_target = MagicMock(spec=PromptTarget)
        non_chat_target.get_identifier.return_value = ComponentIdentifier(
            class_name="NonChatTarget", class_module="test"
        )
        # Configuration reports no EDITABLE_HISTORY support
        non_chat_target.configuration.includes.side_effect = (
            lambda *, capability: capability != CapabilityName.EDITABLE_HISTORY
        )

        with patch.object(Psychosocial, "_resolve_seed_groups", return_value=mock_resolved_seed_data):
            scenario = Psychosocial(objective_scorer=mock_objective_scorer)
            with pytest.raises(ValueError, match="editable_history"):
                await scenario.initialize_async(
                    objective_target=non_chat_target,
                    dataset_config=mock_dataset_config,
                )


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialHarmsStrategy:
    """Tests for PsychosocialHarmsStrategy enum."""

    def test_strategy_tags(self):
        """Test that strategies have correct tags."""
        assert PsychosocialStrategy.ALL.tags == {"all"}

    def test_aggregate_tags(self):
        """Test that only 'all' is an aggregate tag."""
        aggregate_tags = PsychosocialStrategy.get_aggregate_tags()
        assert "all" in aggregate_tags

    def test_strategy_values(self):
        """Test that strategy values are correct."""
        assert PsychosocialStrategy.ALL.value == "all"
