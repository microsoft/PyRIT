# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.component.adversarial_conversation_manager import _MessageView
from pyrit.executor.attack.core import AttackScoringConfig
from pyrit.executor.attack.core.attack_config import (
    DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE,
    AttackAdversarialConfig,
    resolve_adversarial_json_schema,
    resolve_adversarial_system_prompt,
)
from pyrit.models import Message, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TestAttackScoringConfig:
    """Test AttackScoringConfig validation functionality."""

    def test_init_with_valid_objective_scorer(self):
        """Test initialization with a valid TrueFalseScorer for objective_scorer."""
        mock_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(objective_scorer=mock_scorer)

        assert config.objective_scorer == mock_scorer

    def test_init_with_valid_refusal_scorer(self):
        """Test initialization with a valid TrueFalseScorer for refusal_scorer."""
        mock_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(refusal_scorer=mock_scorer)

        assert config.refusal_scorer == mock_scorer

    def test_init_with_both_valid_scorers(self):
        """Test initialization with valid TrueFalseScorers for both objective and refusal scorers."""
        mock_objective_scorer = MagicMock(spec=TrueFalseScorer)
        mock_refusal_scorer = MagicMock(spec=TrueFalseScorer)

        config = AttackScoringConfig(objective_scorer=mock_objective_scorer, refusal_scorer=mock_refusal_scorer)

        assert config.objective_scorer == mock_objective_scorer
        assert config.refusal_scorer == mock_refusal_scorer

    def test_init_raises_error_for_non_true_false_objective_scorer(self):
        """Test that initialization raises ValueError for non-TrueFalseScorer objective_scorer."""
        mock_scorer = MagicMock(spec=Scorer)

        with pytest.raises(ValueError, match="Objective scorer must be a TrueFalseScorer"):
            AttackScoringConfig(objective_scorer=mock_scorer)

    def test_init_raises_error_for_non_true_false_refusal_scorer(self):
        """Test that initialization raises ValueError for non-TrueFalseScorer refusal_scorer."""
        mock_scorer = MagicMock(spec=Scorer)

        with pytest.raises(ValueError, match="Refusal scorer must be a TrueFalseScorer"):
            AttackScoringConfig(refusal_scorer=mock_scorer)

    def test_init_with_none_scorers(self):
        """Test initialization with None for both scorers (default behavior)."""
        config = AttackScoringConfig()

        assert config.objective_scorer is None
        assert config.refusal_scorer is None

    def test_init_with_auxiliary_scorers(self):
        """Test initialization with auxiliary scorers."""
        mock_aux_scorer_1 = MagicMock(spec=Scorer)
        mock_aux_scorer_2 = MagicMock(spec=Scorer)

        config = AttackScoringConfig(auxiliary_scorers=[mock_aux_scorer_1, mock_aux_scorer_2])

        assert len(config.auxiliary_scorers) == 2
        assert config.auxiliary_scorers[0] == mock_aux_scorer_1
        assert config.auxiliary_scorers[1] == mock_aux_scorer_2

    def test_init_with_use_score_as_feedback_false(self):
        """Test initialization with use_score_as_feedback set to False."""
        config = AttackScoringConfig(use_score_as_feedback=False)

        assert config.use_score_as_feedback is False


class TestAttackAdversarialConfig:
    """Tests for AttackAdversarialConfig construction and its deprecation handling."""

    def test_both_system_prompt_and_path_logs_warning(self, caplog):
        """Setting both system_prompt and the deprecated system_prompt_path warns about precedence."""
        with caplog.at_level("WARNING"):
            AttackAdversarialConfig(
                target=MagicMock(spec=PromptTarget),
                system_prompt="inline {{ objective }}",
                system_prompt_path="some/legacy/path.yaml",
            )
        assert "takes precedence" in caplog.text


