# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Parser for Mistral Shieldstral guard responses.

Shieldstral answers one plain-language policy question per request, and its system prompt
limits the answer to ``yes`` or ``no``. ``yes`` means the content meets the condition the
question describes, such as "Does this content promote physical violence?".

The parser returns the dictionary consumed by ``CallableResponseHandler``. Pair that handler
with ``ShieldstralScorer`` to compose a Shieldstral scorer.

Reference: Mistral AI, "Shieldstral" (2026), https://arxiv.org/abs/2607.25857.

Official model card: https://huggingface.co/mistralai/Shieldstral-1.0-3B
"""

from __future__ import annotations

from typing import Any

from pyrit.exceptions import InvalidJsonException

_MEETS_CONDITION = "yes"
_DOES_NOT_MEET_CONDITION = "no"
# The model card accepts the answer bare, with a full stop, or quoted.
_ANSWER_PUNCTUATION = ".,:;!?\"'*`"


def parse_shieldstral_response(text: str, *, query_name: str | None = None, scope: str | None = None) -> dict[str, Any]:
    """
    Parse a Shieldstral response for ``CallableResponseHandler``.

    Maps a leading ``yes`` (the content meets the query's condition) to ``score_value="True"``
    and a leading ``no`` to ``score_value="False"``, ignoring case and the punctuation the model
    card allows around the answer.

    Args:
        text (str): The raw text response from a Shieldstral endpoint.
        query_name (str | None): A short name for the query the response answered. It namespaces
            the metadata keys, so composing one scorer per query keeps every child's result.
            Defaults to None.
        scope (str | None): Extra namespace for the metadata keys, used by the scorer to carry
            the scored piece's id, so several scored pieces of one message keep their own keys
            when the aggregate merges child metadata. Defaults to None.

    Returns:
        dict[str, Any]: A score dictionary containing ``score_value``, ``rationale``,
            ``description``, and ``metadata``.

    Raises:
        InvalidJsonException: If the response is empty or does not begin with yes or no.
            The LLM scoring helper retries responses that raise this exception.
    """
    raw = text.strip()
    if not raw:
        raise InvalidJsonException(message="Shieldstral returned an empty response.")

    parts = raw.split(maxsplit=1)
    leading_token = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""
    answer = leading_token.strip(_ANSWER_PUNCTUATION).lower()

    if answer not in (_MEETS_CONDITION, _DOES_NOT_MEET_CONDITION):
        raise InvalidJsonException(
            message=f"Shieldstral response did not begin with 'yes' or 'no' (got {leading_token!r}): {raw}"
        )

    meets = answer == _MEETS_CONDITION
    answered = f" to query '{query_name}'" if query_name else ""
    state = "meets" if meets else "does not meet"
    rationale = (
        f"Shieldstral answered '{answer}'{answered}: {remainder}"
        if remainder
        else f"Shieldstral answered '{answer}'{answered}: the content {state} the query's condition."
    )
    prefix = _metadata_prefix(query_name=query_name, scope=scope)

    return {
        "score_value": str(meets),
        "description": f"Shieldstral judged that the content {state} the query's condition.",
        "rationale": rationale,
        # Keys are namespaced per query and per scored piece so that composing one scorer per
        # query, or scoring several pieces, keeps every result. The aggregate merges child
        # metadata last-writer-wins, so shared keys would leave it naming the wrong query.
        "metadata": {
            f"{prefix}_answer": answer,
            f"{prefix}_output": raw,
        },
    }


def _metadata_prefix(*, query_name: str | None, scope: str | None = None) -> str:
    """
    Build the metadata key prefix for one query, optionally scoped to one message piece.

    Args:
        query_name (str | None): The query's short name, used as-is apart from case folding.
        scope (str | None): Extra namespace, such as the scored piece's id. Defaults to None.

    Returns:
        str: A key prefix that does not collide with another query name or another piece.
    """
    prefix = f"shieldstral_{query_name.casefold()}" if query_name else "shieldstral"
    return f"{prefix}_{scope}" if scope else prefix
