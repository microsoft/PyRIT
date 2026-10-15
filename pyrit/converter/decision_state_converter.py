# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Awaitable, Callable

from pyrit.converter._decision_request import parse_decision_request, serialize_decision_request
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class DecisionStateConverter(Converter):
    """
    Apply another converter to the ``state`` of a decision model request.

    Decision targets such as ``LiteLLMDecisionTarget`` expect a JSON object with ``state``
    and ``questions``. Converting the whole request would also rewrite the JSON syntax and
    the questions. This wrapper parses the request, converts only the state text, and
    serializes the request again, so text converters can perturb what a decision model judges
    while its questions stay fixed.

    Example:
        >>> from pyrit.converter import Base64Converter, DecisionStateConverter
        >>>
        >>> converter = DecisionStateConverter(sub_converter=Base64Converter())
        >>> result = await converter.convert_async(
        ...     prompt='{"state": "hello", "questions": {"q": {"type": "noul", "instructions": "Is it a greeting?"}}}'
        ... )
        >>> # Result: '{"state": "aGVsbG8=", "questions": {"q": {...}}}'
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, sub_converter: Converter, state_field: str | None = None) -> None:
        """
        Initialize the decision state converter.

        Args:
            sub_converter (Converter): The text converter to apply to the state.
            state_field (str | None): The field to convert when the state is a JSON object.
                Leave unset when the state is a string. Defaults to None.

        Raises:
            ValueError: If the wrapped converter does not support text input and output.
            ValueError: If ``state_field`` is empty or contains only whitespace.
        """
        super().__init__()

        if not sub_converter.input_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text input")
        if not sub_converter.output_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text output")
        if state_field is not None and not state_field.strip():
            raise ValueError("state_field must be a nonempty string when provided.")

        self._sub_converter = sub_converter
        self._state_field = state_field

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with the converted state field and the wrapped converter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={"state_field": self._state_field},
            sub_converter=self._sub_converter.get_identifier(),
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the state of a decision request with the wrapped converter.

        Args:
            prompt (str): The decision request as a JSON object with ``state`` and ``questions``.
            input_type (PromptDataType): The type of input data. Must be "text".

        Returns:
            ConverterResult: The decision request with its state converted.

        Raises:
            ValueError: If the input type is not "text".
            ValueError: If the prompt is not a decision request or its state is not convertible text.
            ValueError: If the wrapped converter does not return text.
        """
        if input_type != "text":
            raise ValueError(f"DecisionStateConverter only supports text input, got {input_type}")

        async def convert_state_async(state: str) -> ConverterResult:
            return await self._sub_converter.convert_async(prompt=state, input_type="text")

        return await self._convert_request_async(prompt=prompt, convert_state_async=convert_state_async)

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
        Convert marked regions of the state, or the whole state when it has no markers.

        Attack converter pipelines call this method. Markers are resolved inside the state
        text rather than across the request, so marked regions cannot split the JSON.

        Args:
            prompt (str): The decision request as a JSON object with ``state`` and ``questions``.
            input_type (PromptDataType): The type of input data. Must be "text".
            start_token (str): The token that starts a region to convert. Defaults to "⟪".
            end_token (str): The token that ends a region to convert. Defaults to "⟫".
            keep_tokens (bool): Retain each selected marker pair around its converted text.
                Defaults to False.

        Returns:
            ConverterResult: The decision request with its state converted.

        Raises:
            ValueError: If the input type is not "text".
            ValueError: If the prompt is not a decision request or its state is not convertible text.
            ValueError: If the wrapped converter does not return text.
        """
        if input_type != "text":
            raise ValueError(f"DecisionStateConverter only supports text input, got {input_type}")

        async def convert_state_async(state: str) -> ConverterResult:
            return await self._sub_converter.convert_tokens_async(
                prompt=state,
                input_type="text",
                start_token=start_token,
                end_token=end_token,
                keep_tokens=keep_tokens,
            )

        return await self._convert_request_async(prompt=prompt, convert_state_async=convert_state_async)

    async def _convert_request_async(
        self, *, prompt: str, convert_state_async: Callable[[str], Awaitable[ConverterResult]]
    ) -> ConverterResult:
        """
        Parse the request, convert its state text, and serialize the request again.

        Args:
            prompt (str): The decision request JSON.
            convert_state_async (Callable[[str], Awaitable[ConverterResult]]): Converts the state text.

        Returns:
            ConverterResult: The decision request with its state converted.

        Raises:
            ValueError: If the wrapped converter does not return text.
        """
        request = parse_decision_request(prompt=prompt, converter_name=type(self).__name__)
        state_text = self._get_state_text(request["state"])

        result = await convert_state_async(state_text)
        if result.output_type != "text":
            raise ValueError(f"DecisionStateConverter requires text output, but received {result.output_type}.")

        if self._state_field is None:
            request["state"] = result.output_text
        else:
            request["state"][self._state_field] = result.output_text
        return ConverterResult(output_text=serialize_decision_request(request), output_type="text")

    def _get_state_text(self, state: object) -> str:
        """
        Return the state text selected for conversion.

        Args:
            state (object): The parsed request state.

        Returns:
            str: The text to convert.

        Raises:
            ValueError: If the selected state value is not a string.
        """
        if self._state_field is None:
            if not isinstance(state, str):
                raise ValueError(
                    f"The decision state is a {type(state).__name__}, not a string. "
                    "Set state_field to the object field to convert."
                )
            return state

        if not isinstance(state, dict):
            raise ValueError(
                f"state_field is {self._state_field!r}, but the decision state is a {type(state).__name__}, "
                "not an object."
            )
        if self._state_field not in state:
            raise ValueError(f"The decision state has no field {self._state_field!r}.")
        value = state[self._state_field]
        if not isinstance(value, str):
            raise ValueError(
                f"The decision state field {self._state_field!r} is a {type(value).__name__}, not a string."
            )
        return value
