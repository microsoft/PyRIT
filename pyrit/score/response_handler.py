# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import inspect
import json
import math
from abc import abstractmethod
from collections.abc import Sequence
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from pyrit.exceptions import InvalidJsonException, remove_markdown_json
from pyrit.models import JsonResponseConfig, UnvalidatedScore

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

    from pyrit.models import ComponentIdentifier, JsonSchemaDefinition

_UNSTABLE_REPLAY_VALUE = object()


def _stable_replay_value(value: Any) -> Any:
    """
    Normalize supported handler configuration into a stable JSON value.

    Returns:
        Any: The normalized value or an internal unstable-value sentinel.
    """
    if isinstance(value, Enum):
        return {
            "enum_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _stable_replay_value(value.value),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, BaseModel):
        return {
            "model_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.model_dump(mode="json"),
        }
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            return _UNSTABLE_REPLAY_VALUE
        normalized = {key: _stable_replay_value(item) for key, item in value.items()}
        return _UNSTABLE_REPLAY_VALUE if _UNSTABLE_REPLAY_VALUE in normalized.values() else normalized
    if isinstance(value, (list, tuple)):
        normalized_items = [_stable_replay_value(item) for item in value]
        if _UNSTABLE_REPLAY_VALUE in normalized_items:
            return _UNSTABLE_REPLAY_VALUE
        return {"sequence_type": type(value).__name__, "items": normalized_items}
    if isinstance(value, (set, frozenset)):
        normalized_items = [_stable_replay_value(item) for item in value]
        if _UNSTABLE_REPLAY_VALUE in normalized_items:
            return _UNSTABLE_REPLAY_VALUE
        return {
            "set_type": type(value).__name__,
            "items": sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True)),
        }
    return _UNSTABLE_REPLAY_VALUE


def _callable_replay_identifier(parser: Callable[[str], dict[str, Any]]) -> dict[str, Any] | None:
    """
    Build a stable identity for a top-level parser or partial of one.

    Returns:
        dict[str, Any] | None: Stable callable configuration, or None when unavailable.
    """
    parser_args: tuple[Any, ...] = ()
    parser_keywords: dict[str, Any] = {}
    function = parser
    if isinstance(parser, partial):
        function = parser.func
        parser_args = parser.args
        parser_keywords = parser.keywords or {}
    if not inspect.isfunction(function) or function.__name__ == "<lambda>" or "<locals>" in function.__qualname__:
        return None
    normalized_args = _stable_replay_value(parser_args)
    normalized_keywords = _stable_replay_value(parser_keywords)
    if _UNSTABLE_REPLAY_VALUE in (normalized_args, normalized_keywords):
        return None
    return {
        "function": f"{function.__module__}.{function.__qualname__}",
        "args": normalized_args,
        "keywords": normalized_keywords,
    }


def _build_unvalidated_score(
    *,
    parsed_response: dict[str, Any],
    score_value_output_key: str,
    rationale_output_key: str,
    description_output_key: str,
    metadata_output_key: str,
    category_output_key: str,
    scorer_identifier: ComponentIdentifier,
    scored_prompt_id: str | uuid.UUID,
    category: Sequence[str] | str | None,
    objective: str | None,
) -> UnvalidatedScore:
    category_response = parsed_response.get(category_output_key)

    if category_response is not None and category is not None:
        raise ValueError("Category is present in the response and an argument")

    # Validate and normalize category to a list of strings
    cat_val = category_response if category_response is not None else category
    normalized_category: list[str] | None
    if cat_val is None:
        normalized_category = None
    elif isinstance(cat_val, str):
        normalized_category = [cat_val]
    elif isinstance(cat_val, Sequence):
        if not all(isinstance(x, str) for x in cat_val):
            if category_response is not None:
                raise InvalidJsonException(message="'category' must be a string or a sequence of strings")
            raise ValueError("'category' must be a string or a sequence of strings")
        normalized_category = list(cat_val)
    else:
        if category_response is not None:
            raise InvalidJsonException(message="'category' must be a string or a sequence of strings")
        raise ValueError("'category' must be a string or a sequence of strings")

    # Normalize metadata to a dictionary with string keys and string/int/float values
    raw_md = parsed_response.get(metadata_output_key)
    normalized_md: dict[str, str | int | float] | None
    if raw_md is None:
        normalized_md = None
    elif isinstance(raw_md, dict):
        # Coerce keys to str and filter to str/int/float values only
        normalized_md = {str(k): v for k, v in raw_md.items() if isinstance(v, (str, int, float))}
        # If dictionary becomes empty after filtering, keep as empty dict
    elif isinstance(raw_md, (str, int, float)):
        # Wrap primitive metadata into a namespaced field
        normalized_md = {"metadata": raw_md}
    else:
        # Unrecognized metadata shape; drop to avoid downstream errors
        normalized_md = None

    return UnvalidatedScore(
        raw_score_value=str(parsed_response[score_value_output_key]),
        score_value_description=parsed_response.get(description_output_key, ""),
        score_category=normalized_category,
        score_rationale=parsed_response[rationale_output_key],
        scorer_class_identifier=scorer_identifier,
        score_metadata=normalized_md,
        message_piece_id=scored_prompt_id,
        objective=objective,
    )


