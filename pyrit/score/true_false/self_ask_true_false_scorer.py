# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, JsonSchemaDefinition, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

TRUE_FALSE_QUESTIONS_PATH = Path(SCORER_SEED_PROMPT_PATH, "true_false_question").resolve()

_DEFAULT_TRUE_FALSE_SYSTEM_PROMPT_PATH = TRUE_FALSE_QUESTIONS_PATH / "true_false_system_prompt.yaml"
_DEFAULT_FALSE_DESCRIPTION = "Anything that does not fulfill or is not related to the true description."


class TrueFalseQuestionPaths(enum.Enum):
    """Paths to true/false question YAML files."""

    CURRENT_EVENTS = Path(TRUE_FALSE_QUESTIONS_PATH, "current_events.yaml").resolve()
    GROUNDED = Path(TRUE_FALSE_QUESTIONS_PATH, "grounded.yaml").resolve()
    PROMPT_INJECTION = Path(TRUE_FALSE_QUESTIONS_PATH, "prompt_injection.yaml").resolve()
    QUESTION_ANSWERING = Path(TRUE_FALSE_QUESTIONS_PATH, "question_answering.yaml").resolve()
    GANDALF = Path(TRUE_FALSE_QUESTIONS_PATH, "gandalf.yaml").resolve()
    YES_NO = Path(TRUE_FALSE_QUESTIONS_PATH, "yes_no_answer.yaml").resolve()
    TASK_ACHIEVED = Path(TRUE_FALSE_QUESTIONS_PATH, "task_achieved.yaml").resolve()
    # This is an LLM-powered refinement of the TASK_ACHIEVED rubric
    TASK_ACHIEVED_REFINED = Path(TRUE_FALSE_QUESTIONS_PATH, "task_achieved_refined.yaml").resolve()
    CRIMINAL_PERSONA = Path(TRUE_FALSE_QUESTIONS_PATH, "criminal_persona.yaml").resolve()


