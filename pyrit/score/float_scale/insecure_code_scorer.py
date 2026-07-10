# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence
from pathlib import Path

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, JsonSchemaDefinition, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

_DEFAULT_INSECURE_CODE_SYSTEM_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "insecure_code" / "system_prompt.yaml"
_DEFAULT_HARM_CATEGORY = "security"


def render_insecure_code_system_prompt(
    *,
    harm_categories: str = _DEFAULT_HARM_CATEGORY,
    template_path: str | Path | None = None,
) -> SeedPrompt:
    """
    Render an insecure-code scoring system prompt from a template.

    Loads the templated system-prompt ``SeedPrompt`` (defaulting to the bundled
    ``insecure_code/system_prompt.yaml``) and renders it with the given harm categories. The
    returned ``SeedPrompt`` is a copy whose ``value`` is the rendered text; the template's other
    fields (notably ``response_json_schema``) are preserved so schema forwarding keeps working.

    Args:
        harm_categories (str): The harm category rendered into the template. Defaults to "security".
        template_path (str | Path | None): Path to the system-prompt template YAML. Defaults to the
            bundled insecure-code system prompt.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    resolved_path = verify_and_resolve_path(
        template_path if template_path else _DEFAULT_INSECURE_CODE_SYSTEM_PROMPT_PATH
    )
    template = SeedPrompt.from_yaml_file(resolved_path)
    rendered_value = template.render_template_value(harm_categories=harm_categories)
    return template.model_copy(update={"value": rendered_value})


class InsecureCodeScorer(FloatScaleScorer):
    """
    A scorer that uses an LLM to evaluate code snippets for potential security vulnerabilities.

    This scorer is intended for generated-code evaluation scenarios where the response to score is
    source code or a code-like snippet, such as insecure-coding parity checks across vulnerability
    scanners. It holds a chat ``chat_target``, a ``system_prompt`` (a rendered or static
    ``SeedPrompt``, a plain ``str``, or ``None`` for the default insecure-code rubric), and a
    ``response_handler`` that turns the target's raw output into a float-scale score.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        score_category: Sequence[str] | str | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the Insecure Code Scorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring.
            system_prompt (SeedPrompt | str | None): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_insecure_code_system_prompt``) is used verbatim and may
                carry a ``response_json_schema``; a ``str`` is used as-is; ``None`` falls back to the
                default insecure-code rubric. Defaults to None.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            score_category (Sequence[str] | str | None): The category to attach to scores. Defaults
                to "security".
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

        self._score_category: Sequence[str] | str = (
            score_category if score_category is not None else _DEFAULT_HARM_CATEGORY
        )

    @staticmethod
    def _resolve_system_prompt(
        system_prompt: SeedPrompt | str | None,
    ) -> tuple[str, JsonSchemaDefinition | None]:
        if system_prompt is None:
            rendered = render_insecure_code_system_prompt()
            return rendered.value, rendered.response_json_schema
        if isinstance(system_prompt, SeedPrompt):
            return system_prompt.value, system_prompt.response_json_schema
        if isinstance(system_prompt, str):
            return system_prompt, None
        raise TypeError("system_prompt must be a SeedPrompt, str, or None.")

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
            category=self._score_category,
            objective=objective,
        )

        # Convert UnvalidatedScore to Score, applying scaling and metadata
        score = unvalidated_score.to_score(
            score_value=str(self.scale_value_float(float(unvalidated_score.raw_score_value), 0, 1)),
            score_type="float_scale",
        )

        return [score]
