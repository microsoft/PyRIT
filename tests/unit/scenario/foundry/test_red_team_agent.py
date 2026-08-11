# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RedTeamAgent class."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyrit.converter import (
    AnsiAttackConverter,
    AsciiArtConverter,
    AtbashConverter,
    Base64Converter,
    CaesarConverter,
    CharacterSpaceConverter,
    CharSwapConverter,
    Converter,
    DiacriticConverter,
    FlipConverter,
    LeetspeakConverter,
    MorseConverter,
    ROT13Converter,
    StringJoinConverter,
    SuffixAppendConverter,
    TenseConverter,
    TextJailbreakConverter,
    UnicodeConfusableConverter,
    UnicodeSubstitutionConverter,
    UrlConverter,
)
from pyrit.converter.binary_converter import BinaryConverter
from pyrit.converter.token_smuggling.ascii_smuggler_converter import AsciiSmugglerConverter
from pyrit.executor.attack import (
    CrescendoAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.core.attack_strategy import AttackStrategy
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import AtomicAttack, DatasetAttackConfiguration
from pyrit.scenario.foundry import (  # type: ignore[ty:unresolved-import]
    FoundryComposite,
    FoundryTechnique,
    RedTeamAgent,
)
from pyrit.score import FloatScaleThresholdScorer, TrueFalseScorer


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


def _converter_technique_cases() -> list[tuple[FoundryTechnique, type[Converter]]]:
    """Return every supported Foundry converter mapping."""
    return [
        (FoundryTechnique.AnsiAttack, AnsiAttackConverter),
        (FoundryTechnique.AsciiArt, AsciiArtConverter),
        (FoundryTechnique.AsciiSmuggler, AsciiSmugglerConverter),
        (FoundryTechnique.Atbash, AtbashConverter),
        (FoundryTechnique.Base64, Base64Converter),
        (FoundryTechnique.Binary, BinaryConverter),
        (FoundryTechnique.Caesar, CaesarConverter),
        (FoundryTechnique.CharacterSpace, CharacterSpaceConverter),
        (FoundryTechnique.CharSwap, CharSwapConverter),
        (FoundryTechnique.Diacritic, DiacriticConverter),
        (FoundryTechnique.Flip, FlipConverter),
        (FoundryTechnique.Leetspeak, LeetspeakConverter),
        (FoundryTechnique.Morse, MorseConverter),
        (FoundryTechnique.ROT13, ROT13Converter),
        (FoundryTechnique.SuffixAppend, SuffixAppendConverter),
        (FoundryTechnique.StringJoin, StringJoinConverter),
        (FoundryTechnique.Tense, TenseConverter),
        (FoundryTechnique.UnicodeConfusable, UnicodeConfusableConverter),
        (FoundryTechnique.UnicodeSubstitution, UnicodeSubstitutionConverter),
        (FoundryTechnique.Url, UrlConverter),
        (FoundryTechnique.Jailbreak, TextJailbreakConverter),
    ]


def _attack_technique_cases() -> list[tuple[FoundryTechnique | None, type[AttackStrategy[Any, Any]], int | None]]:
    """Return every supported Foundry attack mapping plus the baseline."""
    return [
        (None, PromptSendingAttack, None),
        (FoundryTechnique.Crescendo, CrescendoAttack, None),
        (FoundryTechnique.MultiTurn, RedTeamingAttack, None),
        (FoundryTechnique.Pair, TreeOfAttacksWithPruningAttack, 1),
        (FoundryTechnique.Tap, TreeOfAttacksWithPruningAttack, 3),
    ]


def _get_request_converters(atomic_attack: AtomicAttack) -> list[Converter]:
    """Return request converters from an atomic attack in execution order."""
    return [
        converter
        for configuration in atomic_attack.attack_technique.attack.get_request_converters()
        for converter in configuration.converters
    ]


@pytest.fixture
def mock_memory_seed_groups():
    """Create mock seed groups that _get_default_seed_groups() would return."""
    objectives = [
        "test objective 1",
        "test objective 2",
        "test objective 3",
        "test objective 4",
    ]
    return [AttackSeedGroup(seeds=[SeedObjective(value=obj)]) for obj in objectives]


@pytest.fixture
def mock_dataset_config(mock_memory_seed_groups):
    """Create a mock dataset config that returns the seed groups."""
    mock_config = MagicMock(spec=DatasetAttackConfiguration)
    mock_config.get_attack_seed_groups_async = AsyncMock(return_value=mock_memory_seed_groups)
    mock_config.dataset_names = ["foundry_red_team"]
    return mock_config


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_adversarial_target():
    """Create a mock adversarial target for testing."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockAdversarialTarget")
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
def configured_scenario(
    mock_objective_target: PromptTarget,
    mock_adversarial_target: PromptTarget,
    mock_float_threshold_scorer: FloatScaleThresholdScorer,
) -> RedTeamAgent:
    """Create a scenario ready for direct technique materialization."""
    scenario = RedTeamAgent(
        adversarial_chat=mock_adversarial_target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=mock_float_threshold_scorer),
    )
    scenario._objective_target = mock_objective_target
    return scenario


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
class TestFoundryInitialization:
    """Tests for RedTeamAgent initialization."""

    async def test_init_with_single_technique(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test initialization with a single attack technique."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Base64],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            assert scenario.atomic_attack_count > 0
            assert scenario.name == "RedTeamAgent"

    async def test_init_with_multiple_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test initialization with multiple attack techniques."""
        techniques = [
            FoundryTechnique.Base64,
            FoundryTechnique.ROT13,
            FoundryTechnique.Leetspeak,
        ]

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": techniques,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            assert scenario.atomic_attack_count >= len(techniques)

    def test_init_with_custom_adversarial_target(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Test initialization with custom adversarial target."""
        scenario = RedTeamAgent(
            adversarial_chat=mock_adversarial_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario._adversarial_chat == mock_adversarial_target

    def test_init_with_custom_scorer(self, mock_objective_target, mock_objective_scorer):
        """Test initialization with custom objective scorer."""
        scenario = RedTeamAgent(
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario._attack_scoring_config.objective_scorer == mock_objective_scorer

    async def test_init_with_memory_labels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test initialization with memory labels."""
        memory_labels = {"test": "foundry", "category": "attack"}

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            assert scenario._memory_labels == {}

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "memory_labels": memory_labels,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            assert scenario._memory_labels == memory_labels

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_init_creates_default_scorer_when_not_provided(
        self, mock_get_scorer, mock_objective_target, mock_memory_seed_groups
    ):
        """Test that initialization creates default scorer when not provided."""
        mock_scorer_instance = MagicMock(spec=TrueFalseScorer)
        mock_get_scorer.return_value = mock_scorer_instance

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent()

            # Verify default scorer was used
            mock_get_scorer.assert_called_once()
            assert scenario._attack_scoring_config.objective_scorer == mock_scorer_instance

            # seed_groups are resolved lazily during initialize_async
            assert scenario._attack_scoring_config.objective_scorer == mock_scorer_instance

    async def test_init_raises_exception_when_no_datasets_available(self, mock_objective_target, mock_objective_scorer):
        """Test that initialization raises ValueError when datasets are not available in memory."""
        # Don't mock _resolve_seed_groups, let it try to load from empty memory
        scenario = RedTeamAgent(attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer))

        # Error should occur during initialize_async when it resolves seed groups.
        # Neutralize the provider fetch so the empty-memory path raises loudly instead of fetching.
        with patch(
            "pyrit.scenario.core.dataset_configuration.DatasetConfiguration._fetch_dataset_async",
            new_callable=AsyncMock,
        ):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                }
            )
            with pytest.raises(ValueError, match="could not be loaded"):
                await scenario.initialize_async()


