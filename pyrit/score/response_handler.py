# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import json
from abc import abstractmethod
from typing import TYPE_CHECKING

from pyrit.exceptions import InvalidJsonException, remove_markdown_json
from pyrit.models import UnvalidatedScore

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from pyrit.models import ComponentIdentifier, JsonSchemaDefinition


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
    def response_schema(self) -> JsonSchemaDefinition | None:
        """
        The JSON schema, if any, describing the response the target should return.

        The LLM round-trip forwards this to the scoring target so targets that natively support
        structured output can enforce it; targets that cannot have it omitted by normalization.
        Handlers that do not constrain the response shape return None (the default).
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
    that the parsed score value is numeric.
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
                parsable as a float and raises ``InvalidJsonException`` otherwise. Defaults to False.
        """
        self._score_value_output_key = score_value_output_key
        self._rationale_output_key = rationale_output_key
        self._description_output_key = description_output_key
        self._metadata_output_key = metadata_output_key
        self._category_output_key = category_output_key
        self._response_schema = response_schema
        self._numeric_value = numeric_value

    @property
    def response_schema(self) -> JsonSchemaDefinition | None:
        """The configured JSON schema to forward to the scoring target, if any."""
        return self._response_schema

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
            InvalidJsonException: If the response is not valid JSON, is missing a required key, or
                (when this handler is numeric) the score value is not parsable as a float.
        """
        response_json = remove_markdown_json(response_text)
        try:
            parsed_response = json.loads(response_json)
            category_response = parsed_response.get(self._category_output_key)

            if category_response and category:
                raise ValueError("Category is present in the response and an argument")

            # Validate and normalize category to a list of strings
            cat_val = category_response if category_response is not None else category
            normalized_category: list[str] | None
            if cat_val is None:
                normalized_category = None
            elif isinstance(cat_val, str):
                normalized_category = [cat_val]
            elif isinstance(cat_val, list):
                if not all(isinstance(x, str) for x in cat_val):
                    raise ValueError("'category' must be a string or a list of strings")
                normalized_category = cat_val  # type: ignore[ty:invalid-assignment]
            else:
                # JSON must yield either a string or a list of strings
                raise ValueError("'category' must be a string or a list of strings")

            # Normalize metadata to a dictionary with string keys and string/int/float values
            raw_md = parsed_response.get(self._metadata_output_key)
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

            score = UnvalidatedScore(
                raw_score_value=str(parsed_response[self._score_value_output_key]),
                score_value_description=parsed_response.get(self._description_output_key),
                score_category=normalized_category,
                score_rationale=parsed_response[self._rationale_output_key],
                scorer_class_identifier=scorer_identifier,
                score_metadata=normalized_md,
                message_piece_id=scored_prompt_id,
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
                float(score.raw_score_value)
            except ValueError:
                raise InvalidJsonException(
                    message=f"Invalid JSON response, score_value should be a float not this: {score.raw_score_value}"
                ) from None

        return score
