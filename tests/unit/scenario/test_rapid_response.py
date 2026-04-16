# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the RapidResponse scenario (refactored from ContentHarms)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.core.core_techniques import (
    many_shot_factory,
    prompt_sending_factory,
    role_play_factory,
    tap_factory,
)
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.scenarios.airt.rapid_response import (
    RapidResponse,
    RapidResponseStrategy,
)
from pyrit.score import TrueFalseScorer


# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_adversarial_target():
    mock = MagicMock(spec=PromptChatTarget)
    mock.get_identifier.return_value = _mock_id("MockAdversarialTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full bundled dataset."""
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
        },
    ):
        yield


def _make_seed_groups(name: str) -> list[SeedAttackGroup]:
    """Create two seed attack groups for a given category."""
    return [
        SeedAttackGroup(
            seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]
        ),
        SeedAttackGroup(
            seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]
        ),
    ]


ALL_HARM_CATEGORIES = ["hate", "fairness", "violence", "sexual", "harassment", "misinformation", "leakage"]

ALL_HARM_SEED_GROUPS = {cat: _make_seed_groups(cat) for cat in ALL_HARM_CATEGORIES}


FIXTURES = ["patch_central_database", "mock_runtime_env"]


# ===========================================================================
# Strategy enum tests
# ===========================================================================


class TestRapidResponseStrategy:
    """Tests for the RapidResponseStrategy enum."""

    def test_technique_members_exist(self):
        """All four technique members are accessible."""
        assert RapidResponseStrategy.PromptSending.value == "prompt_sending"
        assert RapidResponseStrategy.RolePlay.value == "role_play"
        assert RapidResponseStrategy.ManyShot.value == "many_shot"
        assert RapidResponseStrategy.TAP.value == "tap"

    def test_aggregate_members_exist(self):
        """All four aggregate members are accessible."""
        assert RapidResponseStrategy.ALL.value == "all"
        assert RapidResponseStrategy.DEFAULT.value == "default"
        assert RapidResponseStrategy.SINGLE_TURN.value == "single_turn"
        assert RapidResponseStrategy.MULTI_TURN.value == "multi_turn"

    def test_total_member_count(self):
        """4 aggregates + 4 techniques = 8 members."""
        assert len(list(RapidResponseStrategy)) == 8

    def test_non_aggregate_count(self):
        """get_all_strategies returns only the 4 technique members."""
        non_aggregate = RapidResponseStrategy.get_all_strategies()
        assert len(non_aggregate) == 4

    def test_aggregate_tags(self):
        tags = RapidResponseStrategy.get_aggregate_tags()
        assert tags == {"all", "default", "single_turn", "multi_turn"}

    def test_default_expands_to_prompt_sending_and_many_shot(self):
        """DEFAULT aggregate should expand to PromptSending + ManyShot."""
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.DEFAULT})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "many_shot"}

    def test_single_turn_expands_to_prompt_sending_and_role_play(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.SINGLE_TURN})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "role_play"}

    def test_multi_turn_expands_to_many_shot_and_tap(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.MULTI_TURN})
        values = {s.value for s in expanded}
        assert values == {"many_shot", "tap"}

    def test_all_expands_to_all_techniques(self):
        expanded = RapidResponseStrategy.normalize_strategies({RapidResponseStrategy.ALL})
        values = {s.value for s in expanded}
        assert values == {"prompt_sending", "role_play", "many_shot", "tap"}

    def test_strategy_values_are_unique(self):
        values = [s.value for s in RapidResponseStrategy]
        assert len(values) == len(set(values))

    def test_invalid_strategy_value_raises(self):
        with pytest.raises(ValueError):
            RapidResponseStrategy("nonexistent")

    def test_invalid_strategy_name_raises(self):
        with pytest.raises(KeyError):
            RapidResponseStrategy["Nonexistent"]


