# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_CONTENT_CLASSIFIERS_PATH
from pyrit.exceptions import InvalidJsonException
from pyrit.models import (
    ComponentIdentifier,
    JsonSchemaDefinition,
    MessagePiece,
    Score,
    SeedPrompt,
    UnvalidatedScore,
)
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

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

_DEFAULT_CONTENT_CLASSIFIER_SYSTEM_PROMPT_PATH = (
    SCORER_CONTENT_CLASSIFIERS_PATH / "content_classifier_system_prompt.yaml"
)


class ContentClassifierPaths(enum.Enum):
    """Paths to content classifier YAML files."""

    HARMFUL_CONTENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "harm.yaml").resolve()
    SENTIMENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "sentiment.yaml").resolve()


class ContentClassifierCategory(BaseModel):
    """One named category in a content classifier."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ContentClassifier(BaseModel):
    """A set of categories and the fallback category used for content classification."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    categories: tuple[ContentClassifierCategory, ...] = Field(min_length=1)
    no_category_found: str

    @model_validator(mode="after")
    def _validate_fallback_category(self) -> ContentClassifier:
        category_names = [category.name for category in self.categories]
        if len(set(category_names)) != len(category_names):
            raise ValueError("Content classifier category names must be unique.")
        if self.no_category_found not in category_names:
            raise ValueError(f"Fallback category {self.no_category_found!r} is not present in categories.")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> ContentClassifier:
        """
        Load a content classifier from a YAML file.

        Args:
            path (str | Path): Path to the classifier YAML.

        Returns:
            ContentClassifier: The loaded classifier.

        Raises:
            ValueError: If the YAML does not contain a mapping or fails model validation.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"Content classifier YAML file '{resolved_path}' must contain a mapping.")
        return cls.model_validate(loaded)

    @property
    def render_params(self) -> dict[str, str]:
        """The Jinja parameters used to render a content-classifier system prompt."""
        categories = "".join(f"'{category.name}': {category.description}\n" for category in self.categories)
        return {
            "categories": categories,
            "no_category_found": self.no_category_found,
        }


def render_category_system_prompt(
    *,
    content_classifier: ContentClassifier,
    system_prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a content-classification scoring system prompt from a category list.

    The bundled content-classifier template is used when ``system_prompt_template`` is omitted.

    Args:
        content_classifier (ContentClassifier): The classifier supplying categories and fallback.
        system_prompt_template (SeedPrompt | str | None): A custom template or the bundled default.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    return _render_system_prompt_template(
        system_prompt_template=system_prompt_template,
        default_template_path=_DEFAULT_CONTENT_CLASSIFIER_SYSTEM_PROMPT_PATH,
        render_params=content_classifier.render_params,
        required_parameters=["categories", "no_category_found"],
    )


class _ContentClassifierResponseHandler(ResponseHandler):
    """Validate a parsed category score against its classifier."""

    def __init__(
        self,
        *,
        response_handler: ResponseHandler,
        content_classifier: ContentClassifier,
    ) -> None:
        self._response_handler = response_handler
        self._category_names = frozenset(category.name for category in content_classifier.categories)
        self._fallback_category = content_classifier.no_category_found

    @property
    def response_format(self) -> str | None:
        """The wrapped handler's response-format hint."""
        return self._response_handler.response_format

    @property
    def response_schema(self) -> JsonSchemaDefinition | None:
        """The wrapped handler's response schema."""
        return self._response_handler.response_schema

    def parse(
        self,
        *,
        response_text: str,
        scorer_identifier: ComponentIdentifier,
        scored_prompt_id: str | uuid.UUID,
        category: Sequence[str] | str | None = None,
        objective: str | None = None,
    ) -> UnvalidatedScore:
        """
        Parse a response and enforce the configured category/boolean relationship.

        Returns:
            UnvalidatedScore: The parsed score after classifier-specific validation.

        Raises:
            InvalidJsonException: If the category or score value violates the classifier.
        """
        score = self._response_handler.parse(
            response_text=response_text,
            scorer_identifier=scorer_identifier,
            scored_prompt_id=scored_prompt_id,
            category=category,
            objective=objective,
        )

        if score.score_category is None or len(score.score_category) != 1:
            raise InvalidJsonException(message="Content-classifier responses must contain exactly one category.")

        category_name = score.score_category[0]
        if category_name not in self._category_names:
            raise InvalidJsonException(
                message=f"Content-classifier response category {category_name!r} is not configured."
            )

        normalized_value = score.raw_score_value.strip().lower()
        if normalized_value not in {"true", "false"}:
            raise InvalidJsonException(message="Content-classifier score_value must be true or false.")

        expected_value = category_name != self._fallback_category
        if (normalized_value == "true") != expected_value:
            raise InvalidJsonException(
                message="Content-classifier score_value does not match whether the category is the fallback."
            )

        score.raw_score_value = normalized_value
        return score


