# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any, ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.score.true_false.wildguard_parser import WildGuardLabel, parse_wildguard_response

_DEFAULT_WILDGUARD_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "wildguard" / "wildguard_prompt.yaml"
_PROMPT_PARAMETERS = ("user_prompt", "response")

# Message pieces are scored concurrently with asyncio.gather, and TrueFalseCompositeScorer
# gathers its child scorers too, so several coroutines can reach the memory read at once. The
# read runs in a worker thread, which puts the shared SQLAlchemy session on several threads and
# makes the lookup intermittently return nothing. Shared at module level rather than per
# instance so composed scorers serialize against each other as well.
_MEMORY_READ_LOCK = asyncio.Lock()

_MISSING_USER_PROMPT_MESSAGE = (
    "WildGuard classifies a user prompt and a model response together, so it needs the prompt "
    "that produced the response being scored. Score a piece that follows a user turn in a "
    "stored conversation, or pass user_prompt= to the scorer."
)

_EMPTY_RESPONSE_MESSAGE = (
    "WildGuard was asked to judge an empty response. It answers 'N/A' for the response-side "
    "labels when no response is present, which has no true/false reading. Score a response "
    "with content, or select WildGuardLabel.HARMFUL_REQUEST to judge the prompt instead."
)


def _coerce_label(label: WildGuardLabel | str) -> WildGuardLabel:
    """
    Accept the enum or its serialized value.

    ``ScorerRegistry`` inspects constructor signatures with ``inspect.signature``, which under
    postponed annotations reports the annotation as the string ``"WildGuardLabel"`` rather than
    the enum. It therefore cannot coerce a configured ``"Harmful request"``, and the raw string
    would fail the identity guard and miss the parser's per-label lookup.

    Args:
        label (WildGuardLabel | str): The label, or its value such as ``"Harmful request"``.

    Returns:
        WildGuardLabel: The corresponding enum member.

    Raises:
        ValueError: If the value does not name a label.
    """
    if isinstance(label, WildGuardLabel):
        return label
    normalized = label.strip().casefold()
    for member in WildGuardLabel:
        if normalized in (member.value.casefold(), member.name.casefold()):
            return member
    valid = ", ".join(member.value for member in WildGuardLabel)
    raise ValueError(f"Unknown WildGuard label {label!r}. Expected one of: {valid}.")


