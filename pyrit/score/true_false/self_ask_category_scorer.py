# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from pathlib import Path

import yaml

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_CONTENT_CLASSIFIERS_PATH
from pyrit.models import ComponentIdentifier, JsonSchemaDefinition, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

_DEFAULT_CONTENT_CLASSIFIER_SYSTEM_PROMPT_PATH = (
    SCORER_CONTENT_CLASSIFIERS_PATH / "content_classifier_system_prompt.yaml"
)


class ContentClassifierPaths(enum.Enum):
    """Paths to content classifier YAML files."""

    HARMFUL_CONTENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "harm.yaml").resolve()
    SENTIMENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "sentiment.yaml").resolve()


def _content_classifier_to_string(categories: list[dict[str, str]], no_category_found: str) -> str:
    """
    Convert content-classifier categories to a string representation for a system prompt.

    Args:
        categories (list[dict[str, str]]): The categories to convert.
        no_category_found (str): The category used when none of the others fit. Must be present in
            the rendered category descriptions.

    Returns:
        str: The string representation of the categories.

    Raises:
        ValueError: If no categories are provided, or the ``no_category_found`` category is not
            found in the provided categories.
    """
    if not categories:
        raise ValueError("Improperly formatted content classifier yaml file. No categories provided")

    category_descriptions = ""

    for category in categories:
        name = category["name"]
        desc = category["description"]

        category_descriptions += f"'{name}': {desc}\n"

    if no_category_found not in category_descriptions:
        raise ValueError(f"False category {no_category_found} not found in classifier categories")

    return category_descriptions


def render_category_system_prompt(
    *,
    categories: list[dict[str, str]],
    no_category_found: str,
    template_path: str | Path | None = None,
) -> SeedPrompt:
    """
    Render a content-classification scoring system prompt from a category list.

    Joins the classifier categories into a description block, then loads and renders the templated
    system-prompt ``SeedPrompt`` (defaulting to the bundled
    ``content_classifier_system_prompt.yaml``). The returned ``SeedPrompt`` is a copy whose
    ``value`` is the rendered text; the template's other fields (notably ``response_json_schema``)
    are preserved so schema forwarding keeps working.

    Args:
        categories (list[dict[str, str]]): The classifier categories, each a mapping with ``name``
            and ``description`` keys.
        no_category_found (str): The category used when none of the others fit.
        template_path (str | Path | None): Path to the system-prompt template YAML. Defaults to the
            bundled content-classifier system prompt.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    categories_as_string = _content_classifier_to_string(categories, no_category_found)
    resolved_path = verify_and_resolve_path(
        template_path if template_path else _DEFAULT_CONTENT_CLASSIFIER_SYSTEM_PROMPT_PATH
    )
    template = SeedPrompt.from_yaml_file(resolved_path)
    rendered_value = template.render_template_value(
        categories=categories_as_string,
        no_category_found=no_category_found,
    )
    return template.model_copy(update={"value": rendered_value})


class SelfAskCategoryScorer(TrueFalseScorer):
    """
    A class that represents a self-ask score for text classification and scoring.
    Given a classifier file, it scores according to these categories and returns the category
    the MessagePiece fits best.

    There is also a false category that is used if the MessagePiece does not fit any of the categories.

    The scorer holds a ``chat_target``, a ``system_prompt`` (typically rendered from a classifier
    via ``render_category_system_prompt``), and a ``response_handler``. The category is parsed from
    the target's response rather than fixed on the scorer. Use ``from_content_classifier`` to build
    the system prompt directly from a classifier YAML file.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize a new instance of the SelfAskCategoryScorer class.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str | None): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_category_system_prompt``) is used verbatim and may carry
                a ``response_json_schema``; a ``str`` is used as-is. Required.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Raises:
            ValueError: If ``chat_target`` or ``system_prompt`` is not provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")
        if system_prompt is None:
            raise ValueError("system_prompt must be provided.")

        super().__init__(
            score_aggregator=score_aggregator,
            validator=validator or self._DEFAULT_VALIDATOR,
            chat_target=chat_target,
        )

        self._prompt_target = chat_target
        self._system_prompt, schema = self._resolve_system_prompt(system_prompt)
        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt, so the round-trip forwards it to the scoring
        # target. A caller-supplied handler owns its own response contract.
        self._response_handler = response_handler or JsonSchemaResponseHandler(response_schema=schema)

    @classmethod
    def from_content_classifier(
        cls,
        *,
        chat_target: PromptTarget,
        content_classifier_path: str | Path,
        response_handler: ResponseHandler | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: ScorerPromptValidator | None = None,
    ) -> "SelfAskCategoryScorer":
        """
        Build a scorer whose system prompt is rendered from a content-classifier YAML.

        Loads the classifier categories and ``no_category_found`` marker from
        ``content_classifier_path`` and renders the content-classification system prompt via
        ``render_category_system_prompt``.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            content_classifier_path (str | Path): Path to the classifier YAML file (e.g. a
                ``ContentClassifierPaths`` value).
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use. Defaults to
                TrueFalseScoreAggregator.OR.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Returns:
            SelfAskCategoryScorer: The constructed scorer.
        """
        classifier_contents = yaml.safe_load(
            verify_and_resolve_path(content_classifier_path).read_text(encoding="utf-8")
        )
        system_prompt = render_category_system_prompt(
            categories=classifier_contents["categories"],
            no_category_found=classifier_contents["no_category_found"],
        )
        return cls(
            chat_target=chat_target,
            system_prompt=system_prompt,
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
