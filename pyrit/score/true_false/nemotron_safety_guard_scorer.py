# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from contextvars import ContextVar
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
    ScoringExpectation,
    SeedPrompt,
)
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _parse_judgment_observation
from pyrit.score.message_scorable_resolver import _AssistantContentMessageResolver
from pyrit.score.observation.target_judge import JudgmentRequest, TargetJudge
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.nemotron_safety_guard_parser import (
    NemotronSafetyGuardLabel,
    parse_nemotron_safety_guard_response,
)
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import Observation
    from pyrit.score.observation.execution import _ObservationEvidence

_DEFAULT_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "nemotron_safety_guard" / "nemotron_safety_guard_prompt.yaml"
_PROMPT_PARAMETERS = ("user_prompt", "response")

# The user prompt belongs to the conversation, not to an individual piece, so it is resolved once
# per scored message and handed to the pieces through this. A ContextVar rather than an attribute
# because pieces are scored under asyncio.gather, which copies the context into each child task,
# so this stays correct if one scorer instance is used for several messages at once.
_RESOLVED_USER_PROMPT: ContextVar[str | None] = ContextVar("nemotron_safety_guard_user_prompt", default=None)

_MISSING_USER_PROMPT_MESSAGE = (
    "Nemotron Safety Guard classifies a user prompt and a model response together, so it needs the "
    "prompt that produced the response being scored. Score a piece that follows a user turn in a "
    "stored conversation, pass an objective, or pass user_prompt= to the scorer."
)

_EMPTY_RESPONSE_MESSAGE = (
    "Nemotron Safety Guard was asked to judge an empty response. Its prompt tells it to omit the "
    "response verdict when no response is present, so the verdict has no true/false reading. Score "
    "a response with content, or select NemotronSafetyGuardLabel.USER_SAFETY to judge the prompt."
)


def _coerce_label(label: NemotronSafetyGuardLabel | str) -> NemotronSafetyGuardLabel:
    """
    Accept the enum or its serialized value.

    ``ScorerRegistry`` inspects constructor signatures with ``inspect.signature``, which under
    postponed annotations reports the annotation as a string rather than the enum, so a configured
    ``"User Safety"`` arrives as the raw string.

    Args:
        label (NemotronSafetyGuardLabel | str): The label, or its value such as ``"User Safety"``.

    Returns:
        NemotronSafetyGuardLabel: The corresponding enum member.

    Raises:
        ValueError: If the value does not name a label.
    """
    if isinstance(label, NemotronSafetyGuardLabel):
        return label
    normalized = label.strip().casefold()
    for member in NemotronSafetyGuardLabel:
        if normalized in (member.value.casefold(), member.name.casefold()):
            return member
    valid = ", ".join(member.value for member in NemotronSafetyGuardLabel)
    raise ValueError(f"Unknown Nemotron Safety Guard label {label!r}. Expected one of: {valid}.")