class TestResolveAdversarialSystemPrompt:
    """Tests for resolve_adversarial_system_prompt."""

    def test_inline_string_is_trusted_and_wrapped(self):
        """An inline string is wrapped in a Jinja SeedPrompt declaring the required parameters."""
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt="persona {{ objective }}")
        seed = resolve_adversarial_system_prompt(
            config=config,
            default_system_prompt_path="unused.yaml",
            required_parameters=["objective"],
        )
        assert seed.value == "persona {{ objective }}"
        assert "objective" in (seed.parameters or [])


_SCHEMA: dict = {"type": "object", "properties": {"next_message": {"type": "string"}}}
_OTHER_SCHEMA: dict = {"type": "object", "properties": {"foo": {"type": "string"}}}


def _seed_with_schema(schema: dict | None) -> SeedPrompt:
    return SeedPrompt(value="{{ objective }}", data_type="text", response_json_schema=schema)


class TestResolveAdversarialJsonSchema:
    """Tests for the module-level resolve_adversarial_json_schema helper."""

    def test_returns_none_when_neither_declares(self):
        assert resolve_adversarial_json_schema(system_prompt=None, first_message=None) is None
        assert (
            resolve_adversarial_json_schema(
                system_prompt=_seed_with_schema(None), first_message=_seed_with_schema(None)
            )
            is None
        )

    def test_returns_system_prompt_schema(self):
        result = resolve_adversarial_json_schema(
            system_prompt=_seed_with_schema(_SCHEMA), first_message=_seed_with_schema(None)
        )
        assert result == _SCHEMA

    def test_returns_first_message_schema(self):
        result = resolve_adversarial_json_schema(
            system_prompt=_seed_with_schema(None), first_message=_seed_with_schema(_SCHEMA)
        )
        assert result == _SCHEMA

    def test_raises_when_both_declare_schema(self):
        with pytest.raises(ValueError, match="only one of them"):
            resolve_adversarial_json_schema(
                system_prompt=_seed_with_schema(_SCHEMA), first_message=_seed_with_schema(_OTHER_SCHEMA)
            )


class TestGetJsonSchema:
    """Tests for AttackAdversarialConfig.get_json_schema."""

    def test_none_when_prompts_are_strings(self):
        config = AttackAdversarialConfig(
            target=MagicMock(spec=PromptTarget),
            system_prompt="persona {{ objective }}",
            first_message="seed {{ objective }}",
        )
        assert config.get_json_schema() is None

    def test_reads_schema_from_system_prompt(self):
        config = AttackAdversarialConfig(
            target=MagicMock(spec=PromptTarget),
            system_prompt=_seed_with_schema(_SCHEMA),
            first_message="seed {{ objective }}",
        )
        assert config.get_json_schema() == _SCHEMA

    def test_reads_schema_from_first_message(self):
        config = AttackAdversarialConfig(
            target=MagicMock(spec=PromptTarget),
            system_prompt=None,
            first_message=_seed_with_schema(_SCHEMA),
        )
        assert config.get_json_schema() == _SCHEMA

    def test_raises_when_both_declare_schema(self):
        config = AttackAdversarialConfig(
            target=MagicMock(spec=PromptTarget),
            system_prompt=_seed_with_schema(_SCHEMA),
            first_message=_seed_with_schema(_OTHER_SCHEMA),
        )
        with pytest.raises(ValueError, match="only one of them"):
            config.get_json_schema()

    def test_explicit_seedprompt_with_required_params_returned_as_is(self):
        """An explicitly provided SeedPrompt declaring the required params is returned unchanged."""
        provided = SeedPrompt(value="persona {{ objective }}", data_type="text", parameters=["objective"])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        seed = resolve_adversarial_system_prompt(
            config=config,
            default_system_prompt_path="unused.yaml",
            required_parameters=["objective"],
        )
        assert seed is provided

    def test_explicit_seedprompt_missing_required_params_raises(self):
        """An explicit SeedPrompt missing a required parameter raises ValueError."""
        provided = SeedPrompt(value="persona", data_type="text", parameters=[])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        with pytest.raises(ValueError, match="missing required parameters"):
            resolve_adversarial_system_prompt(
                config=config,
                default_system_prompt_path="unused.yaml",
                required_parameters=["objective"],
            )

    def test_explicit_seedprompt_missing_params_uses_custom_error_message(self):
        """A custom error_message overrides the default missing-parameters message."""
        provided = SeedPrompt(value="persona", data_type="text", parameters=[])
        config = AttackAdversarialConfig(target=MagicMock(spec=PromptTarget), system_prompt=provided)
        with pytest.raises(ValueError, match="must declare objective"):
            resolve_adversarial_system_prompt(
                config=config,
                default_system_prompt_path="unused.yaml",
                required_parameters=["objective"],
                error_message="must declare objective",
            )


