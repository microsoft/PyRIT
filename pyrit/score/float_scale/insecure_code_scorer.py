# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, JsonSchemaDefinition, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template

_DEFAULT_INSECURE_CODE_SYSTEM_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "insecure_code" / "system_prompt.yaml"
_DEFAULT_HARM_CATEGORY = "security"


def _normalize_harm_categories(harm_categories: Sequence[str] | str) -> tuple[str, ...]:
    categories = (harm_categories,) if isinstance(harm_categories, str) else tuple(harm_categories)
    if not categories or any(not isinstance(category, str) or not category.strip() for category in categories):
        raise ValueError("harm_categories must contain at least one non-empty string.")
    if len(set(categories)) != len(categories):
        raise ValueError("harm_categories must not contain duplicates.")
    return categories


def render_insecure_code_system_prompt(
    *,
    harm_categories: Sequence[str] | str = _DEFAULT_HARM_CATEGORY,
    system_prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render an insecure-code scoring system prompt from a template.

    Uses the bundled insecure-code template when ``system_prompt_template`` is omitted.

    Args:
        harm_categories (Sequence[str] | str): Harm categories rendered into the template.
        system_prompt_template (SeedPrompt | str | None): A custom template or the bundled default.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.

    Raises:
        ValueError: If ``harm_categories`` is empty.
    """
    rendered_categories = ", ".join(_normalize_harm_categories(harm_categories))
    return _render_system_prompt_template(
        system_prompt_template=system_prompt_template,
        default_template_path=_DEFAULT_INSECURE_CODE_SYSTEM_PROMPT_PATH,
        render_params={"harm_categories": rendered_categories},
        required_parameters=["harm_categories"],
    )


class InsecureCodeScorer(FloatScaleScorer):
    """
    A scorer that uses an LLM to evaluate code snippets for potential security vulnerabilities.

    This scorer is intended for generated-code evaluation scenarios where the response to score is
    source code or a code-like snippet, such as insecure-coding parity checks across vulnerability
    scanners. It holds a chat ``chat_target``, a ``system_prompt`` (a rendered or static
    ``SeedPrompt`` or plain ``str``), and a
    ``response_handler`` that turns the target's raw output into a float-scale score.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str,
        harm_categories: Sequence[str] | str,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the Insecure Code Scorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring.
            system_prompt (SeedPrompt | str): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_insecure_code_system_prompt``) is used verbatim and may
                carry a ``response_json_schema``; a ``str`` is used as-is.
            harm_categories (Sequence[str] | str): The category or categories represented by the
                system prompt.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            validator (ScorerPromptValidator | None): Custom validator for the scorer. Defaults to
                None.

        Raises:
            ValueError: If ``chat_target`` is not provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, chat_target=chat_target)

        self._prompt_target = chat_target

        rendered_value, schema = self._resolve_system_prompt(system_prompt)
        self._system_prompt = rendered_value
        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt and enforces the numeric score contract, so
        # the round-trip forwards the schema to the scoring target. A caller-supplied handler owns
        # its own response contract.
        self._response_handler = response_handler or JsonSchemaResponseHandler(
            response_schema=schema, numeric_value=True
        )

        self._harm_categories = _normalize_harm_categories(harm_categories)

    @classmethod
    def from_harm_categories(
        cls,
        *,
        chat_target: PromptTarget,
        harm_categories: Sequence[str] | str = _DEFAULT_HARM_CATEGORY,
        system_prompt_template: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> "InsecureCodeScorer":
        """
        Build a scorer whose prompt and score metadata use the same harm categories.

        Returns:
            InsecureCodeScorer: The constructed scorer.
        """
        normalized_categories = _normalize_harm_categories(harm_categories)
        return cls(
            chat_target=chat_target,
            system_prompt=render_insecure_code_system_prompt(
                harm_categories=normalized_categories,
                system_prompt_template=system_prompt_template,
            ),
            harm_categories=normalized_categories,
            response_handler=response_handler,
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
                "harm_categories": self._harm_categories,
                "response_json_schema": self._response_handler.response_schema,
            },
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Scores the given message piece using LLM to detect security vulnerabilities.

        Args:
            message_piece (MessagePiece): The code snippet to be scored.
            objective (str | None): Optional objective description for scoring. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object.

        Raises:
            InvalidJsonException: If the response is not valid JSON or the score value is not a float.
        """
        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=message_piece.original_value,
            data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            category=self._harm_categories,
            objective=objective,
        )

        # Convert UnvalidatedScore to Score, applying scaling and metadata
        score = unvalidated_score.to_score(
            score_value=str(self.scale_value_float(float(unvalidated_score.raw_score_value), 0, 1)),
            score_type="float_scale",
        )

        return [score]
