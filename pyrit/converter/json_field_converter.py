# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
from collections.abc import Awaitable, Callable

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_DECODER = json.JSONDecoder()


class JsonFieldConverter(Converter):
    """
    Apply another converter to one string field of a JSON request.

    Structured targets read a JSON request, such as a decision model request with ``state`` and
    ``questions``. Converting the whole request would also rewrite its keys and syntax. This
    wrapper converts only the string at ``json_pointer`` and writes it back as a JSON string.
    Everything else in the request, including whitespace and number formatting, stays as it was.

    ``json_pointer`` follows RFC 6901: ``""`` is the whole document, ``"/state"`` is the
    ``state`` key, ``"/messages/0/content"`` indexes into an array, and ``~1`` and ``~0``
    escape ``/`` and ``~`` inside a key.

    Example:
        >>> from pyrit.converter import Base64Converter, JsonFieldConverter
        >>>
        >>> converter = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state")
        >>> result = await converter.convert_async(
        ...     prompt='{"state": "hello", "questions": {"q": {"type": "noul", "instructions": "Is it a greeting?"}}}'
        ... )
        >>> # Result: '{"state": "aGVsbG8=", "questions": {"q": {"type": "noul", "instructions": "Is it a greeting?"}}}'
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, sub_converter: Converter, json_pointer: str) -> None:
        """
        Initialize the JSON field converter.

        Args:
            sub_converter (Converter): The text converter to apply to the field.
            json_pointer (str): The RFC 6901 JSON Pointer of the string field to convert.

        Raises:
            ValueError: If the wrapped converter does not support text input and output.
            ValueError: If ``json_pointer`` is not a valid JSON Pointer.
        """
        super().__init__()

        if not sub_converter.input_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text input")
        if not sub_converter.output_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text output")

        self._sub_converter = sub_converter
        self._json_pointer = json_pointer
        self._reference_tokens = self._parse_json_pointer(json_pointer)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with the field pointer and the wrapped converter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={"json_pointer": self._json_pointer},
            sub_converter=self._sub_converter.get_identifier(),
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the field at ``json_pointer`` with the wrapped converter.

        Args:
            prompt (str): The JSON request.
            input_type (PromptDataType): The type of input data. Must be "text".

        Returns:
            ConverterResult: The JSON request with the field converted.

        Raises:
            ValueError: If the input type is not "text".
            ValueError: If the prompt is not JSON or ``json_pointer`` does not select a string in it.
            ValueError: If the wrapped converter does not return text.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"JsonFieldConverter only supports text input, got {input_type}")

        async def convert_field_async(text: str) -> ConverterResult:
            return await self._sub_converter.convert_async(prompt=text, input_type="text")

        return await self._convert_field_async(prompt=prompt, convert_text_async=convert_field_async)

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
        Convert marked regions of the field, or the whole field when it has no markers.

        Attack converter pipelines call this method. Markers are resolved inside the field's
        decoded text, so a marked region cannot split the JSON. Markers in other fields are left
        in place for a later converter in the stack.

        Args:
            prompt (str): The JSON request.
            input_type (PromptDataType): The type of input data. Must be "text".
            start_token (str): The token that starts a region to convert. Defaults to "⟪".
            end_token (str): The token that ends a region to convert. Defaults to "⟫".
            keep_tokens (bool): Retain each selected marker pair around its converted text.
                Defaults to False.

        Returns:
            ConverterResult: The JSON request with the field converted.

        Raises:
            ValueError: If the input type is not "text".
            ValueError: If the prompt is not JSON or ``json_pointer`` does not select a string in it.
            ValueError: If the field's markers are malformed or the wrapped converter does not return text.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"JsonFieldConverter only supports text input, got {input_type}")

        async def convert_field_async(text: str) -> ConverterResult:
            return await self._sub_converter.convert_tokens_async(
                prompt=text,
                input_type="text",
                start_token=start_token,
                end_token=end_token,
                keep_tokens=keep_tokens,
            )

        return await self._convert_field_async(prompt=prompt, convert_text_async=convert_field_async)

    async def _convert_field_async(
        self, *, prompt: str, convert_text_async: Callable[[str], Awaitable[ConverterResult]]
    ) -> ConverterResult:
        """
        Find the field, convert its text, and splice it back as a JSON string.

        Args:
            prompt (str): The JSON request.
            convert_text_async (Callable[[str], Awaitable[ConverterResult]]): Converts the field text.

        Returns:
            ConverterResult: The JSON request with the field converted.

        Raises:
            ValueError: If the wrapped converter does not return text.
        """
        text, start, end = self._find_string_field(prompt)
        result = await convert_text_async(text)
        if result.output_type != "text":
            raise ValueError(f"JsonFieldConverter requires text output, but received {result.output_type}.")

        # Splicing instead of re-serializing keeps the rest of the request byte for byte, so
        # number formatting, duplicate keys elsewhere, and whitespace reach the target unchanged.
        converted = json.dumps(result.output_text, ensure_ascii=False)
        # A lone surrogate is valid as a JSON escape but cannot be encoded as UTF-8 when stored or sent.
        converted = _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", converted)
        return ConverterResult(output_text=f"{prompt[:start]}{converted}{prompt[end:]}", output_type="text")

    def _find_string_field(self, prompt: str) -> tuple[str, int, int]:
        """
        Locate the string the pointer selects.

        Args:
            prompt (str): The JSON request.

        Returns:
            tuple[str, int, int]: The decoded string and the start and end offsets of its JSON literal.

        Raises:
            ValueError: If the prompt is not JSON, the pointer does not resolve, a key on the
                path is duplicated, or the selected value is not a string.
        """
        try:
            json.loads(prompt)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"JsonFieldConverter requires a JSON document: {exc}") from exc

        # The document is valid JSON, so the walk below only has to find offsets, not validate.
        position = _skip_whitespace(prompt, position=0)
        location = ""
        for token in self._reference_tokens:
            if prompt[position] == "{":
                position = _find_member(prompt, position=position, token=token, location=location)
            elif prompt[position] == "[":
                position = _find_element(prompt, position=position, token=token, location=location)
            else:
                raise ValueError(
                    f"JSON Pointer {self._json_pointer!r} cannot resolve: the value at {location!r} "
                    "is not an object or array."
                )
            location = f"{location}/{_escape_reference_token(token)}"

        if prompt[position] != '"':
            value, _ = _DECODER.raw_decode(prompt, position)
            raise ValueError(
                f"JSON Pointer {self._json_pointer!r} selects {_describe_json_value(value)}, not a string."
            )
        text, end = _DECODER.raw_decode(prompt, position)
        return str(text), position, end

    @staticmethod
    def _parse_json_pointer(json_pointer: str) -> list[str]:
        """
        Split an RFC 6901 JSON Pointer into its unescaped reference tokens.

        Args:
            json_pointer (str): The pointer.

        Returns:
            list[str]: The reference tokens, empty for the whole document.

        Raises:
            ValueError: If the pointer is nonempty without a leading ``/``, or contains ``~``
                not followed by ``0`` or ``1``.
        """
        if not json_pointer:
            return []
        if not json_pointer.startswith("/"):
            raise ValueError(f"json_pointer must be empty or start with '/', got {json_pointer!r}.")
        if re.search(r"~(?![01])", json_pointer):
            raise ValueError(f"json_pointer {json_pointer!r} has '~' not followed by '0' or '1'.")
        return [token.replace("~1", "/").replace("~0", "~") for token in json_pointer[1:].split("/")]


def _escape_reference_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _skip_whitespace(document: str, *, position: int) -> int:
    while position < len(document) and document[position] in " \t\n\r":
        position += 1
    return position


def _describe_json_value(value: object) -> str:
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, bool):
        return "a boolean"
    if value is None:
        return "null"
    return "a number"


def _find_member(document: str, *, position: int, token: str, location: str) -> int:
    """
    Return the offset of the value under ``token`` in the object starting at ``position``.

    Args:
        document (str): A valid JSON document.
        position (int): The offset of the object's ``{``.
        token (str): The key to find.
        location (str): The pointer to the object, for error messages.

    Returns:
        int: The offset of the member's value.

    Raises:
        ValueError: If the key is missing or appears more than once.
    """
    found: int | None = None
    position = _skip_whitespace(document, position=position + 1)
    while document[position] != "}":
        key, position = _DECODER.raw_decode(document, position)
        position = _skip_whitespace(document, position=_skip_whitespace(document, position=position) + 1)
        if key == token:
            if found is not None:
                # Parsers disagree on which duplicate wins, so the field to convert is ambiguous.
                raise ValueError(f"The object at {location!r} has the key {token!r} more than once.")
            found = position
        _, position = _DECODER.raw_decode(document, position)
        position = _skip_whitespace(document, position=position)
        if document[position] == ",":
            position = _skip_whitespace(document, position=position + 1)
    if found is None:
        raise ValueError(f"The object at {location!r} has no key {token!r}.")
    return found


def _find_element(document: str, *, position: int, token: str, location: str) -> int:
    """
    Return the offset of the element at index ``token`` in the array starting at ``position``.

    Args:
        document (str): A valid JSON document.
        position (int): The offset of the array's ``[``.
        token (str): The index to find, in decimal without leading zeros.
        location (str): The pointer to the array, for error messages.

    Returns:
        int: The offset of the element.

    Raises:
        ValueError: If the token is not an array index or the index is out of range.
    """
    if not _ARRAY_INDEX.fullmatch(token):
        raise ValueError(f"The value at {location!r} is an array, and {token!r} is not an array index.")
    index = int(token)
    position = _skip_whitespace(document, position=position + 1)
    count = 0
    while document[position] != "]":
        if count == index:
            return position
        _, position = _DECODER.raw_decode(document, position)
        position = _skip_whitespace(document, position=position)
        if document[position] == ",":
            position = _skip_whitespace(document, position=position + 1)
        count += 1
    raise ValueError(f"The array at {location!r} has {count} elements, so index {index} is out of range.")