def render_nemotron_safety_guard_prompt(
    *,
    response: str,
    user_prompt: str,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a Nemotron Safety Guard classification request for one prompt and response pair.

    Args:
        response (str): The model response being classified.
        user_prompt (str): The user prompt that produced ``response``.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the bundled
            template, which is the prompt NeMo Guardrails sends to these models.

    Returns:
        SeedPrompt: The rendered request prompt.
    """
    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_DEFAULT_PROMPT_PATH,
        render_params={"user_prompt": user_prompt, "response": response},
        required_parameters=_PROMPT_PARAMETERS,
    )


class NemotronSafetyGuardScorer(MessageTrueFalseScorer):
    """
    Classify a prompt and response pair with an NVIDIA Nemotron Safety Guard model.

    NVIDIA's content safety guard models, ``nvidia/Llama-3.1-Nemotron-Safety-Guard-8B-v3``
    (multilingual) and ``nvidia/llama-3.1-nemoguard-8b-content-safety``, judge a user prompt and a
    model response together against the 23-category Aegis 2.0 taxonomy and return a safe or unsafe
    verdict for each, with the violated category names. ``label`` selects which verdict becomes the
    boolean score, True for unsafe; the other verdict and the categories are kept in the score
    metadata.

    The scored message is the model response. The prompt it is judged against is, in order, the
    fixed ``user_prompt``, the latest earlier user turn of the scored conversation, or the scoring
    objective.

    The bundled request is the prompt NeMo Guardrails sends to both models, as one user message.
    Serve the model from a chat endpoint that applies its chat template, such as vLLM's OpenAI
    server or an NVIDIA NIM, at temperature 0.

    Reference: [@joshi2025cultureguard], [@ghosh2025aegis]
    Paper: https://arxiv.org/abs/2508.01710
    """

    SCORE_CATEGORY: ClassVar[str] = "nemotron_safety_guard"
    RESPONSE_PARSER_FINGERPRINT: ClassVar[str] = "nemotron-safety-guard-response-v1"
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"], supported_roles=["assistant"]
    )

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        label: NemotronSafetyGuardLabel | str = NemotronSafetyGuardLabel.RESPONSE_SAFETY,
        user_prompt: str | None = None,
        prompt_template: SeedPrompt | str | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the Nemotron Safety Guard scorer.

        Args:
            chat_target (PromptTarget): A chat target serving a Nemotron Safety Guard model.
            label (NemotronSafetyGuardLabel | str): Which verdict becomes the score value, as the
                enum or its value such as ``"User Safety"``, which is what a serialized
                configuration supplies. Defaults to ``NemotronSafetyGuardLabel.RESPONSE_SAFETY``.
            user_prompt (str | None): Fixed prompt to classify responses against, which takes
                precedence over the scored conversation and the objective. Defaults to None. An
                explicitly blank override is rejected at scoring time rather than falling back.
            prompt_template (SeedPrompt | str | None): Custom request template referencing
                ``user_prompt`` and ``response``. Defaults to the bundled template.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to assistant text
                only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If ``label`` does not name one of the guard's verdicts.
        """
        label = _coerce_label(label)

        self._prompt_target = chat_target
        self._judge = TargetJudge(target=chat_target, requirements=type(self).TARGET_REQUIREMENTS)
        self._label = label
        self._user_prompt = user_prompt
        self._prompt_template = _resolve_prompt_template(prompt_template=prompt_template)
        self._response_handler = CallableResponseHandler(
            parser=partial(parse_nemotron_safety_guard_response, label=label),
            parser_fingerprint=self.RESPONSE_PARSER_FINGERPRINT,
        )

        super().__init__(
            validator=validator or self._DEFAULT_VALIDATOR,
            score_aggregator=score_aggregator,
            message_resolver=_AssistantContentMessageResolver(),
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the scorer identifier.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        params: dict[str, Any] = {
            "label": self._label.value,
            "prompt_template": self._prompt_template.value,
            # A fixed prompt changes the request that gets sent, so it belongs in the identity.
            "user_prompt": self._user_prompt,
        }
        return self._create_identifier(
            params=params,
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:missing-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _resolve_user_prompt_async(self, message_piece: MessagePiece, *, objective: str | None) -> str | None:
        """
        Find the user prompt that a scored response is judged against.

        Called once per scored message rather than once per piece, so that several pieces do not
        issue concurrent memory reads.

        Args:
            message_piece (MessagePiece): Any piece of the scored message. Only its conversation and
                sequence are read, which are shared across the message.
            objective (str | None): The scoring objective, used when the conversation holds no
                earlier user turn, as for a response scored on its own.

        Returns:
            str | None: The configured prompt, otherwise the latest earlier user turn of the scored
                conversation, otherwise the objective, otherwise None. Blank context returns None.
        """
        if self._user_prompt is not None:
            return self._user_prompt if self._user_prompt.strip() else None

        if message_piece.conversation_id and message_piece.sequence >= 1:
            conversation = await self._memory.get_message_pieces_async(conversation_id=message_piece.conversation_id)
            prior_user_pieces = [
                piece for piece in conversation if piece.sequence < message_piece.sequence and piece.api_role == "user"
            ]
            if prior_user_pieces:
                # Select the latest user turn before filtering by data type, so a turn with no text
                # does not silently fall back to an older one. The converted value is what the
                # target received.
                user_sequence = max(piece.sequence for piece in prior_user_pieces)
                prompt = "\n".join(
                    piece.converted_value
                    for piece in prior_user_pieces
                    if piece.sequence == user_sequence and piece.converted_value_data_type == "text"
                )
                return prompt if prompt.strip() else None

        return objective if objective and objective.strip() else None

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Render the guard request and convert the result.

        Returns:
            list[Score]: The guard's verdict for the piece.

        Raises:
            ValueError: If no user prompt is available.
        """
        user_prompt = _RESOLVED_USER_PROMPT.get()
        if not user_prompt:
            raise ValueError(_MISSING_USER_PROMPT_MESSAGE)

        request_prompt = render_nemotron_safety_guard_prompt(
            response=message_piece.converted_value,
            user_prompt=user_prompt,
            prompt_template=self._prompt_template,
        )
        unvalidated_score = await self._judge.judge_async(
            response_handler=self._response_handler,
            request=self._capture_judgment_evidence(
                JudgmentRequest(
                    expectation=expectation,
                    system_prompt=None,
                    value=request_prompt.value,
                    data_type="text",
                    scored_prompt_id=message_piece.id,
                    scorer_identifier=self.get_identifier(),
                    judgment_replay_identifier=self._get_judgment_replay_identifier(),
                    category=self.SCORE_CATEGORY,
                )
            ),
        )
        return [
            unvalidated_score.to_score(
                score_value=unvalidated_score.raw_score_value,
                score_type="true_false",
            )
        ]

    def _judgment_replay_identifier(self) -> dict[str, object]:
        """Return the Nemotron Safety Guard judgment contract."""
        return {"version": 1}

    def _score_judgment_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Replay the retained response from the guard's scoring target.

        Returns:
            list[Score]: The replayed score.
        """
        unvalidated = _parse_judgment_observation(
            observation=observation,
            evidence=evidence,
            response_handler=self._response_handler,
            scorer_identifier=self.get_identifier(),
            judgment_replay_identifier=self._get_judgment_replay_identifier(),
            expectation=expectation,
            category=self.SCORE_CATEGORY,
        )
        return [
            unvalidated.to_score(
                score_value=unvalidated.raw_score_value.lower(),
                score_type="true_false",
            )
        ]

    async def _score_async(
        self, message: Message, *, objective: str | None = None, expectation: ScoringExpectation | None = None
    ) -> list[Score]:
        """
        Resolve the user prompt once for the message, then score every supported piece.

        The user prompt is a property of the conversation, so every piece is judged against the same
        one.

        Args:
            message (Message): The message to score.
            objective (str | None): Objective retained on the resulting score, and the user prompt
                when the conversation holds none. Defaults to None.
            expectation (ScoringExpectation | None): Complete criteria passed through aggregation.

        Returns:
            list[Score]: A single aggregated true/false score, or an empty list when no piece could
                be scored.

        Raises:
            ValueError: If every supported piece is empty for the response verdict.
        """
        pieces = self._get_supported_pieces(message)
        if not pieces:
            return []
        if self._label is NemotronSafetyGuardLabel.RESPONSE_SAFETY:
            pieces = [piece for piece in pieces if piece.converted_value.strip()]
            if not pieces:
                raise ValueError(_EMPTY_RESPONSE_MESSAGE)
        # Filter before starting any piece tasks, without modifying the stored message.
        scoring_message = Message(message_pieces=pieces)
        resolved_objective = objective or (expectation.objective if expectation else None)
        token = _RESOLVED_USER_PROMPT.set(
            await self._resolve_user_prompt_async(pieces[0], objective=resolved_objective)
        )
        try:
            return await super()._score_async(scoring_message, objective=objective, expectation=expectation)
        finally:
            _RESOLVED_USER_PROMPT.reset(token)


def _resolve_prompt_template(*, prompt_template: SeedPrompt | str | None) -> SeedPrompt:
    if prompt_template is None:
        resolved = SeedPrompt.from_yaml_file(_DEFAULT_PROMPT_PATH)
    elif isinstance(prompt_template, SeedPrompt):
        resolved = prompt_template
    elif isinstance(prompt_template, str):
        resolved = SeedPrompt(value=prompt_template, data_type="text", is_jinja_template=True)
    else:
        raise TypeError("prompt_template must be a SeedPrompt, str, or None.")

    # Render once here so a template missing a parameter fails at construction rather than on the
    # first scored message.
    render_nemotron_safety_guard_prompt(
        response="validation response",
        user_prompt="validation prompt",
        prompt_template=resolved,
    )
    return resolved