def _template_message(value: str = "Hello", *, data_type: str = "text", error: str = "none") -> Message:
    """Build a single-piece objective-target response for template rendering."""
    piece = MessagePiece(role="assistant", original_value=value, original_value_data_type=data_type)
    piece.response_error = error
    return Message(message_pieces=[piece])


def _feedback_score(rationale: str = "Because reasons") -> Score:
    """Build a scorer result carrying a rationale used as adversarial feedback."""
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=["test"],
        score_value_description="d",
        score_rationale=rationale,
        score_metadata={},
        message_piece_id="00000000-0000-0000-0000-000000000000",
    )


def _render_default_adversarial_template(
    *, message: Message, score: Score | None = None, use_score_as_feedback: bool = False
) -> str:
    """Render ``DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE`` the way the manager does per turn."""
    template = SeedPrompt(value=DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE, data_type="text", is_jinja_template=True)
    return template.render_template_value_silent(
        objective="the objective",
        score=score,
        message=_MessageView(message),
        use_score_as_feedback=use_score_as_feedback,
    )


class TestDefaultAdversarialPromptTemplate:
    """Coverage for every branch of the default per-turn adversarial prompt template."""

    def test_blocked_response_asks_for_rewrite(self):
        """A blocked objective response yields a rewrite instruction, ignoring text/score."""
        rendered = _render_default_adversarial_template(message=_template_message("", error="blocked"))
        assert rendered == (
            "Request to target failed: blocked. Please rewrite your prompt to avoid getting blocked next time."
        )

    def test_error_response_surfaces_error_code(self):
        """An errored objective response surfaces the error code."""
        rendered = _render_default_adversarial_template(message=_template_message("", error="processing"))
        assert rendered == "Request to target failed: processing"

    def test_text_response_passed_through(self):
        """A plain text response is handed to the adversarial chat verbatim."""
        rendered = _render_default_adversarial_template(message=_template_message("Hello"))
        assert rendered == "Hello"

    def test_text_response_appends_feedback_when_enabled(self):
        """With use_score_as_feedback enabled, the score rationale is appended to the text."""
        rendered = _render_default_adversarial_template(
            message=_template_message("Hello"), score=_feedback_score(), use_score_as_feedback=True
        )
        assert rendered == "Hello\n\nBecause reasons"

    def test_text_response_ignores_feedback_when_disabled(self):
        """Without use_score_as_feedback, the score rationale is not appended to the text."""
        rendered = _render_default_adversarial_template(
            message=_template_message("Hello"), score=_feedback_score(), use_score_as_feedback=False
        )
        assert rendered == "Hello"

    def test_no_text_response_uses_feedback_only(self):
        """A response with no usable text falls back to the score rationale as feedback."""
        rendered = _render_default_adversarial_template(
            message=_template_message("/tmp/out.png", data_type="image_path"),
            score=_feedback_score(),
            use_score_as_feedback=True,
        )
        assert rendered == "Because reasons"

    def test_empty_response_nudges_to_continue(self):
        """A response with no text and no feedback nudges the adversarial chat to continue."""
        rendered = _render_default_adversarial_template(
            message=_template_message("/tmp/out.png", data_type="image_path")
        )
        assert rendered == "The previous response was empty. Please continue."