@pytest.mark.usefixtures(*FIXTURES)
class TestFoundryTechniqueNormalization:
    """Tests for attack technique normalization."""

    async def test_normalize_easy_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that EASY technique expands to easy attack techniques."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.EASY],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # EASY should expand to multiple attack techniques
            assert scenario.atomic_attack_count > 1

    async def test_normalize_moderate_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that MODERATE technique expands to moderate attack techniques."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.MODERATE],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # MODERATE should expand to moderate attack techniques (currently only 1: Tense)
            assert scenario.atomic_attack_count >= 1

    async def test_normalize_difficult_techniques(
        self, mock_objective_target, mock_float_threshold_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that DIFFICULT technique expands to difficult attack techniques."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            # DIFFICULT technique includes TAP which requires FloatScaleThresholdScorer
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_float_threshold_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.DIFFICULT],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # DIFFICULT should expand to multiple attack techniques
            assert scenario.atomic_attack_count > 1

    async def test_normalize_mixed_difficulty_levels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that multiple difficulty levels expand correctly."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.EASY, FoundryTechnique.MODERATE],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # Combined difficulty levels should expand to multiple techniques
            assert scenario.atomic_attack_count > 5  # EASY has 20, MODERATE has 1, combined should have more

    async def test_normalize_with_specific_and_difficulty_levels(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that specific techniques combined with difficulty levels work correctly."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [
                        FoundryTechnique.EASY,
                        FoundryTechnique.Base64,  # Specific technique
                    ],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # EASY expands to 20 techniques, but Base64 might already be in EASY, so at least 20
            assert scenario.atomic_attack_count >= 20