# ===========================================================================
# Initialization / class-level tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseBasic:
    """Tests for RapidResponse initialization and class properties."""

    def test_version_is_2(self):
        assert RapidResponse.VERSION == 2

    def test_get_strategy_class(self):
        assert RapidResponse.get_strategy_class() is RapidResponseStrategy

    def test_get_default_strategy_returns_default(self):
        assert RapidResponse.get_default_strategy() == RapidResponseStrategy.DEFAULT

    def test_default_dataset_config_has_all_harm_datasets(self):
        config = RapidResponse.default_dataset_config()
        assert isinstance(config, DatasetConfiguration)
        names = config.get_default_dataset_names()
        expected = [f"airt_{cat}" for cat in ALL_HARM_CATEGORIES]
        for name in expected:
            assert name in names
        assert len(names) == 7

    def test_default_dataset_config_max_dataset_size(self):
        config = RapidResponse.default_dataset_config()
        assert config.max_dataset_size == 4

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_initialization_minimal(self, mock_get_scorer, mock_adversarial_target, mock_objective_scorer):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
        assert scenario._adversarial_chat == mock_adversarial_target
        assert scenario.name == "RapidResponse"

    def test_initialization_with_custom_scorer(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario._objective_scorer == mock_objective_scorer

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_default_adversarial_target_created(self, mock_get_scorer, mock_objective_scorer):
        """With env vars patched, constructor creates an OpenAIChatTarget."""
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse()
        assert scenario._adversarial_chat is not None

    @pytest.mark.asyncio
    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_initialization_defaults_to_default_strategy(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
        await scenario.initialize_async(objective_target=mock_objective_target)
        # DEFAULT expands to PromptSending + ManyShot → 2 composites
        assert len(scenario._scenario_composites) == 2

    @pytest.mark.asyncio
    async def test_initialize_raises_when_no_datasets(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Dataset resolution fails from empty memory."""
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="DatasetConfiguration has no seed_groups"):
            await scenario.initialize_async(objective_target=mock_objective_target)

    @pytest.mark.asyncio
    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    @patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=ALL_HARM_SEED_GROUPS)
    async def test_memory_labels_stored(
        self,
        _mock_groups,
        mock_get_scorer,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
    ):
        mock_get_scorer.return_value = mock_objective_scorer
        labels = {"test_run": "123"}
        scenario = RapidResponse(adversarial_chat=mock_adversarial_target)
        await scenario.initialize_async(objective_target=mock_objective_target, memory_labels=labels)
        assert scenario._memory_labels == labels

    @pytest.mark.parametrize("harm_category", ALL_HARM_CATEGORIES)
    def test_harm_category_prompt_file_exists(self, harm_category):
        harm_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
        assert (harm_path / f"{harm_category}.prompt").exists()


# ===========================================================================
# Attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseAttackGeneration:
    """Tests for _get_atomic_attacks_async with various strategies."""

    async def _init_and_get_attacks(
        self,
        *,
        mock_objective_target,
        mock_adversarial_target,
        mock_objective_scorer,
        strategies: list[RapidResponseStrategy] | None = None,
        seed_groups: dict[str, list[SeedAttackGroup]] | None = None,
    ):
        """Helper: initialize scenario and return atomic attacks."""
        groups = seed_groups or {"hate": _make_seed_groups("hate")}
        with patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups):
            scenario = RapidResponse(
                adversarial_chat=mock_adversarial_target,
                objective_scorer=mock_objective_scorer,
            )
            init_kwargs = {"objective_target": mock_objective_target}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            return await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_default_strategy_produces_prompt_sending_and_many_shot(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, ManyShotJailbreakAttack}

    @pytest.mark.asyncio
    async def test_single_turn_strategy_produces_prompt_sending_and_role_play(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.SINGLE_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {PromptSendingAttack, RolePlayAttack}

    @pytest.mark.asyncio
    async def test_multi_turn_strategy_produces_many_shot_and_tap(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.MULTI_TURN],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {ManyShotJailbreakAttack, TreeOfAttacksWithPruningAttack}

    @pytest.mark.asyncio
    async def test_all_strategy_produces_all_four_techniques(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.ALL],
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {
            PromptSendingAttack,
            RolePlayAttack,
            ManyShotJailbreakAttack,
            TreeOfAttacksWithPruningAttack,
        }

    @pytest.mark.asyncio
    async def test_single_technique_selection(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.PromptSending],
        )
        assert len(attacks) > 0
        for a in attacks:
            assert isinstance(a.attack_technique.attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attack_count_is_techniques_times_datasets(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """With 2 datasets and DEFAULT (2 techniques), expect 4 atomic attacks."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        # DEFAULT = PromptSending + ManyShot = 2 techniques, 2 datasets → 4
        assert len(attacks) == 4

    @pytest.mark.asyncio
    async def test_atomic_attack_names_group_by_harm_category(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """_build_atomic_attack_name groups by dataset (harm category), not technique."""
        two_datasets = {
            "hate": _make_seed_groups("hate"),
            "violence": _make_seed_groups("violence"),
        }
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=two_datasets,
        )
        names = {a.atomic_attack_name for a in attacks}
        assert names == {"hate", "violence"}

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_unknown_technique_skipped_with_warning(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """If a technique name has no factory, it's skipped (not an error)."""
        groups = {"hate": _make_seed_groups("hate")}
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups),
            patch.object(
                RapidResponse,
                "get_attack_technique_factories",
                return_value={"prompt_sending": prompt_sending_factory()},
            ),
        ):
            scenario = RapidResponse(
                adversarial_chat=mock_adversarial_target,
                objective_scorer=mock_objective_scorer,
            )
            # Select ALL which includes role_play, many_shot, tap — none have factories
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                scenario_strategies=[RapidResponseStrategy.ALL],
            )
            attacks = await scenario._get_atomic_attacks_async()
            # Only prompt_sending should have produced attacks
            assert len(attacks) == 1
            assert isinstance(attacks[0].attack_technique.attack, PromptSendingAttack)

    @pytest.mark.asyncio
    async def test_attacks_include_seed_groups(
        self, mock_objective_target, mock_adversarial_target, mock_objective_scorer
    ):
        """Each atomic attack carries the correct seed groups."""
        attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            mock_adversarial_target=mock_adversarial_target,
            mock_objective_scorer=mock_objective_scorer,
            strategies=[RapidResponseStrategy.PromptSending],
        )
        for a in attacks:
            assert len(a.objectives) > 0