class SelfAskCategoryScorer(TrueFalseScorer):
    """
    A class that represents a self-ask score for text classification and scoring.
    Given a ``ContentClassifier``, it scores according to its categories and returns the category
    the ``MessagePiece`` fits best.

    There is also a false category that is used if the MessagePiece does not fit any of the categories.

    The scorer holds a ``chat_target``, a ``system_prompt`` (typically rendered from a classifier
    via ``render_category_system_prompt``), and a ``response_handler``. The category is parsed from
    the target's response rather than fixed on the scorer. Use ``from_content_classifier`` to build
    the system prompt from a ``ContentClassifier``.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str,
        content_classifier: ContentClassifier,
        response_handler: ResponseHandler | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize a new instance of the SelfAskCategoryScorer class.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_category_system_prompt``) is used verbatim and may carry
                a ``response_json_schema``; a ``str`` is used as-is.
            content_classifier (ContentClassifier): The classifier represented by the prompt.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Raises:
            ValueError: If ``chat_target`` is not provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")
        super().__init__(
            score_aggregator=score_aggregator,
            validator=validator or self._DEFAULT_VALIDATOR,
            chat_target=chat_target,
        )

        self._prompt_target = chat_target
        self._content_classifier = content_classifier
        self._system_prompt, schema = self._resolve_system_prompt(system_prompt)
        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt, so the round-trip forwards it to the scoring
        # target. A caller-supplied handler owns its own response contract.
        self._response_handler = _ContentClassifierResponseHandler(
            response_handler=response_handler or JsonSchemaResponseHandler(response_schema=schema),
            content_classifier=content_classifier,
        )

    @classmethod
    def from_content_classifier(
        cls,
        *,
        chat_target: PromptTarget,
        content_classifier: ContentClassifier,
        system_prompt_template: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: ScorerPromptValidator | None = None,
    ) -> SelfAskCategoryScorer:
        """
        Build a scorer whose system prompt and response contract use one content classifier.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            content_classifier (ContentClassifier): The classifier to use.
            system_prompt_template (SeedPrompt | str | None): A custom Jinja template or the bundled
                content-classifier template.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use. Defaults to
                TrueFalseScoreAggregator.OR.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Returns:
            SelfAskCategoryScorer: The constructed scorer.
        """
        system_prompt = render_category_system_prompt(
            content_classifier=content_classifier,
            system_prompt_template=system_prompt_template,
        )
        return cls(
            chat_target=chat_target,
            system_prompt=system_prompt,
            content_classifier=content_classifier,
            response_handler=response_handler,
            score_aggregator=score_aggregator,
            validator=validator,
        )

    @staticmethod
    def _resolve_system_prompt(
        system_prompt: SeedPrompt | str,
    ) -> tuple[str, JsonSchemaDefinition | None]:
        if isinstance(system_prompt, SeedPrompt):
            return system_prompt.value, system_prompt.response_json_schema
        if isinstance(system_prompt, str):
            return system_prompt, None
        raise TypeError("system_prompt must be a SeedPrompt or str.")

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
                "content_classifier": self._content_classifier.model_dump(),
                "response_json_schema": self._response_handler.response_schema,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Scores the given message using the chat target.

        Args:
            message_piece (MessagePiece): The message piece to score.
            objective (str | None): The task based on which the text should be scored
                (the original attacker model's objective). Defaults to None.

        Returns:
            list[Score]: The message_piece's score.
                         The category that fits best in the response is used for score_category.
                         The score_value is True in all cases unless no category fits. In which case,
                         the score value is false and the _false_category is used.
        """
        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=message_piece.converted_value,
            data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            objective=objective,
        )

        score = unvalidated_score.to_score(score_value=unvalidated_score.raw_score_value, score_type="true_false")

        return [score]
