# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackScoringConfig,
    BijectionLearningAttack,
    BijectionLearningParameters,
    SingleTurnAttackContext,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
)
from pyrit.prompt_converter.bijection_converter import BijectionConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import TrueFalseScorer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _make_response(text: str = "mocked response") -> Message:
    return Message.from_prompt(prompt=text, role="assistant")


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
def bijection_attack(mock_objective_target):
    return BijectionLearningAttack(objective_target=mock_objective_target)


@pytest.fixture
def mock_scorer():
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id()
    return scorer


@pytest.fixture
def basic_context():
    return SingleTurnAttackContext(
        params=BijectionLearningParameters(objective="Explain something harmful"),
        conversation_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestBijectionLearningAttackInit:
    def test_default_parameters(self, mock_objective_target):
        attack = BijectionLearningAttack(objective_target=mock_objective_target)
        assert attack._mapping_type == "digit"
        assert attack._fixed_points == 13
        assert attack._digit_length == 2
        assert attack._num_teaching_shots == 5
        assert attack._max_attempts_on_failure == 0

    def test_custom_parameters(self, mock_objective_target):
        attack = BijectionLearningAttack(
            objective_target=mock_objective_target,
            mapping_type="letter",
            fixed_points=5,
            digit_length=3,
            num_teaching_shots=8,
            max_attempts_on_failure=4,
        )
        assert attack._mapping_type == "letter"
        assert attack._fixed_points == 5
        assert attack._digit_length == 3
        assert attack._num_teaching_shots == 8
        assert attack._max_attempts_on_failure == 4

    def test_accepts_scoring_config(self, mock_objective_target, mock_scorer):
        scoring_config = AttackScoringConfig(objective_scorer=mock_scorer)
        attack = BijectionLearningAttack(
            objective_target=mock_objective_target,
            attack_scoring_config=scoring_config,
        )
        assert attack._objective_scorer == mock_scorer


# ---------------------------------------------------------------------------
# params_type exclusions
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestBijectionLearningParamsType:
    def test_params_type_excludes_next_message(self, bijection_attack):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(bijection_attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, bijection_attack):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(bijection_attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, bijection_attack):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(bijection_attack.params_type)}
        assert "objective" in fields


