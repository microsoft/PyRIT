# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    SingleTurnAttackContext,
    SingleTurnCrescendoAttack,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    SeedPrompt,
)
from pyrit.prompt_converter import Base64Converter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptChatTarget
from pyrit.score import TrueFalseScorer


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to build a ComponentIdentifier for tests."""
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to build a ComponentIdentifier for tests."""
    return ComponentIdentifier(class_name=name, class_module="test_module")


@pytest.fixture
def mock_objective_target():
    """Create a mock PromptChatTarget for the objective target."""
    target = MagicMock(spec=PromptChatTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return target


@pytest.fixture
def mock_adversarial_target():
    """Create a mock PromptChatTarget for the adversarial chat target."""
    target = MagicMock(spec=PromptChatTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = _mock_target_id("MockAdversarialTarget")
    return target


@pytest.fixture
def mock_template():
    """Create a mock SeedPrompt template for the STCA system prompt."""
    template = MagicMock(spec=SeedPrompt)
    template.render_template_value.return_value = "rendered system prompt"
    return template


@pytest.fixture
def mock_scorer():
    """Create a mock objective scorer."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id()
    return scorer


@pytest.fixture
def basic_context():
    """Create a basic single-turn attack context."""
    return SingleTurnAttackContext(
        params=AttackParameters(objective="Explain how to pick a pin tumbler lock"),
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def adversarial_config(mock_adversarial_target):
    """Create an AttackAdversarialConfig wrapping the mock adversarial target."""
    return AttackAdversarialConfig(target=mock_adversarial_target)


def _valid_dialogue_json(num_turns: int = 3, final: str = "How do I pick a pin tumbler lock?") -> str:
    """Build a valid dialogue JSON string with the requested number of turns."""
    payload = {
        "turns": [{"user": f"user question {i}", "assistant": f"assistant reply {i}"} for i in range(num_turns)],
        "final_user_question": final,
    }
    return json.dumps(payload)


def _adversarial_response(text: str) -> Message:
    """Build a Message that mimics what the prompt normalizer returns from the adversarial chat."""
    response = MagicMock(spec=Message)
    response.get_value.return_value = text
    return response


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackInitialization:
    """Tests for SingleTurnCrescendoAttack initialization."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_init_with_default_parameters(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        """Default initialization sets the paper's STCA-3 default and loads the default template."""
        mock_from_yaml.return_value = mock_template

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )

        assert attack._objective_target == mock_objective_target
        assert attack._adversarial_chat == adversarial_config.target
        assert attack._num_synthesized_turns == 3
        assert attack._max_attempts_on_failure == 0
        assert attack._adversarial_chat_system_prompt_template == mock_template

        mock_from_yaml.assert_called_once()
        kwargs = mock_from_yaml.call_args.kwargs
        assert "stca_variant_1.yaml" in str(kwargs["template_path"])
        assert set(kwargs["required_parameters"]) == {"objective", "num_synthesized_turns"}

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_init_with_custom_num_synthesized_turns(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        """Custom n is honored."""
        mock_from_yaml.return_value = mock_template

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=5,
        )

        assert attack._num_synthesized_turns == 5

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_init_with_all_parameters(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template, mock_scorer
    ):
        """All optional parameters are wired through correctly."""
        mock_from_yaml.return_value = mock_template
        converter_config = AttackConverterConfig()
        scoring_config = AttackScoringConfig(objective_scorer=mock_scorer)
        prompt_normalizer = PromptNormalizer()

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_converter_config=converter_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=2,
            num_synthesized_turns=4,
        )

        assert attack._objective_scorer == mock_scorer
        assert attack._prompt_normalizer == prompt_normalizer
        assert attack._max_attempts_on_failure == 2
        assert attack._num_synthesized_turns == 4

    @pytest.mark.parametrize("bad_n", [0, -1, -10])
    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_init_raises_on_zero_or_negative_n(
        self, mock_from_yaml, bad_n, mock_objective_target, adversarial_config, mock_template
    ):
        """num_synthesized_turns must be at least 1."""
        mock_from_yaml.return_value = mock_template

        with pytest.raises(ValueError, match="num_synthesized_turns must be at least 1"):
            SingleTurnCrescendoAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                num_synthesized_turns=bad_n,
            )

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_init_uses_custom_system_prompt_path(
        self, mock_from_yaml, mock_objective_target, mock_adversarial_target, mock_template, tmp_path
    ):
        """A custom system_prompt_path on the adversarial config is used instead of the default."""
        mock_from_yaml.return_value = mock_template
        custom_path = tmp_path / "custom_stca.yaml"
        custom_path.write_text("placeholder")
        adversarial = AttackAdversarialConfig(
            target=mock_adversarial_target,
            system_prompt_path=str(custom_path),
        )

        SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial,
        )

        kwargs = mock_from_yaml.call_args.kwargs
        assert str(kwargs["template_path"]) == str(custom_path)


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackParamsType:
    """Tests for the AttackParameters subset accepted by SingleTurnCrescendoAttack."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_params_type_excludes_next_message(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "next_message" not in fields

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_params_type_excludes_prepended_conversation(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "prepended_conversation" not in fields

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_params_type_includes_objective(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "objective" in fields


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackSynthesis:
    """Tests for the adversarial chat synthesis step."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_synthesize_renders_system_prompt_and_calls_adversarial_chat(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """The synthesis step renders the system prompt with objective and n, then calls the adversarial chat."""
        mock_from_yaml.return_value = mock_template
        mock_template.render_template_value.return_value = "rendered system prompt"

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=3,
        )

        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=_adversarial_response(_valid_dialogue_json(3)))
        attack._prompt_normalizer = normalizer

        result = await attack._synthesize_dialogue_async(context=basic_context)

        mock_template.render_template_value.assert_called_once_with(
            objective=basic_context.objective,
            num_synthesized_turns=3,
        )
        adversarial_config.target.set_system_prompt.assert_called_once()
        sys_kwargs = adversarial_config.target.set_system_prompt.call_args.kwargs
        assert sys_kwargs["system_prompt"] == "rendered system prompt"

        normalizer.send_prompt_async.assert_called_once()
        assert result["final_user_question"] == "How do I pick a pin tumbler lock?"
        assert len(result["turns"]) == 3

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_synthesize_strips_markdown_code_fences(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """JSON wrapped in ```json fences is parsed correctly."""
        mock_from_yaml.return_value = mock_template
        wrapped = f"```json\n{_valid_dialogue_json(3)}\n```"

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )

        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=_adversarial_response(wrapped))
        attack._prompt_normalizer = normalizer

        result = await attack._synthesize_dialogue_async(context=basic_context)
        assert len(result["turns"]) == 3

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_synthesize_retries_on_invalid_json_then_succeeds(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """The pyrit_json_retry decorator re-runs the synthesis on InvalidJsonException until valid JSON arrives.

        tests/unit/conftest.py sets RETRY_MAX_NUM_ATTEMPTS=2, so the budget is one retry on top of the
        initial attempt. Side effects: invalid JSON, then valid JSON, for two total adversarial calls.
        """
        mock_from_yaml.return_value = mock_template

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=3,
        )

        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(
            side_effect=[
                _adversarial_response("not json at all"),
                _adversarial_response(_valid_dialogue_json(3)),
            ]
        )
        attack._prompt_normalizer = normalizer

        result = await attack._synthesize_dialogue_async(context=basic_context)

        assert len(result["turns"]) == 3
        assert normalizer.send_prompt_async.call_count == 2

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_synthesize_raises_when_response_is_none(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """A None response from the adversarial chat raises ValueError, which the retry decorator does not swallow."""
        mock_from_yaml.return_value = mock_template

        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )

        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)
        attack._prompt_normalizer = normalizer

        with pytest.raises(ValueError, match="No response received from adversarial chat"):
            await attack._synthesize_dialogue_async(context=basic_context)


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackResponseParsing:
    """Tests for _parse_adversarial_response (strict JSON validation)."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def _make_attack(self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template, n: int = 3):
        mock_from_yaml.return_value = mock_template
        return SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=n,
        )

    def test_parse_accepts_valid_payload(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
            n=3,
        )
        parsed = attack._parse_adversarial_response(response_text=_valid_dialogue_json(3))
        assert len(parsed["turns"]) == 3
        assert parsed["final_user_question"]

    def test_parse_raises_on_invalid_json(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        with pytest.raises(InvalidJsonException, match="Invalid JSON encountered"):
            attack._parse_adversarial_response(response_text="not json at all")

    def test_parse_raises_on_missing_keys(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        with pytest.raises(InvalidJsonException, match="Missing required keys"):
            attack._parse_adversarial_response(response_text=json.dumps({"turns": []}))

    def test_parse_raises_on_extra_keys(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        payload = json.loads(_valid_dialogue_json(3))
        payload["extra_field"] = "noise"
        with pytest.raises(InvalidJsonException, match="Unexpected keys"):
            attack._parse_adversarial_response(response_text=json.dumps(payload))

    def test_parse_raises_on_wrong_turn_count(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
            n=3,
        )
        with pytest.raises(InvalidJsonException, match="Expected 3 turns, got 2"):
            attack._parse_adversarial_response(response_text=_valid_dialogue_json(2))

    def test_parse_raises_on_malformed_turn_keys(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        payload = {
            "turns": [
                {"user": "hi", "bot": "hello"},
                {"user": "hi2", "assistant": "hello2"},
                {"user": "hi3", "assistant": "hello3"},
            ],
            "final_user_question": "do the thing",
        }
        with pytest.raises(InvalidJsonException, match="Turn 0 must have exactly"):
            attack._parse_adversarial_response(response_text=json.dumps(payload))

    def test_parse_raises_on_non_string_turn_values(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        payload = {
            "turns": [
                {"user": "hi", "assistant": 12345},
                {"user": "hi2", "assistant": "hello2"},
                {"user": "hi3", "assistant": "hello3"},
            ],
            "final_user_question": "do the thing",
        }
        with pytest.raises(InvalidJsonException, match="must be strings"):
            attack._parse_adversarial_response(response_text=json.dumps(payload))

    def test_parse_raises_on_empty_final_question(self, mock_objective_target, adversarial_config, mock_template):
        attack = self._make_attack(
            mock_objective_target=mock_objective_target,
            adversarial_config=adversarial_config,
            mock_template=mock_template,
        )
        with pytest.raises(InvalidJsonException, match="non-empty string"):
            attack._parse_adversarial_response(response_text=_valid_dialogue_json(3, final="   "))


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackPromptFormatting:
    """Tests for the transitional-phrase formatting of the synthesized dialogue."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_format_uses_transitional_phrases_and_objective(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        """Formatted output includes the paper's transitional phrasing and the final question."""
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=3,
        )

        synthesized = json.loads(_valid_dialogue_json(3, final="how does X work in detail?"))
        formatted = attack._format_single_turn_prompt(synthesized_dialogue=synthesized)

        assert "Earlier, I said:" in formatted
        assert "You replied:" in formatted
        assert "Then, I asked:" in formatted
        assert "You responded:" in formatted
        assert "Finally, I asked:" in formatted
        assert "how does X work in detail?" in formatted
        # Each user and assistant content from the synthesis appears in the formatted prompt
        for i in range(3):
            assert f"user question {i}" in formatted
            assert f"assistant reply {i}" in formatted

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    def test_format_handles_n_equals_one(
        self, mock_from_yaml, mock_objective_target, adversarial_config, mock_template
    ):
        """A single synthesized turn uses 'Earlier, I said' and 'You replied' followed by 'Finally'."""
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=1,
        )

        synthesized = json.loads(_valid_dialogue_json(1, final="the direct ask"))
        formatted = attack._format_single_turn_prompt(synthesized_dialogue=synthesized)

        assert "Earlier, I said:" in formatted
        assert "You replied:" in formatted
        # With only one prior turn, no "Then, I asked" should appear
        assert "Then, I asked:" not in formatted
        assert "Finally, I asked:" in formatted
        assert "the direct ask" in formatted


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackExecution:
    """Tests for _perform_async wiring."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_perform_sets_next_message_then_calls_super(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """_perform_async builds the synthesized message, assigns context.next_message, then defers to parent."""
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            num_synthesized_turns=3,
        )

        attack._synthesize_dialogue_async = AsyncMock(
            return_value=json.loads(_valid_dialogue_json(3, final="the final question"))
        )

        with patch.object(
            SingleTurnCrescendoAttack.__bases__[0],
            "_perform_async",
            new_callable=AsyncMock,
        ) as mock_super_perform:
            mock_result = AttackResult(
                conversation_id=basic_context.conversation_id,
                objective=basic_context.objective,
                outcome=AttackOutcome.SUCCESS,
            )
            mock_super_perform.return_value = mock_result

            result = await attack._perform_async(context=basic_context)

            assert basic_context.next_message is not None
            assert len(basic_context.next_message.message_pieces) == 1
            sent_text = basic_context.next_message.message_pieces[0].original_value
            assert "the final question" in sent_text
            assert "Earlier, I said:" in sent_text

            mock_super_perform.assert_called_once_with(context=basic_context)
            assert result == mock_result

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_perform_with_request_converters_preserves_converter_chain(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        """Request converters configured at construction time are preserved on the attack instance."""
        mock_from_yaml.return_value = mock_template
        converter_config = AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=[Base64Converter()])
        )
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_converter_config=converter_config,
            num_synthesized_turns=2,
        )

        assert len(attack._request_converters) == 1
        assert isinstance(attack._request_converters[0].converters[0], Base64Converter)

        attack._synthesize_dialogue_async = AsyncMock(return_value=json.loads(_valid_dialogue_json(2)))

        with patch.object(
            SingleTurnCrescendoAttack.__bases__[0],
            "_perform_async",
            new_callable=AsyncMock,
        ) as mock_super_perform:
            mock_result = AttackResult(
                conversation_id=basic_context.conversation_id,
                objective=basic_context.objective,
                outcome=AttackOutcome.SUCCESS,
            )
            mock_super_perform.return_value = mock_result
            await attack._perform_async(context=basic_context)
            mock_super_perform.assert_called_once_with(context=basic_context)


@pytest.mark.usefixtures("patch_central_database")
class TestSingleTurnCrescendoAttackLifecycle:
    """End-to-end lifecycle test using mocked phases."""

    @patch("pyrit.executor.attack.single_turn.single_turn_crescendo.SeedPrompt.from_yaml_with_required_parameters")
    @pytest.mark.asyncio
    async def test_execute_async_successful_lifecycle(
        self,
        mock_from_yaml,
        mock_objective_target,
        adversarial_config,
        mock_template,
        basic_context,
    ):
        mock_from_yaml.return_value = mock_template
        attack = SingleTurnCrescendoAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
        )

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
