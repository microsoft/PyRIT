# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

from pyrit.converter._decision_request import parse_decision_request, serialize_decision_request
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

DecisionOptionOrder = Literal["reverse", "shuffle"]


class DecisionOptionOrderConverter(Converter):
    """
    Reorder the options of the ``choice`` questions in a decision model request.

    Option order should not change a decision model's answer, so a changed answer is a flip.
    ``reverse`` reverses each option list. ``shuffle`` draws a different order for each list,
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

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, order: DecisionOptionOrder = "reverse", seed: int | None = None) -> None:
        """
        Initialize the decision option order converter.

        Args:
            order (DecisionOptionOrder): "reverse" or "shuffle". Defaults to "reverse".
            seed (int | None): Seed for reproducible shuffles. Defaults to None.

        Raises:
            ValueError: If ``order`` is not "reverse" or "shuffle".
        """
        super().__init__()
        if order not in ("reverse", "shuffle"):
            raise ValueError(f"order must be 'reverse' or 'shuffle', got {order!r}.")
        self._order = order
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with the reordering mode and seed.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"order": self._order, "seed": self._seed})

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
        if input_type != "text":
            raise ValueError(f"DecisionOptionOrderConverter only supports text input, got {input_type}")

        request = parse_decision_request(prompt=prompt, converter_name=type(self).__name__)
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
        return ConverterResult(output_text=serialize_decision_request(request), output_type="text")

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
        Reorder options in the whole request, leaving any state markers for state converters.

        Markers select text inside the state, which this converter never changes, so they pass
        through unchanged and ``keep_tokens`` has no effect.

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
        if self._order == "reverse":
            return names[::-1]
        rng = self._get_random_generator(stream="option-order")
        shuffled = names[:]
        # Every list here has at least two distinct names, so a different order always exists.
        while shuffled == names:
            rng.shuffle(shuffled)
        return shuffled
