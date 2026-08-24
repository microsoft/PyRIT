# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Multilingual scenario."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyrit.converter import Base64Converter, QRCodeConverter, RandomTranslationConverter, TranslationConverter
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    ComponentIdentifier,
    Message,
    MessagePiece,
    ScenarioRunState,
    SeedObjective,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.scenarios.airt.multilingual import (
    _DEFAULT_NUM_LANGUAGES,
    _LANGUAGES_METADATA_KEY,
    _PROMPT_SENDING,
    _RANDOM_TRANSLATION,
    _TRANSLATION,
    Multilingual,
    _build_multilingual_technique,
)
from pyrit.score import SubStringScorer, TrueFalseScorer


def _mock_identifier(name: str) -> ComponentIdentifier:
    """Build a component identifier for a mock scenario dependency."""
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Register one compatible and one incompatible technique for catalog tests."""
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_multilingual_technique.cache_clear()

    text_factory = AttackTechniqueFactory(
        name="base64",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(converters=[Base64Converter()])
            )
        },
    )
    image_factory = AttackTechniqueFactory(
        name="qr_code",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(converters=[QRCodeConverter()])
            )
        },
    )
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories([text_factory, image_factory])
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_multilingual_technique.cache_clear()


@pytest.fixture
def mock_memory_seed_groups() -> list[AttackSeedGroup]:
    """Create an inline objective population."""
    return [AttackSeedGroup(seeds=[SeedObjective(value="test objective")])]


@pytest.fixture
def mock_objective_target() -> PromptTarget:
    """Create the target under test."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_identifier("MockObjectiveTarget")
    mock.configuration.includes.return_value = True
    return mock


@pytest.fixture
def mock_adversarial_chat() -> PromptTarget:
    """Create the target used by translation converters."""
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_identifier("MockAdversarialChat")
    mock.capabilities.includes.return_value = True
    return mock


@pytest.fixture
def mock_objective_scorer() -> TrueFalseScorer:
    """Create the objective scorer."""
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_identifier("MockObjectiveScorer")
    return mock


def _patch_seed_groups(mock_memory_seed_groups):
    return patch.object(
        Multilingual,
        "_resolve_seed_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value={"harmbench": mock_memory_seed_groups},
    )


def _request_converters(atomic_attack):
    """Return the flattened request converter chain configured on an atomic attack."""
    configurations = atomic_attack.attack_technique.attack.get_request_converters()
    return [converter for configuration in configurations for converter in configuration.converters]


def _response(text: str) -> list[Message]:
    return [
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value=text,
                    original_value_data_type="text",
                )
            ]
        )
    ]