@pytest.mark.usefixtures(*FIXTURES)
class TestFoundryAttackCreation:
    """Tests for attack creation from techniques."""

    async def test_get_attack_from_single_turn_technique(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test creating an attack from a single-turn technique."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Base64],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            # Get the composite technique that was created during initialization
            composite_technique = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_technique(
                composite=composite_technique, seed_groups=mock_memory_seed_groups
            )

            assert isinstance(atomic_attack, AtomicAttack)
            assert atomic_attack.seed_groups == mock_memory_seed_groups

    async def test_get_attack_from_multi_turn_technique(
        self,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_memory_seed_groups,
        mock_dataset_config,
    ):
        """Test creating a multi-turn attack technique."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                adversarial_chat=mock_adversarial_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Crescendo],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            # Get the composite technique that was created during initialization
            composite_technique = scenario._scenario_composites[0]
            atomic_attack = scenario._get_attack_from_technique(
                composite=composite_technique, seed_groups=mock_memory_seed_groups
            )

            assert isinstance(atomic_attack, AtomicAttack)
            assert atomic_attack.seed_groups == mock_memory_seed_groups


@pytest.mark.usefixtures(*FIXTURES)
class TestFoundryGetAttack:
    """Tests for the _get_attack method."""

    async def test_get_attack_single_turn_with_converters(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test creating a single-turn attack with converters."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Base64],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            attack = scenario._get_attack(
                attack_type=PromptSendingAttack,
                converters=[Base64Converter()],
            )

            assert isinstance(attack, PromptSendingAttack)

    async def test_get_attack_multi_turn_with_adversarial_target(
        self,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        mock_memory_seed_groups,
        mock_dataset_config,
    ):
        """Test creating a multi-turn attack."""
        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                adversarial_chat=mock_adversarial_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Crescendo],
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()

            attack = scenario._get_attack(
                attack_type=CrescendoAttack,
                converters=[],
            )

            assert isinstance(attack, CrescendoAttack)


@pytest.mark.usefixtures(*FIXTURES)
class TestFoundryAllTechniques:
    """Tests for the complete declarative Foundry technique mapping."""

    @pytest.mark.parametrize(("technique", "expected_converter_type"), _converter_technique_cases())
    def test_converter_technique_maps_to_expected_type(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
        technique: FoundryTechnique,
        expected_converter_type: type[Converter],
    ) -> None:
        """Each converter technique creates its declared concrete converter."""
        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(attack=None, converters=[technique]),
            seed_groups=mock_memory_seed_groups,
        )

        converters = _get_request_converters(atomic_attack)
        assert len(converters) == 1
        assert type(converters[0]) is expected_converter_type

    @pytest.mark.parametrize(("technique", "expected_attack_type", "expected_tree_width"), _attack_technique_cases())
    def test_attack_technique_maps_to_expected_type_and_configuration(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
        mock_float_threshold_scorer: FloatScaleThresholdScorer,
        technique: FoundryTechnique | None,
        expected_attack_type: type[AttackStrategy[Any, Any]],
        expected_tree_width: int | None,
    ) -> None:
        """Each attack technique keeps its class, tree width, and supplied scorer."""
        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(attack=technique),
            seed_groups=mock_memory_seed_groups,
        )

        attack = atomic_attack.attack_technique.attack
        assert type(attack) is expected_attack_type
        if expected_tree_width is not None:
            assert attack._configuration.tree_width == expected_tree_width
            assert attack._objective_scorer is mock_float_threshold_scorer

    def test_mapping_cases_cover_all_concrete_foundry_techniques(self) -> None:
        """The mapping cases stay exhaustive as the Foundry enum evolves."""
        converter_techniques = {technique for technique, _ in _converter_technique_cases()}
        attack_techniques = {technique for technique, _, _ in _attack_technique_cases() if technique is not None}

        assert converter_techniques == set(FoundryTechnique.get_techniques_by_tag("converter"))
        assert attack_techniques == set(FoundryTechnique.get_techniques_by_tag("attack"))

    @pytest.mark.parametrize(
        ("technique", "attribute", "expected_value"),
        [
            (FoundryTechnique.Caesar, "caesar_offset", 3),
            (FoundryTechnique.SuffixAppend, "_suffix", "!!!"),
        ],
    )
    def test_converter_technique_preserves_special_arguments(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
        technique: FoundryTechnique,
        attribute: str,
        expected_value: object,
    ) -> None:
        """Converters with fixed Foundry arguments retain those exact values."""
        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(attack=None, converters=[technique]),
            seed_groups=mock_memory_seed_groups,
        )

        converter = _get_request_converters(atomic_attack)[0]
        assert getattr(converter, attribute) == expected_value

    def test_tense_converter_preserves_tense_and_adversarial_target(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
        mock_adversarial_target: PromptTarget,
    ) -> None:
        """Tense keeps the past-tense setting and exact adversarial target."""
        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(attack=None, converters=[FoundryTechnique.Tense]),
            seed_groups=mock_memory_seed_groups,
        )

        converter = _get_request_converters(atomic_attack)[0]
        assert isinstance(converter, TenseConverter)
        assert converter._tense == "past"
        assert converter._converter_target is mock_adversarial_target

    def test_jailbreak_converter_builds_fresh_random_template(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
    ) -> None:
        """Every Jailbreak materialization gets a newly selected random template."""
        templates = [MagicMock(), MagicMock()]
        with patch(
            "pyrit.scenario.scenarios.foundry.red_team_agent.TextJailBreak",
            side_effect=templates,
        ) as template_constructor:
            atomic_attacks = [
                configured_scenario._get_attack_from_technique(
                    composite=FoundryComposite(attack=None, converters=[FoundryTechnique.Jailbreak]),
                    seed_groups=mock_memory_seed_groups,
                )
                for _ in range(2)
            ]

        converters = [_get_request_converters(atomic_attack)[0] for atomic_attack in atomic_attacks]
        assert template_constructor.call_args_list == [call(random_template=True), call(random_template=True)]
        assert isinstance(converters[0], TextJailbreakConverter)
        assert isinstance(converters[1], TextJailbreakConverter)
        assert converters[0].jail_break_template is templates[0]
        assert converters[1].jail_break_template is templates[1]

    def test_composite_preserves_converter_order(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
    ) -> None:
        """Composite converters remain in caller-specified execution order."""
        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(
                attack=None,
                converters=[
                    FoundryTechnique.Url,
                    FoundryTechnique.Base64,
                    FoundryTechnique.ROT13,
                ],
            ),
            seed_groups=mock_memory_seed_groups,
        )

        converters = _get_request_converters(atomic_attack)
        assert [type(converter) for converter in converters] == [UrlConverter, Base64Converter, ROT13Converter]

    def test_unknown_converter_technique_raises_existing_error(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
    ) -> None:
        """Unsupported converter names retain the existing ValueError."""
        composite = FoundryComposite(attack=None)
        composite.converters.append(cast("FoundryTechnique", "unsupported"))

        with pytest.raises(ValueError) as exc_info:
            configured_scenario._get_attack_from_technique(
                composite=composite,
                seed_groups=mock_memory_seed_groups,
            )

        assert str(exc_info.value) == "Unknown technique: unsupported"

    def test_unknown_attack_technique_retains_baseline_fallback(
        self,
        configured_scenario: RedTeamAgent,
        mock_memory_seed_groups: list[AttackSeedGroup],
    ) -> None:
        """An unknown attack-tagged value still falls back to PromptSendingAttack."""
        unknown_attack = MagicMock()
        unknown_attack.tags = {"attack"}
        unknown_attack.value = "unsupported"

        atomic_attack = configured_scenario._get_attack_from_technique(
            composite=FoundryComposite(attack=cast("FoundryTechnique", unknown_attack)),
            seed_groups=mock_memory_seed_groups,
        )

        assert type(atomic_attack.attack_technique.attack) is PromptSendingAttack


@pytest.mark.usefixtures(*FIXTURES)
class TestFoundryProperties:
    """Tests for RedTeamAgent properties and attributes."""

    async def test_scenario_composites_set_after_initialize(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that scenario composites are set after initialize_async."""
        techniques = [FoundryTechnique.Base64, FoundryTechnique.ROT13]

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            # Before initialize_async, composites should be empty
            assert len(scenario._scenario_composites) == 0

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": techniques,
                    "dataset_config": mock_dataset_config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

            # After initialize_async, composites should be set
            assert len(scenario._scenario_composites) == len(techniques)
            assert scenario.atomic_attack_count == len(techniques)

    def test_scenario_version_is_set(self, mock_objective_target, mock_objective_scorer):
        """Test that scenario version is properly set."""
        scenario = RedTeamAgent(
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        assert scenario.VERSION == 1

    async def test_scenario_atomic_attack_count_matches_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """Test that atomic attack count is reasonable for the number of techniques."""
        techniques = [
            FoundryTechnique.Base64,
            FoundryTechnique.ROT13,
            FoundryTechnique.Leetspeak,
        ]

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )

            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": techniques,
                    "dataset_config": mock_dataset_config,
                }
            )
            await scenario.initialize_async()
            # Should have at least as many runs as specific techniques provided
            assert scenario.atomic_attack_count >= len(techniques)

    async def test_initialize_with_foundry_composite_directly(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """FoundryComposite objects passed to initialize_async are used as-is."""
        composite = FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Base64])

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [composite],
                    "dataset_config": mock_dataset_config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

        assert len(scenario._scenario_composites) == 1
        result = scenario._scenario_composites[0]
        assert result.attack == FoundryTechnique.Crescendo
        assert result.converters == [FoundryTechnique.Base64]
        assert result.name == "ComposedTechnique(crescendo, base64)"

    async def test_initialize_with_mixed_composites_and_techniques(
        self, mock_objective_target, mock_objective_scorer, mock_memory_seed_groups, mock_dataset_config
    ):
        """A mix of bare FoundryTechnique and FoundryComposite can be passed together."""
        composite = FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Base64])

        with patch.object(
            RedTeamAgent,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value={"memory": mock_memory_seed_groups},
        ):
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [composite, FoundryTechnique.ROT13],
                    "dataset_config": mock_dataset_config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

        assert len(scenario._scenario_composites) == 2
        assert scenario._scenario_composites[0].attack == FoundryTechnique.Crescendo
        assert scenario._scenario_composites[1].attack is None
        assert scenario._scenario_composites[1].converters == [FoundryTechnique.ROT13]


@pytest.mark.usefixtures(*FIXTURES)
class TestRedTeamAgentBaselineUniformity:
    """ADO 9012 regression: baseline shares objectives with techniques under max_dataset_size."""

    async def test_one_resolution_call_baseline_matches_techniques(self, mock_objective_target, mock_objective_scorer):
        from pyrit.models import AttackSeedGroup, SeedObjective

        seed_groups = [AttackSeedGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]
        config = DatasetAttackConfiguration(seed_groups=seed_groups, max_dataset_size=3)

        first_sample = [("inline", group) for group in seed_groups[:3]]
        second_sample = [("inline", group) for group in seed_groups[5:8]]
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            side_effect=[first_sample, second_sample],
        ) as mock_sample:
            scenario = RedTeamAgent(
                attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [FoundryTechnique.Base64],
                    "dataset_config": config,
                    "include_baseline": True,
                }
            )
            await scenario.initialize_async()

        assert mock_sample.call_count == 1
        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"
        baseline_objs = set(scenario._atomic_attacks[0].objectives)
        for attack in scenario._atomic_attacks[1:]:
            assert set(attack.objectives) == baseline_objs
