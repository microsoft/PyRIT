# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.score import (
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    TrueFalseQuestionPaths,
    render_true_false_system_prompt,
)


@pytest.fixture
def scorer_true_false_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True", "description": "This is true", "rationale": "rationale for true"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


def _grounded_scorer(chat_target: MagicMock) -> SelfAskTrueFalseScorer:
    """Build a scorer from the bundled GROUNDED question using the composition factory."""
    return SelfAskTrueFalseScorer.from_question(
        chat_target=chat_target,
        question=TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value),
    )


async def test_true_false_scorer_score(patch_central_database, scorer_true_false_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = _grounded_scorer(chat_target)

    score = await scorer.score_text_async("true false")

    assert len(score) == 1
    assert score[0].get_value() is True
    assert score[0].score_value_description == "This is true"
    assert score[0].score_rationale == "rationale for true"
    assert score[0].scorer_class_identifier.class_name == "SelfAskTrueFalseScorer"


@pytest.mark.parametrize("bool_value, expected", [(True, True), (False, False)])
async def test_true_false_scorer_parses_json_boolean(patch_central_database, bool_value: bool, expected: bool):
    # The true/false schema declares score_value as a JSON boolean; ensure the scorer
    # parses a real boolean (not the string "True"/"False") into the correct score.
    json_response = '{"score_value": ' + ("true" if bool_value else "false") + ', "description": "d", "rationale": "r"}'
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])
    scorer = _grounded_scorer(chat_target)

    score = await scorer.score_text_async("true false")

    assert len(score) == 1
    assert score[0].get_value() is expected
    assert score[0].score_value in ("true", "false")


async def test_true_false_scorer_set_system_prompt(patch_central_database, scorer_true_false_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = _grounded_scorer(chat_target)

    await scorer.score_text_async("true false")

    chat_target.set_system_prompt.assert_called_once()

    # assert that the category content was loaded into system prompt
    assert "# Instructions" in scorer._system_prompt
    assert "Semantic Alignment:" in scorer._system_prompt


async def test_true_false_scorer_adds_to_memory(scorer_true_false_response: Message):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = _grounded_scorer(chat_target)

        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


async def test_self_ask_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = _grounded_scorer(chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskTrueFalseScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


async def test_self_ask_objective_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()

    json_response = (
        dedent(
            """
            {"badly_named_value": "True", "rationale": "rationale for true"}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = _grounded_scorer(chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskTrueFalseScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


def test_self_ask_true_false_scorer_identifier_has_system_prompt_template(patch_central_database):
    """Test that identifier includes system_prompt_template."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = _grounded_scorer(chat_target)

    # Access identifier via get_identifier() to trigger lazy build
    sid = scorer.get_identifier()

    # Should have system_prompt_template set in params
    assert sid.params["system_prompt_template"] is not None
    assert len(sid.params["system_prompt_template"]) > 0


def test_self_ask_true_false_get_identifier_type(patch_central_database):
    """Test that get_identifier returns correct class_name."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = _grounded_scorer(chat_target)

    identifier = scorer.get_identifier()

    assert identifier.class_name == "SelfAskTrueFalseScorer"
    assert hasattr(identifier, "hash")
    assert "system_prompt_template" in identifier.params


def test_self_ask_true_false_get_identifier_long_prompt_stored_in_full(patch_central_database):
    """Test that long system prompts are stored in full (no truncation) via to_dict()."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = _grounded_scorer(chat_target)

    identifier = scorer.get_identifier()

    # The identifier object stores the full prompt in params
    full_prompt = identifier.params["system_prompt_template"]
    assert full_prompt is not None
    assert len(full_prompt) > 100  # GROUNDED prompt is long

    # model_dump() flattens params and stores the full value (no truncation)
    id_dict = identifier.model_dump()
    assert id_dict["system_prompt_template"] == full_prompt


def test_true_false_question_from_yaml_loads_fields():
    """TrueFalseQuestion.from_yaml populates fields and render_params excludes category."""
    question = TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value)

    assert question.category == "grounded"
    assert question.true_description

    params = question.render_params
    assert set(params) == {"true_description", "false_description", "metadata"}
    assert "category" not in params


def test_true_false_question_from_yaml_raises_on_none():
    """TrueFalseQuestion.from_yaml raises when the YAML content is not a mapping."""
    with patch("pyrit.score.true_false.self_ask_true_false_scorer.yaml.safe_load", return_value=None):
        with pytest.raises(ValueError, match="Failed to load true_false_question YAML"):
            TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value)


def test_init_static_str_system_prompt(patch_central_database):
    """A plain string system prompt is used verbatim with no JSON schema."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    question = TrueFalseQuestion(
        category="harm",
        true_description="harmful",
        false_description="not harmful",
    )
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target,
        system_prompt="a static classifier prompt",
        question=question,
    )

    assert scorer._system_prompt == "a static classifier prompt"
    assert scorer._response_handler.response_schema is None
    assert scorer._score_category == ["harm"]


def test_init_static_seed_prompt_preserves_schema(patch_central_database):
    """A static SeedPrompt is used verbatim and its response_json_schema is preserved."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    seed_prompt = SeedPrompt(value="static seed prompt", data_type="text", response_json_schema={"type": "object"})
    question = TrueFalseQuestion(
        category="harm",
        true_description="harmful",
        false_description="not harmful",
    )
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target,
        system_prompt=seed_prompt,
        question=question,
    )

    assert scorer._system_prompt == "static seed prompt"
    assert scorer._response_handler.response_schema == {"type": "object"}


def test_init_default_system_prompt_uses_task_achieved(patch_central_database):
    """With only a chat_target, the scorer falls back to the default TASK_ACHIEVED rubric."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer(chat_target=chat_target)

    assert scorer._score_category == ["task_achieved"]
    assert scorer._response_handler.response_schema is not None
    assert "# Instructions" in scorer._system_prompt


def test_init_templated_seed_prompt_from_separate_files(patch_central_database):
    """Template YAML and question YAML can be separate: render the question, pass the SeedPrompt."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    question = TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value)
    seed_prompt = render_true_false_system_prompt(question=question)

    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target,
        system_prompt=seed_prompt,
        question=question,
    )

    assert scorer._score_category == ["grounded"]
    assert "# Instructions" in scorer._system_prompt
    # The schema embedded in the template survives model_copy during rendering.
    assert scorer._response_handler.response_schema is not None


