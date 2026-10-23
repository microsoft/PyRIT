# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any


def parse_decision_request(*, prompt: str, converter_name: str) -> dict[str, Any]:
    """
    Parse a decision request: a JSON object with ``state`` and ``questions``.

    Args:
        prompt (str): The decision request JSON.
        converter_name (str): The converter name used in error messages.

    Returns:
        dict[str, Any]: The parsed request.

    Raises:
        ValueError: If the prompt is not a JSON object with ``state`` and ``questions``.
    """
    try:
        request = json.loads(prompt)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{converter_name} requires a decision request JSON object.") from exc
    if not isinstance(request, dict) or not {"state", "questions"} <= request.keys():
        raise ValueError(f"{converter_name} requires a decision request with state and questions.")
    return request


def serialize_decision_request(request: dict[str, Any]) -> str:
    """
    Serialize a decision request.

    Args:
        request (dict[str, Any]): The decision request.

    Returns:
        str: The request JSON. Non-ASCII text, such as homoglyphs from a converter, stays readable.
    """
    return json.dumps(request, ensure_ascii=False)