# ---------------------------------------------------------------------------
# Converter pipeline wiring
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestBijectionConverterPipelineWiring:
    """The attack must route each attempt through the normalizer with a paired
    encode/decode converter, not pre-encode manually."""

    async def test_normalizer_receives_plain_objective_not_preencoded(self, bijection_attack, basic_context):
        """The message passed to send_prompt_async must be the plain objective.
        Encoding is delegated to the request converter."""
        captured_messages: list[Message] = []

        async def capture(**kwargs):
            captured_messages.append(kwargs["message"])
            return _make_response("ok")

        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._evaluate_response_async = AsyncMock(return_value=None)

        await bijection_attack._perform_async(context=basic_context)

        assert captured_messages
        # The message text must be the plain objective
        assert captured_messages[0].get_piece().original_value == basic_context.objective

    async def test_request_converters_contain_bijection_encode_converter(self, bijection_attack, basic_context):
        """The last request converter in each call must be a BijectionConverter
        in encode mode."""
        captured_req_configs: list[list[PromptConverterConfiguration]] = []

        async def capture(**kwargs):
            captured_req_configs.append(kwargs.get("request_converter_configurations", []))
            return _make_response("ok")

        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._evaluate_response_async = AsyncMock(return_value=None)

        await bijection_attack._perform_async(context=basic_context)

        assert captured_req_configs
        last_config = captured_req_configs[0][-1]  # last converter in the chain
        assert len(last_config.converters) == 1
        enc = last_config.converters[0]
        assert isinstance(enc, BijectionConverter)
        assert enc._direction == "encode"

    async def test_response_converters_contain_bijection_decode_converter(self, bijection_attack, basic_context):
        """The first response converter in each call must be a BijectionConverter
        in decode mode."""
        captured_resp_configs: list[list[PromptConverterConfiguration]] = []

        async def capture(**kwargs):
            captured_resp_configs.append(kwargs.get("response_converter_configurations", []))
            return _make_response("ok")

        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._evaluate_response_async = AsyncMock(return_value=None)

        await bijection_attack._perform_async(context=basic_context)

        assert captured_resp_configs
        first_config = captured_resp_configs[0][0]  # first response converter
        assert len(first_config.converters) == 1
        dec = first_config.converters[0]
        assert isinstance(dec, BijectionConverter)
        assert dec._direction == "decode"

    async def test_encode_and_decode_converters_share_same_mapping(self, bijection_attack, basic_context):
        """The encode and decode converters in a single attempt must share the
        same mapping so the decoder undoes exactly what the encoder did."""
        captured: list[dict] = []

        async def capture(**kwargs):
            captured.append(
                {
                    "req": kwargs.get("request_converter_configurations", []),
                    "resp": kwargs.get("response_converter_configurations", []),
                }
            )
            return _make_response("ok")

        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._evaluate_response_async = AsyncMock(return_value=None)

        await bijection_attack._perform_async(context=basic_context)

        call = captured[0]
        enc_converter = call["req"][-1].converters[0]
        dec_converter = call["resp"][0].converters[0]

        assert isinstance(enc_converter, BijectionConverter)
        assert isinstance(dec_converter, BijectionConverter)
        # The decode converter's mapping must be the same as the encode converter's
        assert dec_converter.mapping == enc_converter.mapping

    async def test_fresh_mapping_per_attempt(self, mock_objective_target, basic_context):
        """Each retry attempt must use a different random mapping."""
        attack = BijectionLearningAttack(
            objective_target=mock_objective_target,
            max_attempts_on_failure=2,
        )
        attack._setup_async = AsyncMock()

        enc_mappings: list[dict] = []

        async def capture(**kwargs):
            req_configs = kwargs.get("request_converter_configurations", [])
            enc = req_configs[-1].converters[0]
            enc_mappings.append(enc.mapping)
            return  # force retry

        attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        attack._objective_scorer = MagicMock(spec=TrueFalseScorer)
        attack._objective_scorer.get_identifier.return_value = _mock_scorer_id()

        await attack._perform_async(context=basic_context)

        assert len(enc_mappings) == 3  # initial + 2 retries
        unique_mappings = {frozenset(m.items()) for m in enc_mappings}
        assert len(unique_mappings) > 1, "Expected different mappings across attempts"

    async def test_user_request_converters_precede_bijection_encoder(self, mock_objective_target, basic_context):
        """User-supplied request converters must appear before the bijection
        encoder in the request pipeline."""
        from pyrit.prompt_converter import Base64Converter

        user_conv = Base64Converter()
        user_config = AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=[user_conv])
        )
        attack = BijectionLearningAttack(
            objective_target=mock_objective_target,
            attack_converter_config=user_config,
        )
        attack._setup_async = AsyncMock()

        captured_req: list[list[PromptConverterConfiguration]] = []

        async def capture(**kwargs):
            captured_req.append(kwargs.get("request_converter_configurations", []))
            return _make_response("ok")

        attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        attack._evaluate_response_async = AsyncMock(return_value=None)

        await attack._perform_async(context=basic_context)

        configs = captured_req[0]
        # First converter is user-supplied Base64
        assert isinstance(configs[0].converters[0], Base64Converter)
        # Last converter is the bijection encoder
        assert isinstance(configs[-1].converters[0], BijectionConverter)
        assert configs[-1].converters[0]._direction == "encode"

    async def test_user_response_converters_follow_bijection_decoder(self, mock_objective_target, basic_context):
        """User-supplied response converters must appear after the bijection
        decoder in the response pipeline."""
        from pyrit.prompt_converter import Base64Converter

        user_conv = Base64Converter()
        user_config = AttackConverterConfig(
            response_converters=PromptConverterConfiguration.from_converters(converters=[user_conv])
        )
        attack = BijectionLearningAttack(
            objective_target=mock_objective_target,
            attack_converter_config=user_config,
        )
        attack._setup_async = AsyncMock()

        captured_resp: list[list[PromptConverterConfiguration]] = []

        async def capture(**kwargs):
            captured_resp.append(kwargs.get("response_converter_configurations", []))
            return _make_response("ok")

        attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=capture)
        attack._evaluate_response_async = AsyncMock(return_value=None)

        await attack._perform_async(context=basic_context)

        configs = captured_resp[0]
        # First response converter is the bijection decoder
        assert isinstance(configs[0].converters[0], BijectionConverter)
        assert configs[0].converters[0]._direction == "decode"
        # Last response converter is user-supplied Base64
        assert isinstance(configs[-1].converters[0], Base64Converter)