async def test_init_scores_end_to_end(patch_central_database, scorer_true_false_response: Message):
    """A composition-built scorer performs a full scoring round-trip with the default JSON handler."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    question = TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value)
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target,
        system_prompt=render_true_false_system_prompt(question=question),
        question=question,
    )

    scores = await scorer.score_text_async("true false")

    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert scores[0].score_category == ["grounded"]


def test_init_raises_when_no_chat_target(patch_central_database):
    """A chat_target is required."""
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskTrueFalseScorer()


def test_from_question_sets_category(patch_central_database):
    """from_question renders a question into the system prompt and sets the score category."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer.from_question(
        chat_target=chat_target,
        question=TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value),
    )

    assert scorer._score_category == ["grounded"]
    assert "# Instructions" in scorer._system_prompt


def test_from_question_with_custom_question_sets_category(patch_central_database):
    """from_question accepts an in-memory TrueFalseQuestion and uses its category."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    custom_question = TrueFalseQuestion(
        category="custom_harm_category",
        true_description="The response contains harmful content.",
        false_description="The response does not contain harmful content.",
    )

    scorer = SelfAskTrueFalseScorer.from_question(chat_target=chat_target, question=custom_question)

    assert scorer._score_category == ["custom_harm_category"]


@pytest.mark.parametrize(
    "template",
    [
        "{{ true_description }} / {{ false_description }} / {{ metadata }}",
        SeedPrompt(
            value="{{ true_description }} / {{ false_description }} / {{ metadata }}",
            data_type="text",
        ),
    ],
)
def test_from_question_supports_custom_template(patch_central_database, template: SeedPrompt | str):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    question = TrueFalseQuestion(
        category="custom",
        true_description="yes",
        false_description="no",
        metadata="context",
    )

    scorer = SelfAskTrueFalseScorer.from_question(
        chat_target=chat_target,
        question=question,
        system_prompt_template=template,
    )

    assert scorer._system_prompt == "yes / no / context"


def test_init_custom_prompt_requires_question(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    with pytest.raises(ValueError, match="system_prompt and question must be provided together"):
        SelfAskTrueFalseScorer(chat_target=chat_target, system_prompt="custom")


def test_from_question_renders_metadata(patch_central_database):
    """Metadata supplied via TrueFalseQuestion makes it into the rendered system prompt."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    question = TrueFalseQuestion(
        category="custom_harm_category",
        true_description="positive",
        false_description="negative",
        metadata="extra-context",
    )

    scorer = SelfAskTrueFalseScorer.from_question(chat_target=chat_target, question=question)

    assert scorer._score_category == ["custom_harm_category"]
    assert "extra-context" in scorer._system_prompt


async def test_from_question_scores_end_to_end(patch_central_database, scorer_true_false_response: Message):
    """A scorer built via from_question performs a full scoring round-trip."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskTrueFalseScorer.from_question(
        chat_target=chat_target,
        question=TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value),
    )

    scores = await scorer.score_text_async("true false")

    assert len(scores) == 1
    assert scores[0].get_value() is True