# ===========================================================================
# _build_atomic_attack_name tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildAtomicAttackName:
    def test_rapid_response_groups_by_seed_group_name(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        result = scenario._build_atomic_attack_name(technique_name="prompt_sending", seed_group_name="hate")
        assert result == "hate"

    def test_rapid_response_ignores_technique_name(self, mock_adversarial_target, mock_objective_scorer):
        scenario = RapidResponse(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        r1 = scenario._build_atomic_attack_name(technique_name="prompt_sending", seed_group_name="hate")
        r2 = scenario._build_atomic_attack_name(technique_name="tap", seed_group_name="hate")
        assert r1 == r2 == "hate"


# ===========================================================================
# Core techniques factory tests
# ===========================================================================


class TestCoreTechniques:
    """Tests for shared AttackTechniqueFactory builders in core_techniques.py."""

    def test_prompt_sending_factory_attack_class(self):
        f = prompt_sending_factory()
        assert f.attack_class is PromptSendingAttack

    def test_role_play_factory_attack_class(self):
        f = role_play_factory()
        assert f.attack_class is RolePlayAttack

    def test_many_shot_factory_attack_class(self):
        f = many_shot_factory()
        assert f.attack_class is ManyShotJailbreakAttack

    def test_tap_factory_attack_class(self):
        f = tap_factory()
        assert f.attack_class is TreeOfAttacksWithPruningAttack

    def test_base_class_returns_all_four_factories(self):
        factories = RapidResponse.get_attack_technique_factories()
        assert set(factories.keys()) == {"prompt_sending", "role_play", "many_shot", "tap"}
        assert factories["prompt_sending"].attack_class is PromptSendingAttack
        assert factories["role_play"].attack_class is RolePlayAttack
        assert factories["many_shot"].attack_class is ManyShotJailbreakAttack
        assert factories["tap"].attack_class is TreeOfAttacksWithPruningAttack


# ===========================================================================
# Deprecated alias tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestDeprecatedAliases:
    """Tests for backward-compatible ContentHarms aliases."""

    def test_content_harms_is_rapid_response(self):
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        assert ContentHarms is RapidResponse

    def test_content_harms_strategy_is_rapid_response_strategy(self):
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarmsStrategy

        assert ContentHarmsStrategy is RapidResponseStrategy

    def test_content_harms_instance_name_is_rapid_response(self, mock_adversarial_target, mock_objective_scorer):
        """ContentHarms() creates a RapidResponse with name 'RapidResponse'."""
        from pyrit.scenario.scenarios.airt.content_harms import ContentHarms

        scenario = ContentHarms(
            adversarial_chat=mock_adversarial_target,
            objective_scorer=mock_objective_scorer,
        )
        assert scenario.name == "RapidResponse"
        assert isinstance(scenario, RapidResponse)