class TrueFalseQuestion(BaseModel):
    """
    A value type representing a true/false scoring question.

    Owns the descriptive parameters (``category``, ``true_description``, ``false_description``,
    ``metadata``) that are rendered into a true/false scoring system prompt. It can be constructed
    directly or loaded from a YAML file via ``from_yaml``, and it exposes the Jinja render
    parameters via ``render_params`` so a templated ``SeedPrompt`` can be rendered independently
    of how the question was obtained (e.g. template YAML and question YAML kept in separate files).
    """

    model_config = ConfigDict(extra="ignore", frozen=True, validate_default=True)

    true_description: str
    false_description: str = ""
    category: str = ""
    metadata: str = ""

    @field_validator("false_description")
    @classmethod
    def _apply_false_description_fallback(cls, false_description: str) -> str:
        return false_description or _DEFAULT_FALSE_DESCRIPTION

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TrueFalseQuestion":
        """
        Load a ``TrueFalseQuestion`` from a YAML file.

        Args:
            path (str | Path): Path to the true/false question YAML file.

        Returns:
            TrueFalseQuestion: The loaded question.

        Raises:
            ValueError: If the file does not contain a YAML mapping.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError("Failed to load true_false_question YAML")
        known = {
            key: loaded[key]
            for key in ("category", "true_description", "false_description", "metadata")
            if key in loaded
        }
        return cls(**known)

    @property
    def render_params(self) -> dict[str, str]:
        """The Jinja parameters used to render the true/false scoring system prompt."""
        return {
            "true_description": self.true_description,
            "false_description": self.false_description,
            "metadata": self.metadata,
        }


def render_true_false_system_prompt(
    *,
    question: TrueFalseQuestion,
    system_prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a true/false scoring system prompt from a question and a template.

    Uses the bundled true/false template when ``system_prompt_template`` is omitted.

    Args:
        question (TrueFalseQuestion): The question supplying the render parameters.
        system_prompt_template (SeedPrompt | str | None): A custom template or the bundled default.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    return _render_system_prompt_template(
        system_prompt_template=system_prompt_template,
        default_template_path=_DEFAULT_TRUE_FALSE_SYSTEM_PROMPT_PATH,
        render_params=question.render_params,
        required_parameters=["true_description", "false_description"],
    )


class SelfAskTrueFalseScorer(TrueFalseScorer):
    """
    A self-ask true/false scorer with scorer-owned composition.

    The scorer holds a ``chat_target``, a ``system_prompt``, the ``TrueFalseQuestion`` represented by
    that prompt, and a ``response_handler`` that turns the target's raw output into a score.

    Two construction modes are supported:

    - Default: omit both ``system_prompt`` and ``question`` to use the bundled TASK_ACHIEVED rubric.
    - Custom: use ``from_question`` to render a template from one question, or pass both an already
      rendered ``system_prompt`` and its ``question`` directly.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text", "image_path"],
    )
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str | None = None,
        question: TrueFalseQuestion | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the SelfAskTrueFalseScorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str | None): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_true_false_system_prompt``) is used verbatim and may
                carry a ``response_json_schema``; a ``str`` is used as-is; ``None`` falls back to the
                default TASK_ACHIEVED rubric. Defaults to None.
            question (TrueFalseQuestion | None): The question represented by a custom system prompt.
                Omit only when also using the bundled default rubric.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use. Defaults to
                TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If ``chat_target`` is not provided or only one of ``system_prompt`` and
                ``question`` is provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")

        super().__init__(
            validator=validator or self._DEFAULT_VALIDATOR,
            score_aggregator=score_aggregator,
            chat_target=chat_target,
        )

        self._prompt_target = chat_target

        rendered_value, schema, resolved_question = self._resolve_system_prompt(
            system_prompt=system_prompt,
            question=question,
        )
        self._system_prompt = rendered_value
        self._question = resolved_question
        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt, so the round-trip forwards it to the
        # scoring target (enforced natively when supported, omitted via normalization otherwise). A
        # caller-supplied handler owns its own response contract, including any schema.
        self._response_handler = response_handler or JsonSchemaResponseHandler(response_schema=schema)
        self._score_category = [resolved_question.category]

    @staticmethod
    def _resolve_system_prompt(
        *,
        system_prompt: SeedPrompt | str | None,
        question: TrueFalseQuestion | None,
    ) -> tuple[str, JsonSchemaDefinition | None, TrueFalseQuestion]:
        if system_prompt is None and question is None:
            default_question = TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.TASK_ACHIEVED.value)
            rendered = render_true_false_system_prompt(question=default_question)
            return rendered.value, rendered.response_json_schema, default_question
        if system_prompt is None or question is None:
            raise ValueError("system_prompt and question must be provided together.")
        if isinstance(system_prompt, SeedPrompt):
            return system_prompt.value, system_prompt.response_json_schema, question
        if isinstance(system_prompt, str):
            return system_prompt, None, question
        raise TypeError("system_prompt must be a SeedPrompt, str, or None.")

    @classmethod
    def from_question(
        cls,
        *,
        chat_target: PromptTarget,
        question: TrueFalseQuestion,
        system_prompt_template: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> "SelfAskTrueFalseScorer":
        """
        Build a scorer whose system prompt and category are driven by a ``TrueFalseQuestion``.

        Renders the true/false scoring system prompt from ``question`` (via
        ``render_true_false_system_prompt``) and sets ``score_category`` from ``question.category``.
        Use this when a preset question drives more than the prompt; for a fully custom or static
        prompt, construct the scorer directly with ``system_prompt``.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            question (TrueFalseQuestion): The question supplying the system prompt and category.
            system_prompt_template (SeedPrompt | str | None): A custom Jinja template or the bundled
                true/false template.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use. Defaults to
                TrueFalseScoreAggregator.OR.

        Returns:
            SelfAskTrueFalseScorer: The constructed scorer.
        """
        return cls(
            chat_target=chat_target,
            system_prompt=render_true_false_system_prompt(
                question=question,
                system_prompt_template=system_prompt_template,
            ),
            question=question,
            response_handler=response_handler,
            validator=validator,
            score_aggregator=score_aggregator,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
                "user_prompt_template": "objective: {objective}\nresponse: {response}",
                "question": self._question.model_dump(),
                "response_json_schema": self._response_handler.response_schema,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Scores the given message piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the text or image to be scored.
            objective (str | None): The objective to evaluate against (the original attacker model's objective).
                Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object.
                The category is configured from the TrueFalseQuestionPath.
                The score_value is True or False based on which description fits best.
                Metadata can be configured to provide additional information.
        """
        # Build scoring prompt - for non-text content, extra context about objective is sent as a prepended text piece
        is_non_text = message_piece.converted_value_data_type != "text"
        if is_non_text:
            prepended_text = f"objective: {objective}\nresponse:"
            scoring_value = message_piece.converted_value
            scoring_data_type = message_piece.converted_value_data_type
        else:
            prepended_text = None
            scoring_value = f"objective: {objective}\nresponse: {message_piece.converted_value}"
            scoring_data_type = "text"

        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=scoring_value,
            data_type=scoring_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            prepended_text=prepended_text,
            category=self._score_category,
            objective=objective,
        )

        score = unvalidated_score.to_score(score_value=unvalidated_score.raw_score_value, score_type="true_false")
        return [score]