@pytest.mark.usefixtures("patch_central_database")
class TestMultilingual:
    """Validate multilingual technique selection and converter construction."""

    def test_technique_catalog_includes_only_translation_compatible_factories(self) -> None:
        technique_class = _build_multilingual_technique()

        all_values = {technique.value for technique in technique_class.expand({technique_class.ALL})}
        default_values = {technique.value for technique in technique_class.expand({technique_class.default()})}
        assert all_values == {_PROMPT_SENDING, "base64"}
        assert default_values == {_PROMPT_SENDING}

    def test_declares_run_parameters(self) -> None:
        """Language and strategy selectors are declared while user converter stacks are rejected."""
        parameters = {parameter.name: parameter for parameter in Multilingual.additional_parameters()}
        supported_names = {parameter.name for parameter in Multilingual.supported_parameters()}
        assert set(parameters) == {"num_languages", "languages", "translation_strategies"}
        assert set(parameters).issubset(supported_names)
        assert "technique_converters" not in supported_names
        assert set(parameters["translation_strategies"].choices or []) == {_TRANSLATION, _RANDOM_TRANSLATION}

    async def test_default_draws_five_random_languages(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        selected = ["French", "Spanish"]

        with (
            _patch_seed_groups(mock_memory_seed_groups),
            patch("pyrit.scenario.scenarios.airt.multilingual.random.sample", return_value=selected) as sample,
        ):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
            assert scenario._resolved_languages == selected
            assert sample.call_args.args[1] == _DEFAULT_NUM_LANGUAGES

    async def test_num_languages_samples_that_many(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        selected = ["French", "German", "Spanish"]

        with (
            _patch_seed_groups(mock_memory_seed_groups),
            patch("pyrit.scenario.scenarios.airt.multilingual.random.sample", return_value=selected) as sample,
        ):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(args={"objective_target": mock_objective_target, "num_languages": 3})
            await scenario.initialize_async()
            assert scenario._resolved_languages == selected
            assert sample.call_args.args[1] == 3

    async def test_both_translation_strategies_build_distinct_matrix_slices(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "languages": ["Canadian French", "Spanish"],
                    "translation_strategies": [_TRANSLATION, _RANDOM_TRANSLATION],
                }
            )
            await scenario.initialize_async()

        assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == [
            "baseline",
            "prompt_sending_translation_canadian_french_harmbench",
            "prompt_sending_translation_spanish_harmbench",
            "prompt_sending_random_translation_harmbench",
        ]
        converters = [_request_converters(attack) for attack in scenario._atomic_attacks[1:4]]
        assert all(isinstance(converter_chain[-1], TranslationConverter) for converter_chain in converters[0:2])
        assert isinstance(converters[2][-1], RandomTranslationConverter)
        assert converters[2][-1].languages == ["Canadian French", "Spanish"]

    async def test_registered_technique_preserves_built_in_converter_before_translation(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        technique_class = _build_multilingual_technique()
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [technique_class.base64],
                    "languages": ["French"],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

        converters = _request_converters(scenario._atomic_attacks[0])
        assert [type(converter) for converter in converters] == [Base64Converter, TranslationConverter]

    async def test_language_normalization_preserves_unique_atomic_attack_names(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "languages": ["Canadian French", "canadian_french", "  SPANISH  ", "spanish"],
                    "translation_strategies": [_TRANSLATION],
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

        assert scenario._resolved_languages == ["Canadian French", "SPANISH"]
        assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == [
            "prompt_sending_translation_canadian_french_harmbench",
            "prompt_sending_translation_spanish_harmbench",
        ]

    async def test_mutually_exclusive_selectors_raise(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "num_languages": 2,
                    "languages": ["French"],
                }
            )
            with pytest.raises(ValueError, match="only one of"):
                await scenario.initialize_async()

    def test_invalid_translation_strategy_raises(self, mock_adversarial_chat, mock_objective_scorer):
        scenario = Multilingual(
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )
        with pytest.raises(ValueError, match="expected one of"):
            scenario.set_params_from_args(args={"translation_strategies": ["unknown"]})

    async def test_metadata_records_resolved_languages(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "languages": ["French", "Spanish"],
                }
            )
            await scenario.initialize_async()

        metadata = scenario._build_initial_scenario_metadata()
        assert metadata[_LANGUAGES_METADATA_KEY] == ["French", "Spanish"]

    def test_resolve_languages_replays_persisted_set_on_resume(self, mock_adversarial_chat, mock_objective_scorer):
        scenario = Multilingual(
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            scenario_result_id="existing-result",
        )
        stored = MagicMock()
        stored.metadata = {_LANGUAGES_METADATA_KEY: ["French", "Spanish"]}

        with patch.object(scenario._memory, "get_scenario_results", return_value=[stored]):
            assert scenario._resolve_languages() == ["French", "Spanish"]

    async def test_baseline_is_prepended_by_default_with_same_seed_population(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        with _patch_seed_groups(mock_memory_seed_groups):
            scenario = Multilingual(
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )
            scenario.set_params_from_args(args={"objective_target": mock_objective_target, "languages": ["French"]})
            await scenario.initialize_async()

        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"
        assert scenario._atomic_attacks[0].seed_groups == scenario._atomic_attacks[1].seed_groups
        assert scenario._atomic_attacks[0].seed_groups[0] is scenario._atomic_attacks[1].seed_groups[0]

    async def test_random_translation_pool_changes_technique_evaluation_hash(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer, mock_memory_seed_groups
    ):
        hashes = []
        for languages in (["French", "Spanish"], ["German", "Japanese"]):
            with _patch_seed_groups(mock_memory_seed_groups):
                scenario = Multilingual(
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                )
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "languages": languages,
                        "translation_strategies": [_RANDOM_TRANSLATION],
                        "include_baseline": False,
                    }
                )
                await scenario.initialize_async()
                hashes.append(scenario._atomic_attacks[0].technique_eval_hash)

        assert hashes[0] != hashes[1]

    async def test_converter_retry_exhaustion_retries_only_failed_language(
        self, mock_objective_target, mock_adversarial_chat, mock_memory_seed_groups
    ) -> None:
        objective_scorer = SubStringScorer(substring="RECOVERED")
        translation_schedule = [
            _response("objectif traduit en francais"),
            RuntimeError("Spanish translation failed once"),
            RuntimeError("Spanish translation failed twice"),
            RuntimeError("Spanish translation failed three times"),
            _response("objetivo traducido al espanol"),
        ]
        mock_adversarial_chat.send_prompt_async = AsyncMock(side_effect=translation_schedule)
        mock_objective_target.send_prompt_async = AsyncMock(
            side_effect=[
                _response("RECOVERED in French"),
                _response("RECOVERED in Spanish"),
            ]
        )

        scenario = Multilingual(
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=objective_scorer,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "languages": ["French", "Spanish"],
                "translation_strategies": [_TRANSLATION],
                "include_baseline": False,
                "max_concurrency": 1,
                "max_retries": 1,
                "memory_labels": {"test": "multilingual-retry"},
            }
        )

        memory = CentralMemory.get_memory_instance()
        with (
            _patch_seed_groups(mock_memory_seed_groups),
            patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
            patch.object(
                memory,
                "update_scenario_run_state",
                wraps=memory.update_scenario_run_state,
            ) as update_state,
        ):
            await scenario.initialize_async()
            result = await scenario.run_async()

        french_name = "prompt_sending_translation_french_harmbench"
        spanish_name = "prompt_sending_translation_spanish_harmbench"
        french_results = result.attack_results[french_name]
        spanish_results = result.attack_results[spanish_name]

        assert result.scenario_run_state == ScenarioRunState.COMPLETED
        assert result.number_tries == 2
        assert result.error_message is None
        assert result.error_type is None
        assert result.metadata[_LANGUAGES_METADATA_KEY] == ["French", "Spanish"]
        assert [attack_result.outcome for attack_result in french_results] == [AttackOutcome.SUCCESS]
        assert [attack_result.outcome for attack_result in spanish_results] == [
            AttackOutcome.ERROR,
            AttackOutcome.SUCCESS,
        ]
        assert spanish_results[0].error_type == "RuntimeError"
        for attack_result in [french_results[0], spanish_results[1]]:
            assert attack_result.last_response is not None
            assert attack_result.last_score is not None
            assert attack_result.last_score.get_value() is True
            assert attack_result.last_score.objective == "test objective"
            assert attack_result.last_score.message_piece_id == attack_result.last_response.id
        assert all(
            attack_result.labels == {"test": "multilingual-retry"}
            for attack_result in [*french_results, *spanish_results]
        )
        assert len({attack_result.attack_result_id for attack_result in [*french_results, *spanish_results]}) == 3
        assert len({attack_result.conversation_id for attack_result in [*french_results, *spanish_results]}) == 3

        assert mock_adversarial_chat.send_prompt_async.await_count == 5
        translation_prompts = [
            awaited.kwargs["message"].message_pieces[0].converted_value or ""
            for awaited in mock_adversarial_chat.send_prompt_async.await_args_list
        ]
        assert ["french" in prompt for prompt in translation_prompts] == [True, False, False, False, False]
        assert ["spanish" in prompt for prompt in translation_prompts] == [False, True, True, True, True]
        assert mock_objective_target.send_prompt_async.await_count == 2
        assert [
            awaited.kwargs["message"].get_value() for awaited in mock_objective_target.send_prompt_async.await_args_list
        ] == ["objectif traduit en francais", "objetivo traducido al espanol"]
        assert sleep_mock.await_args_list == [call(1.0), call(2.0)]
        persisted_results = memory.get_attack_results(scenario_result_id=scenario._scenario_result_id)
        assert len(persisted_results) == 3
        observed_states = [call.kwargs["scenario_run_state"] for call in update_state.call_args_list]
        assert observed_states == [
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.COMPLETED,
        ]