def render_wildguard_prompt(
    *,
    response: str,
    user_prompt: str,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a WildGuard classification request for one prompt and response pair.

    Args:
        response (str): The model response being classified.
        user_prompt (str): The user prompt that produced ``response``.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the
            bundled WildGuard template.

    Returns:
        SeedPrompt: The rendered request prompt.
    """
    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_DEFAULT_WILDGUARD_PROMPT_PATH,
        render_params={"user_prompt": user_prompt, "response": response},
        required_parameters=_PROMPT_PARAMETERS,
    )


class WildGuardScorer(TrueFalseScorer):
    """
    Classify a prompt and response pair with the Allen Institute WildGuard classifier.

    WildGuard returns three judgements from a single call: whether the request is harmful,
    whether the response is a refusal, and whether the response is harmful. ``label`` selects
    which one becomes the boolean score; the other two are kept in the score metadata so
    reading them costs no extra request.

    That also means composing several of these under ``TrueFalseCompositeScorer`` is not the
    intended way to read more than one judgement. One scorer already reports all three, so a
    second only repeats the same request, and the two scores would carry the same metadata
    keys.

    The scored message is the model response. The prompt it is judged against is read from the
    preceding turn of the scored conversation, or supplied with ``user_prompt``.
    """

    SCORE_CATEGORY: ClassVar[str] = "wildguard"
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        label: WildGuardLabel | str = WildGuardLabel.HARMFUL_RESPONSE,
        user_prompt: str | None = None,
        prompt_template: SeedPrompt | str | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the WildGuard scorer.

        Args:
            chat_target (PromptTarget): A target serving WildGuard.
            label (WildGuardLabel | str): Which of the three judgements becomes the score
                value, as the enum or its value such as ``"Harmful request"``, which is what a
                serialized configuration supplies. Defaults to
                ``WildGuardLabel.HARMFUL_RESPONSE``.
            user_prompt (str | None): Fixed prompt to classify responses against, which takes
                precedence over the preceding turn of the scored conversation. Defaults to None.
            prompt_template (SeedPrompt | str | None): Custom WildGuard request template.
                Defaults to the bundled template.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If ``label`` does not name one of WildGuard's three judgements.
        """
        label = _coerce_label(label)

        self._prompt_target = chat_target
        self._label = label
        self._user_prompt = user_prompt
        self._prompt_template = _resolve_prompt_template(prompt_template=prompt_template)

        super().__init__(
            validator=validator or self._DEFAULT_VALIDATOR,
            score_aggregator=score_aggregator,
            chat_target=chat_target,
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
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _resolve_user_prompt_async(self, message_piece: MessagePiece) -> str | None:
        """
        Find the user prompt that a scored response is judged against.

        Args:
            message_piece (MessagePiece): The response being scored.

        Returns:
            str | None: The configured prompt, otherwise the preceding user turn of the
                scored conversation, otherwise None.
        """
        if self._user_prompt:
            return self._user_prompt
        if not message_piece.conversation_id or message_piece.sequence < 1:
            return None

        # get_message_pieces is a blocking SQLAlchemy query, so it is offloaded rather than
        # run on the event loop during scoring.
        async with _MEMORY_READ_LOCK:
            conversation = await asyncio.to_thread(
                self._memory.get_message_pieces, conversation_id=message_piece.conversation_id
            )
        # The converted value is what the target actually received. After a converter runs, the
        # original value can be the seed prompt, which the target never saw.
        preceding_turn = [
            piece.converted_value
            for piece in conversation
            if piece.sequence == message_piece.sequence - 1
            and piece.converted_value_data_type == "text"
            and piece.api_role == "user"
        ]
        return "\n".join(preceding_turn) or None

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score one response against the configured WildGuard label.

        Args:
            message_piece (MessagePiece): The model response to classify.
            objective (str | None): Objective retained on the resulting score. It is not
                included in the WildGuard request. Defaults to None.

        Returns:
            list[Score]: A single true/false WildGuard score.

        Raises:
            ValueError: If no user prompt can be found, or if a response-side label was
                selected for an empty response.
        """
        response = message_piece.converted_value
        if not response.strip() and self._label is not WildGuardLabel.HARMFUL_REQUEST:
            # Checked before the request rather than in the parser, because the parser's
            # exceptions drive a retry and resending an empty response cannot change the answer.
            raise ValueError(_EMPTY_RESPONSE_MESSAGE)

        user_prompt = await self._resolve_user_prompt_async(message_piece)
        if not user_prompt:
            raise ValueError(_MISSING_USER_PROMPT_MESSAGE)

        request_prompt = render_wildguard_prompt(
            response=response,
            user_prompt=user_prompt,
            prompt_template=self._prompt_template,
        )
        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=None,
            response_handler=CallableResponseHandler(
                parser=partial(parse_wildguard_response, label=self._label, scope=str(message_piece.id))
            ),
            value=request_prompt.value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            category=self.SCORE_CATEGORY,
            objective=objective,
        )
        return [
            unvalidated_score.to_score(
                score_value=unvalidated_score.raw_score_value,
                score_type="true_false",
            )
        ]

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score every supported piece and record the aggregated verdict.

        Each piece keeps its own labels and raw output under its own keys, so none is lost to
        the last-writer-wins metadata merge. This adds the label-level verdict on top, which
        follows the configured aggregator rather than whichever piece happened to be merged
        last.

        Args:
            message (Message): The message to score.
            objective (str | None): Objective retained on the resulting score. Defaults to None.

        Returns:
            list[Score]: A single aggregated true/false score, or an empty list when no piece
                could be scored.
        """
        scores = await super()._score_async(message, objective=objective)
        if not scores:
            return scores

        aggregate = scores[0]
        aggregate.score_metadata = {
            **(aggregate.score_metadata or {}),
            f"wildguard_{self._label.metadata_key}_verdict": ("yes" if aggregate.get_value() else "no"),
        }
        return scores


def _resolve_prompt_template(*, prompt_template: SeedPrompt | str | None) -> SeedPrompt:
    if prompt_template is None:
        resolved = SeedPrompt.from_yaml_file(_DEFAULT_WILDGUARD_PROMPT_PATH)
    elif isinstance(prompt_template, SeedPrompt):
        resolved = prompt_template
    elif isinstance(prompt_template, str):
        resolved = SeedPrompt(value=prompt_template, data_type="text", is_jinja_template=True)
    else:
        raise TypeError("prompt_template must be a SeedPrompt, str, or None.")

    # Render once here so a template missing a parameter fails at construction rather than on
    # the first scored message.
    render_wildguard_prompt(
        response="validation response",
        user_prompt="validation prompt",
        prompt_template=resolved,
    )
    return resolved