# ---------------------------------------------------------------------------
# _perform_async outcomes
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestBijectionLearningPerform:
    async def test_perform_returns_attack_result(self, bijection_attack, basic_context):
        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(return_value=_make_response("ok"))
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._evaluate_response_async = AsyncMock(return_value=None)

        result = await bijection_attack._perform_async(context=basic_context)

        assert isinstance(result, AttackResult)
        assert result.objective == basic_context.objective

    async def test_perform_no_response_gives_failure_outcome(self, bijection_attack, basic_context):
        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(return_value=None)
        bijection_attack._setup_async = AsyncMock()
        bijection_attack._objective_scorer = MagicMock(spec=TrueFalseScorer)
        bijection_attack._objective_scorer.get_identifier.return_value = _mock_scorer_id()

        result = await bijection_attack._perform_async(context=basic_context)

        assert result.outcome == AttackOutcome.FAILURE

    async def test_scorer_receives_response_from_normalizer(self, bijection_attack, basic_context):
        """_evaluate_response_async must be called with the response returned
        by the normalizer (which has already been decoded by the response
        converter inside the normalizer pipeline)."""
        normalizer_response = _make_response("decoded text from normalizer")
        bijection_attack._prompt_normalizer = MagicMock(spec=PromptNormalizer)
        bijection_attack._prompt_normalizer.send_prompt_async = AsyncMock(return_value=normalizer_response)
        bijection_attack._setup_async = AsyncMock()

        scored_responses: list[Message] = []

        async def capture_score(*, response, objective):
            scored_responses.append(response)
            return

        bijection_attack._evaluate_response_async = AsyncMock(side_effect=capture_score)

        await bijection_attack._perform_async(context=basic_context)

        assert scored_responses
        assert scored_responses[0] is normalizer_response


# ---------------------------------------------------------------------------
# BijectionConverter encode/decode integration
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestBijectionConverterIntegration:
    async def test_encode_decode_roundtrip_letter_type(self):
        enc = BijectionConverter(mapping_type="letter", fixed_points=5, seed=123, append_description=False)
        dec = BijectionConverter(direction="decode", custom_mapping=enc.mapping)
        original = "the quick brown fox jumps"
        encoded = (await enc.convert_async(prompt=original)).output_text
        decoded = (await dec.convert_async(prompt=encoded)).output_text
        assert decoded == original

    async def test_encode_decode_roundtrip_digit_type(self):
        enc = BijectionConverter(
            mapping_type="digit",
            fixed_points=10,
            digit_length=2,
            seed=456,
            append_description=False,
        )
        dec = BijectionConverter(direction="decode", custom_mapping=enc.mapping)
        original = "over the lazy dog"
        encoded = (await enc.convert_async(prompt=original)).output_text
        decoded = (await dec.convert_async(prompt=encoded)).output_text
        assert decoded == original