class ResponseHandler(abc.ABC):
    """
    Owns the response contract for a scoring target.

    A ResponseHandler owns two things and nothing else: the JSON schema (if any) the scoring
    target should honor, and turning the raw text the target returns into an ``UnvalidatedScore``
    (including any value validation, such as requiring a numeric score). It does not perform the
    LLM round-trip, build the system prompt, or decide how the resulting score branches. Different
    handlers implement different wire formats (e.g. JSON today).
    """

    @property
    def json_response_config(self) -> JsonResponseConfig:
        """
        The canonical JSON-response request this handler asks the scoring target for.

        Format and schema are one coupled unit: the LLM round-trip serializes this onto the
        request metadata via ``to_metadata``, and targets that natively support structured output
        enforce the schema (others have it omitted by normalization). The default is disabled,
        imposing no wire format so targets that emit plain text are not forced into a format they
        cannot honor. Handlers that require JSON override this.
        """
        return JsonResponseConfig(enabled=False)

    def _get_replay_identifier(self) -> dict[str, Any] | None:
        """Return a replay contract only when the concrete handler explicitly declares one."""
        if "_replay_identifier" not in type(self).__dict__:
            return None
        return self._replay_identifier()

    def _replay_identifier(self) -> dict[str, Any] | None:
        """
        Return stable parser configuration for observation replay.

        Every concrete subclass must override this method to enable replay, even when
        inheriting a parser or another replay-enabled handler. The returned JSON-serializable
        configuration must include a behavior version and all state that affects parsing.
        Override this method and extend ``super()._replay_identifier()`` when adding parser
        configuration; a class name alone does not identify instance-specific behavior.

        Returns:
            dict[str, Any] | None: Stable parser configuration, or None when replay is unsafe.
        """
        return None

    @abstractmethod
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
        Parse raw target output into an ``UnvalidatedScore``.

        Args:
            response_text (str): The raw text returned by the scoring target.
            scorer_identifier (ComponentIdentifier): Identifier of the scorer that produced the
                request, stored on the resulting score.
            scored_prompt_id (str | uuid.UUID): The ID of the message piece being scored.
            category (Sequence[str] | str | None): The category of the score. May instead be parsed
                from the response; supplying both is an error. Defaults to None.
            objective (str | None): The objective associated with the score, used for
                contextualizing the result. Defaults to None.

        Returns:
            UnvalidatedScore: The parsed score, whose ``raw_score_value`` still needs to be
                normalized and validated by the caller.
        """
        ...


class JsonSchemaResponseHandler(ResponseHandler):
    """
    Default ResponseHandler that parses JSON scoring responses.

    Reproduces PyRIT's historical scoring-response parsing: strip any markdown code fences,
    ``json.loads`` the text, then read the score value, rationale, optional description,
    category, and metadata from configurable keys. It also owns the response contract: the
    optional JSON schema handed to the target, and (when ``numeric_value`` is set) validating
    that the parsed score value is finite and numeric.
    """

    def __init__(
        self,
        *,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
        response_schema: JsonSchemaDefinition | None = None,
        numeric_value: bool = False,
    ) -> None:
        """
        Initialize the handler with the JSON keys to read from the response.

        Args:
            score_value_output_key (str): Key holding the score value. Defaults to "score_value".
            rationale_output_key (str): Key holding the rationale. Defaults to "rationale".
            description_output_key (str): Key holding the description. Defaults to "description".
            metadata_output_key (str): Key holding the metadata. Defaults to "metadata".
            category_output_key (str): Key holding the category. Defaults to "category".
            response_schema (JsonSchemaDefinition | None): Optional JSON schema the scoring target
                should honor. Exposed via ``response_schema`` and forwarded to the target by the
                LLM round-trip. Defaults to None.
            numeric_value (bool): When True, ``parse`` requires the parsed score value to be
                parsable as a finite float and raises ``InvalidJsonException`` otherwise. Defaults
                to False.
        """
        self._score_value_output_key = score_value_output_key
        self._rationale_output_key = rationale_output_key
        self._description_output_key = description_output_key
        self._metadata_output_key = metadata_output_key
        self._category_output_key = category_output_key
        self._response_schema = response_schema
        self._numeric_value = numeric_value

    @property
    def json_response_config(self) -> JsonResponseConfig:
        """The JSON-response request: always JSON, carrying the optional configured schema."""
        return JsonResponseConfig(enabled=True, json_schema=self._response_schema)

    def _replay_identifier(self) -> dict[str, Any]:
        """Return all configuration that changes JSON parsing."""
        return {
            "handler": f"{type(self).__module__}.{type(self).__qualname__}",
            "version": 1,
            "score_value_output_key": self._score_value_output_key,
            "rationale_output_key": self._rationale_output_key,
            "description_output_key": self._description_output_key,
            "metadata_output_key": self._metadata_output_key,
            "category_output_key": self._category_output_key,
            "response_schema": self._response_schema,
            "numeric_value": self._numeric_value,
        }

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
        Parse a JSON scoring response into an ``UnvalidatedScore``.

        Args:
            response_text (str): The raw text returned by the scoring target.
            scorer_identifier (ComponentIdentifier): Identifier of the scorer that produced the
                request, stored on the resulting score.
            scored_prompt_id (str | uuid.UUID): The ID of the message piece being scored.
            category (Sequence[str] | str | None): The category of the score. May instead be parsed
                from the response; supplying both is an error. Defaults to None.
            objective (str | None): The objective associated with the score, used for
                contextualizing the result. Defaults to None.

        Returns:
            UnvalidatedScore: The parsed score, whose ``raw_score_value`` still needs to be
                normalized and validated by the caller.

        Raises:
            ValueError: If a category is present in both the response and the argument, or the
                parsed category is not a string or a list of strings.
            InvalidJsonException: If the response is invalid JSON, is not a top-level JSON object,
                is missing a required key, or (when this handler is numeric) the score value is not
                parsable as a finite float.
        """
        response_json = remove_markdown_json(response_text)
        try:
            parsed_response = json.loads(response_json)
            if not isinstance(parsed_response, dict):
                raise InvalidJsonException(
                    message=f"Invalid JSON response, expected a top-level object: {response_json}"
                )
            score = _build_unvalidated_score(
                parsed_response=parsed_response,
                score_value_output_key=self._score_value_output_key,
                rationale_output_key=self._rationale_output_key,
                description_output_key=self._description_output_key,
                metadata_output_key=self._metadata_output_key,
                category_output_key=self._category_output_key,
                scorer_identifier=scorer_identifier,
                scored_prompt_id=scored_prompt_id,
                category=category,
                objective=objective,
            )

        except json.JSONDecodeError:
            raise InvalidJsonException(message=f"Invalid JSON response: {response_json}") from None

        except KeyError:
            raise InvalidJsonException(message=f"Invalid JSON response, missing Key: {response_json}") from None

        if self._numeric_value:
            try:
                # A numeric handler requires the score value to be parsable as a float; a
                # well-formed-but-non-numeric value is treated as an invalid response.
                parsed_value = float(score.raw_score_value)
            except ValueError:
                raise InvalidJsonException(
                    message=f"Invalid JSON response, score_value should be a float not this: {score.raw_score_value}"
                ) from None
            if not math.isfinite(parsed_value):
                raise InvalidJsonException(
                    message=f"Invalid JSON response, score_value must be a finite float: {score.raw_score_value}"
                )

        return score


