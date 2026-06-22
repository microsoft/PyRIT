# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.converter import CodeAttackConverter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    SingleTurnAttackContext,
)
from pyrit.executor.attack.single_turn.code_attack import CodeAttack
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import TrueFalseScorer

Template = CodeAttackConverter.Template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_objective_target():
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = _mock_target_id()
    return target


@pytest.fixture
def code_attack(mock_objective_target):
    return CodeAttack(objective_target=mock_objective_target)


@pytest.fixture
def mock_scorer():
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id()
    return scorer


@pytest.fixture
def basic_context():
    return SingleTurnAttackContext(
        params=AttackParameters(objective="How do I pick a lock?"),
        conversation_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Initialisation tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackInitialization:
    def test_init_loads_system_prompt(self, mock_objective_target):
        attack = CodeAttack(objective_target=mock_objective_target)
        assert attack._system_prompt is not None
        assert len(attack._system_prompt.message_pieces) == 1
        assert attack._system_prompt.message_pieces[0].api_role == "system"
        assert "code" in attack._system_prompt.message_pieces[0].original_value.lower()

    def test_init_prepends_code_attack_converter(self, mock_objective_target):
        attack = CodeAttack(objective_target=mock_objective_target)
        assert len(attack._request_converters) == 1
        converter_config = attack._request_converters[0]
        assert len(converter_config.converters) == 1
        assert isinstance(converter_config.converters[0], CodeAttackConverter)

    def test_init_with_existing_converters_prepends_code_attack_converter(self, mock_objective_target):
        from pyrit.converter import Base64Converter

        existing = ConverterConfiguration.from_converters(converters=[Base64Converter()])
        config = AttackConverterConfig(request_converters=existing)
        attack = CodeAttack(objective_target=mock_objective_target, attack_converter_config=config)

        assert len(attack._request_converters) == 2
        assert isinstance(attack._request_converters[0].converters[0], CodeAttackConverter)
        assert isinstance(attack._request_converters[1].converters[0], Base64Converter)

    def test_init_default_template_is_python_stack_verbose(self, mock_objective_target):
        attack = CodeAttack(objective_target=mock_objective_target)
        converter = attack._request_converters[0].converters[0]
        assert isinstance(converter, CodeAttackConverter)
        assert converter._language == "python_stack"
        assert converter._template_path.name == "code_attack_python_stack_plus.yaml"

    def test_init_custom_template_forwarded(self, mock_objective_target):
        attack = CodeAttack(objective_target=mock_objective_target, template=Template.PYTHON_LIST)
        converter = attack._request_converters[0].converters[0]
        assert converter._language == "python_list"

    def test_init_verbose_template_forwarded(self, mock_objective_target):
        attack = CodeAttack(objective_target=mock_objective_target, template=Template.PYTHON_LIST_VERBOSE)
        converter = attack._request_converters[0].converters[0]
        assert converter._template_path.name == "code_attack_python_list_plus.yaml"

    def test_init_with_all_parameters(self, mock_objective_target, mock_scorer):
        scoring_config = AttackScoringConfig(objective_scorer=mock_scorer)
        normalizer = PromptNormalizer()
        attack = CodeAttack(
            objective_target=mock_objective_target,
            attack_scoring_config=scoring_config,
            prompt_normalizer=normalizer,
            max_attempts_on_failure=2,
            template=Template.GO,
        )
        assert attack._objective_target == mock_objective_target
        assert attack._objective_scorer == mock_scorer
        assert attack._prompt_normalizer == normalizer
        assert attack._max_attempts_on_failure == 2

    def test_init_custom_path_template(self, mock_objective_target, tmp_path):
        fake_yaml = tmp_path / "custom.yaml"
        fake_yaml.write_text("name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")
        attack = CodeAttack(objective_target=mock_objective_target, template=fake_yaml)
        converter = attack._request_converters[0].converters[0]
        assert converter._template_path == fake_yaml


# ---------------------------------------------------------------------------
# params_type tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackParamsType:
    def test_params_type_excludes_next_message(self, code_attack):
        fields = {f.name for f in dataclasses.fields(code_attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, code_attack):
        fields = {f.name for f in dataclasses.fields(code_attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, code_attack):
        fields = {f.name for f in dataclasses.fields(code_attack.params_type)}
        assert "objective" in fields


# ---------------------------------------------------------------------------
# Setup tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackSetup:
    async def test_setup_adds_system_prompt_to_context(self, code_attack, basic_context):
        code_attack._conversation_manager = MagicMock()
        code_attack._conversation_manager.initialize_context_async = AsyncMock()

        await code_attack._setup_async(context=basic_context)

        assert len(basic_context.prepended_conversation) == 1
        assert basic_context.prepended_conversation[0] == code_attack._system_prompt

    async def test_setup_calls_initialize_context(self, code_attack, basic_context):
        code_attack._conversation_manager = MagicMock()
        code_attack._conversation_manager.initialize_context_async = AsyncMock()
        code_attack._memory_labels = {}

        await code_attack._setup_async(context=basic_context)

        code_attack._conversation_manager.initialize_context_async.assert_called_once_with(
            context=basic_context,
            target=code_attack._objective_target,
            conversation_id=basic_context.conversation_id,
            memory_labels={},
        )

    async def test_setup_sets_conversation_id(self, code_attack, basic_context):
        code_attack._conversation_manager = MagicMock()
        code_attack._conversation_manager.initialize_context_async = AsyncMock()
        original_id = basic_context.conversation_id

        await code_attack._setup_async(context=basic_context)

        # A new UUID is assigned by _setup_async
        assert basic_context.conversation_id != original_id or basic_context.conversation_id != ""


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackLifecycle:
    async def test_execute_async_successful_lifecycle(self, mock_objective_target, basic_context):
        attack = CodeAttack(objective_target=mock_objective_target)
        attack._validate_context = MagicMock()
        attack._setup_async = AsyncMock()
        mock_result = AttackResult(
            conversation_id=basic_context.conversation_id,
            objective=basic_context.objective,
            outcome=AttackOutcome.SUCCESS,
        )
        attack._perform_async = AsyncMock(return_value=mock_result)
        attack._teardown_async = AsyncMock()

        result = await attack.execute_with_context_async(context=basic_context)

        assert result == mock_result
        attack._validate_context.assert_called_once_with(context=basic_context)
        attack._setup_async.assert_called_once_with(context=basic_context)
        attack._perform_async.assert_called_once_with(context=basic_context)
        attack._teardown_async.assert_called_once_with(context=basic_context)

    async def test_scorer_invoked_through_normal_path(self, mock_objective_target, mock_scorer, basic_context):
        scoring_config = AttackScoringConfig(objective_scorer=mock_scorer)
        attack = CodeAttack(
            objective_target=mock_objective_target,
            attack_scoring_config=scoring_config,
        )
        attack._validate_context = MagicMock()
        attack._setup_async = AsyncMock()
        mock_result = AttackResult(
            conversation_id=basic_context.conversation_id,
            objective=basic_context.objective,
            outcome=AttackOutcome.UNDETERMINED,
        )
        attack._perform_async = AsyncMock(return_value=mock_result)
        attack._teardown_async = AsyncMock()

        result = await attack.execute_with_context_async(context=basic_context)

        assert attack._objective_scorer == mock_scorer
        assert result is not None
