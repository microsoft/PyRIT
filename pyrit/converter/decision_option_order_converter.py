# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
from enum import Enum
from typing import Any

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _parse_decision_request(prompt: str) -> dict[str, Any]:
    """
    Parse a decision request: a JSON object with ``state`` and ``questions``.

    Args:
        prompt (str): The decision request JSON.

    Returns:
        dict[str, Any]: The parsed request.

    Raises:
        ValueError: If the prompt is not a JSON object with ``state`` and ``questions``.
    """
    try:
        request = json.loads(prompt)
    except json.JSONDecodeError as exc:
        raise ValueError("DecisionOptionOrderConverter requires a decision request JSON object.") from exc
    if not isinstance(request, dict) or not {"state", "questions"} <= request.keys():
        raise ValueError("DecisionOptionOrderConverter requires a decision request with state and questions.")
    return request


def _serialize_decision_request(request: dict[str, Any]) -> str:
    """
    Serialize a decision request.

    Args:
        request (dict[str, Any]): The decision request.

    Returns:
        str: The request JSON. Non-ASCII text, such as homoglyphs from a converter, stays readable.
    """
    serialized = json.dumps(request, ensure_ascii=False)
    # A lone surrogate is valid as a JSON escape but cannot be encoded as UTF-8 when stored or sent.
    return _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", serialized)


class DecisionOptionOrderConverter(Converter):
    """
    Reorder the options of the ``choice`` questions in a decision model request.

    Option order should not change a decision model's answer, so a changed answer shows order sensitivity.
    ``Order.REVERSE`` reverses each option list. ``Order.SHUFFLE`` draws a different order for each list,
    reproducible with ``seed``. Option names and descriptions are unchanged, and so are the
    state and the other question types: ``score`` levels are ordinal and keep their order.

    Example:
        >>> from pyrit.converter import DecisionOptionOrderConverter
        >>>
        >>> converter = DecisionOptionOrderConverter()
        >>> result = await converter.convert_async(
        ...     prompt='{"state": "...", "questions": {"route": {"type": "choice", '
        ...     '"criteria": {"billing": "...", "refund": "..."}}}}'
        ... )
        >>> # Result: '{"state": "...", "questions": {"route": {"type": "choice", "criteria": {"refund": "...", ...}}}}'
    """

    class Order(Enum):
        """How the options of each ``choice`` question are reordered."""

        REVERSE = "reverse"  #: Reverse each option list.
        SHUFFLE = "shuffle"  #: Draw a different order for each list, reproducible with ``seed``.

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, order: "DecisionOptionOrderConverter.Order" = Order.REVERSE, seed: int | None = None) -> None:
        """
        Initialize the decision option order converter.

        Args:
            order (DecisionOptionOrderConverter.Order): How to reorder the options. Defaults to
                ``Order.REVERSE``.
            seed (int | None): Seed for reproducible shuffles. Defaults to None.

        Raises:
            TypeError: If ``order`` is not a ``DecisionOptionOrderConverter.Order``.
        """
        super().__init__()
        if not isinstance(order, DecisionOptionOrderConverter.Order):
            raise TypeError(f"order must be a DecisionOptionOrderConverter.Order, got {order!r}.")
        self._order = order
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with the reordering mode and seed.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"order": self._order.value, "seed": self._seed})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Reorder the options of every ``choice`` question in the request.

        Args:
            prompt (str): The decision request as a JSON object with ``state`` and ``questions``.
            input_type (PromptDataType): The type of input data. Must be "text".

        Returns:
            ConverterResult: The decision request with reordered options.

        Raises:
            ValueError: If the input type is not "text".
            ValueError: If the prompt is not a decision request.
            ValueError: If no ``choice`` question has at least two options to reorder.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"DecisionOptionOrderConverter only supports text input, got {input_type}")

        request = _parse_decision_request(prompt)
        questions = request["questions"]
        reordered = 0
        if isinstance(questions, dict):
            for question in questions.values():
                if not isinstance(question, dict) or question.get("type") != "choice":
                    continue
                criteria = question.get("criteria")
                if not isinstance(criteria, dict) or len(criteria) < 2:
                    continue
                names = self._reorder(list(criteria))
                question["criteria"] = {name: criteria[name] for name in names}
                reordered += 1

        if not reordered:
            raise ValueError(
                "DecisionOptionOrderConverter requires at least one choice question with two or more options."
            )
        return ConverterResult(output_text=_serialize_decision_request(request), output_type="text")

    async def convert_tokens_async(
        self,
        *,
        prompt: str,
        input_type: PromptDataType = "text",
        start_token: str = "⟪",
        end_token: str = "⟫",
        keep_tokens: bool = False,
    ) -> ConverterResult:
        """
        Reorder options in the whole request, leaving any markers for other text converters in the stack.

        Markers select text inside the state, which this converter never changes, so they pass
        through unchanged for a later converter to use, and ``keep_tokens`` has no effect.

        Args:
            prompt (str): The decision request as a JSON object with ``state`` and ``questions``.
            input_type (PromptDataType): The type of input data. Must be "text".
            start_token (str): Unused; markers are left in place. Defaults to "⟪".
            end_token (str): Unused; markers are left in place. Defaults to "⟫".
            keep_tokens (bool): Unused; markers are left in place. Defaults to False.

        Returns:
            ConverterResult: The decision request with reordered options.
        """
        return await self.convert_async(prompt=prompt, input_type=input_type)

    def _reorder(self, names: list[str]) -> list[str]:
        """
        Return the option names in their new order.

        Args:
            names (list[str]): The option names in their current order, at least two.

        Returns:
            list[str]: The reordered names, never in the current order.
        """
        if self._order is DecisionOptionOrderConverter.Order.REVERSE:
            return names[::-1]
        rng = self._get_random_generator(stream="option-order")
        shuffled = names[:]
        # Every list here has at least two distinct names, so a different order always exists.
        while shuffled == names:
            rng.shuffle(shuffled)
        return shuffled