class TrueFalseResponseHandler(ResponseHandler):
    """Response-handler decorator that enforces the true/false score domain."""

    def __init__(self, *, response_handler: ResponseHandler) -> None:
        """
        Initialize the decorator.

        Args:
            response_handler (ResponseHandler): Handler that parses the target's wire format.
        """
        self._response_handler = response_handler

    @property
    def json_response_config(self) -> JsonResponseConfig:
        """The wrapped handler's JSON-response request."""
        return self._response_handler.json_response_config

    def _replay_identifier(self) -> dict[str, Any] | None:
        """Return the wrapped parser identity with the true/false constraint."""
        wrapped = self._response_handler._get_replay_identifier()
        if wrapped is None:
            return None
        return {
            "handler": f"{type(self).__module__}.{type(self).__qualname__}",
            "version": 1,
            "wrapped": wrapped,
        }

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
        Parse a response and require a true/false score value.

        Returns:
            UnvalidatedScore: The parsed score with a normalized true/false value.

        Raises:
            InvalidJsonException: If the parsed value is outside the true/false domain.
        """
        score = self._response_handler.parse(
            response_text=response_text,
            scorer_identifier=scorer_identifier,
            scored_prompt_id=scored_prompt_id,
            category=category,
            objective=objective,
        )

        # Strip surrounding whitespace before comparing: a judge that returns
        # "true\n" or " false" is giving a valid verdict, and should not be
        # rejected as out-of-domain over incidental whitespace.
        normalized_value = score.raw_score_value.strip().lower()
        if normalized_value not in {"true", "false"}:
            raise InvalidJsonException(
                message=f"True/false score_value must be 'true' or 'false', not {score.raw_score_value!r}."
            )

        score.raw_score_value = normalized_value
        return score


class CallableResponseHandler(ResponseHandler):
    """
    ResponseHandler that delegates parsing to a user-supplied callable.

    The escape hatch for scoring targets whose raw output is not PyRIT's default JSON scoring
    shape (for example a safety classifier that emits ``safe`` or ``unsafe\\nS1,S2``). The
    supplied ``parser`` maps the raw target text to a score dictionary
    (``score_value``/``rationale`` plus optional ``description``/``category``/``metadata``); this
    handler then assembles the ``UnvalidatedScore``. A missing required key raises
    ``InvalidJsonException`` so the standard JSON retry still applies. It intentionally imposes no
    ``response_format`` on the request so classifier targets remain free to return plain text.
    """

    def __init__(
        self,
        *,
        parser: Callable[[str], dict[str, Any]],
        parser_fingerprint: str | None = None,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
    ) -> None:
        """
        Initialize the handler with the parser callable and the keys to read from its output.

        Args:
            parser (Callable[[str], dict[str, Any]]): Maps the raw target text to a score
                dictionary. It may raise ``InvalidJsonException`` to trigger a retry.
            parser_fingerprint (str | None): Explicit versioned identity for parser behavior.
                Replay is disabled when omitted. Defaults to None.
            score_value_output_key (str): Key holding the score value. Defaults to "score_value".
            rationale_output_key (str): Key holding the rationale. Defaults to "rationale".
            description_output_key (str): Key holding the description. Defaults to "description".
            metadata_output_key (str): Key holding the metadata. Defaults to "metadata".
            category_output_key (str): Key holding the category. Defaults to "category".
        """
        self._parser = parser
        self._parser_fingerprint = parser_fingerprint
        self._score_value_output_key = score_value_output_key
        self._rationale_output_key = rationale_output_key
        self._description_output_key = description_output_key
        self._metadata_output_key = metadata_output_key
        self._category_output_key = category_output_key

    def _replay_identifier(self) -> dict[str, Any] | None:
        """Return stable callable and output-key configuration when available."""
        if not self._parser_fingerprint:
            return None
        parser = _callable_replay_identifier(self._parser)
        if parser is None:
            return None
        return {
            "handler": f"{type(self).__module__}.{type(self).__qualname__}",
            "version": 1,
            "parser_fingerprint": self._parser_fingerprint,
            "parser": parser,
            "score_value_output_key": self._score_value_output_key,
            "rationale_output_key": self._rationale_output_key,
            "description_output_key": self._description_output_key,
            "metadata_output_key": self._metadata_output_key,
            "category_output_key": self._category_output_key,
        }

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
        Parse raw target output into an ``UnvalidatedScore`` via the wrapped callable.

        Args:
            response_text (str): The raw text returned by the scoring target.
            scorer_identifier (ComponentIdentifier): Identifier of the scorer that produced the
                request, stored on the resulting score.
            scored_prompt_id (str | uuid.UUID): The ID of the message piece being scored.
            category (Sequence[str] | str | None): The category of the score. May instead be parsed
                from the response; supplying both is an error. Defaults to None.
            objective (str | None): The objective associated with the score, used for
                contextualizing the result. Defaults to None.

        Returns:
            UnvalidatedScore: The parsed score, whose ``raw_score_value`` still needs to be
                normalized and validated by the caller.

        Raises:
            ValueError: If a category is present in both the response and the argument.
            InvalidJsonException: If the parser raises it, fails, or its output is missing a
                required key.
        """
        try:
            parsed_response = self._parser(response_text)
        except InvalidJsonException:
            raise
        except Exception as ex:
            raise InvalidJsonException(message=f"Response parser failed on: {response_text}") from ex

        try:
            return _build_unvalidated_score(
                parsed_response=parsed_response,
                score_value_output_key=self._score_value_output_key,
                rationale_output_key=self._rationale_output_key,
                description_output_key=self._description_output_key,
                metadata_output_key=self._metadata_output_key,
                category_output_key=self._category_output_key,
                scorer_identifier=scorer_identifier,
                scored_prompt_id=scored_prompt_id,
                category=category,
                objective=objective,
            )
        except KeyError:
            raise InvalidJsonException(message=f"Response missing required key: {parsed_response}") from None
