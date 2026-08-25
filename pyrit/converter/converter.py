# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import asyncio
import functools
import inspect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast, get_args

from pyrit import converter
from pyrit.common.random_context import get_random_generator, random_execution
from pyrit.models import ComponentIdentifier, ConverterIdentifier, Identifiable, PromptDataType
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    import random
    from collections.abc import Awaitable, Callable

    from pyrit.prompt_target import PromptTarget


@dataclass
class ConverterResult:
    """The result of a prompt conversion, containing the converted output and its type."""

    #: The converted text output. This is the main result of the conversion.
    output_text: str
    #: The data type of the converted output. Indicates the format/type of the ``output_text``.
    output_type: PromptDataType

    def __str__(self) -> str:
        """
        Representation of the ConverterResult.

        Returns:
            str: A string representation showing the output type and text.
        """
        return f"{self.output_type}: {self.output_text}"


class Converter(Identifiable):
    """
    Base class for converters that transform prompts into a different representation or format.

    Concrete subclasses must declare their supported input and output modalities using class attributes:
    - SUPPORTED_INPUT_TYPES: tuple of PromptDataType values that the converter accepts
    - SUPPORTED_OUTPUT_TYPES: tuple of PromptDataType values that the converter produces

    These attributes are enforced at class definition time for all non-abstract subclasses.
    Concrete ``convert_async`` implementations are also wrapped at class definition time so
    named random streams are scoped to one input. Stochastic subclasses should obtain randomness
    through ``_get_random_generator`` and store an optional explicit constructor seed in ``_seed``.
    """

    #: Tuple of input modalities supported by this converter. Subclasses must override this.
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ()
    #: Tuple of output modalities supported by this converter. Subclasses must override this.
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ()

    #: Capability requirements placed on the converter's target (if any).
    #: Subclasses that use a target should override this and pass the target to
    #: ``super().__init__(converter_target=...)`` so the base class can validate it.
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements()

    _identifier: ComponentIdentifier | None = None
    _seed: int | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """
        Validate subclass contracts and scope concrete conversions for named randomness.

        Args:
            **kwargs: Additional keyword arguments passed to the superclass.

        Raises:
            TypeError: If a concrete subclass does not define non-empty SUPPORTED_INPUT_TYPES
                or SUPPORTED_OUTPUT_TYPES, or if its ``__init__`` accepts
                positional parameters after ``self``.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="Converter")
        # Only validate concrete (non-abstract) classes
        if not inspect.isabstract(cls):
            if not cls.SUPPORTED_INPUT_TYPES:
                raise TypeError(
                    f"{cls.__name__} must define non-empty SUPPORTED_INPUT_TYPES tuple. "
                    f"Declare the input modalities this converter accepts."
                )
            if not cls.SUPPORTED_OUTPUT_TYPES:
                raise TypeError(
                    f"{cls.__name__} must define non-empty SUPPORTED_OUTPUT_TYPES tuple. "
                    f"Declare the output modalities this converter produces."
                )

        convert_async = cast(
            "Callable[..., Awaitable[ConverterResult]] | None",
            cls.__dict__.get("convert_async"),
        )
        if convert_async and not getattr(convert_async, "__isabstractmethod__", False):

            @functools.wraps(convert_async)
            async def convert_with_random_context_async(
                self: Converter,
                *args: Any,
                **kwargs: Any,
            ) -> ConverterResult:
                namespace = f"{type(self).__module__}.{type(self).__qualname__}"
                prompt = kwargs.get("prompt")
                input_type = kwargs.get("input_type", "text")
                operation_key = f"{input_type}\x1f{prompt}" if isinstance(prompt, str) else None
                with random_execution(
                    namespace=namespace,
                    seed=self._get_random_seed_override(),
                    owner=self,
                    operation_key=operation_key,
                ):
                    return await convert_async(self, *args, **kwargs)

            cls.convert_async = cast("Any", convert_with_random_context_async)

    def __init__(self, *, converter_target: PromptTarget | None = None) -> None:
        """
        Initialize the converter.

        Args:
            converter_target (PromptTarget | None): Target used by the converter, if any. When
                provided, it is validated against ``TARGET_REQUIREMENTS``.
        """
        super().__init__()
        if converter_target is not None:
            type(self).TARGET_REQUIREMENTS.validate(target=converter_target)

    @abc.abstractmethod
    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into the target format supported by the converter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.
        """

    def input_supported(self, input_type: PromptDataType) -> bool:
        """
        Check if the input type is supported by the converter.

        Args:
            input_type (PromptDataType): The input type to check.

        Returns:
            bool: True if the input type is supported, False otherwise.
        """
        return input_type in self.SUPPORTED_INPUT_TYPES

    def output_supported(self, output_type: PromptDataType) -> bool:
        """
        Check if the output type is supported by the converter.

        Args:
            output_type (PromptDataType): The output type to check.

        Returns:
            bool: True if the output type is supported, False otherwise.
        """
        return output_type in self.SUPPORTED_OUTPUT_TYPES

    def _get_random_generator(self, *, stream: str) -> random.Random:
        """
        Return this conversion's generator for a named child stream.

        Args:
            stream (str): Stable name for the converter's independent random stream.

        Returns:
            random.Random: An operation-local generator.
        """
        return get_random_generator(stream=stream)

    def _get_random_seed_override(self) -> int | None:
        """
        Return the explicit seed that replaces the configured root for this converter.

        Stochastic converters that expose a ``seed`` constructor argument store it in
        ``self._seed``. Subclasses with another seed source can override this method.

        Returns:
            int | None: The converter-specific seed, or None to inherit the configured root.
        """
        return self._seed

    async def convert_tokens_async(
        self, *, prompt: str, input_type: PromptDataType = "text", start_token: str = "⟪", end_token: str = "⟫"
    ) -> ConverterResult:
        """
        Convert substrings within a prompt that are enclosed by specified start and end tokens. If there are no tokens
        present, the entire prompt is converted.

        Args:
            prompt (str): The input prompt containing text to be converted.
            input_type (str): The type of input data. Defaults to "text".
            start_token (str): The token indicating the start of a substring to be converted. Defaults to "⟪" which is
                relatively distinct.
            end_token (str): The token indicating the end of a substring to be converted. Defaults to "⟫" which is
                relatively distinct.

        Returns:
            str: The prompt with specified substrings converted.

        Raises:
            ValueError: If the input is inconsistent.
        """
        if input_type != "text" and (start_token in prompt or end_token in prompt):
            raise ValueError("Input type must be text when start or end tokens are present.")

        # Find all matches between start_token and end_token
        pattern = re.escape(start_token) + "(.*?)" + re.escape(end_token)
        matches = re.findall(pattern, prompt)

        if not matches:
            # No tokens found, convert the entire prompt
            return await self.convert_async(prompt=prompt, input_type=input_type)

        if prompt.count(start_token) != prompt.count(end_token):
            raise ValueError("Uneven number of start tokens and end tokens.")

        tasks = [self._replace_text_match_async(match) for match in matches]
        converted_parts = await asyncio.gather(*tasks)

        for original, converted in zip(matches, converted_parts, strict=False):
            prompt = prompt.replace(f"{start_token}{original}{end_token}", converted.output_text, 1)

        return ConverterResult(output_text=prompt, output_type="text")

    async def _replace_text_match_async(self, match: str) -> ConverterResult:
        return await self.convert_async(prompt=match, input_type="text")

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build and return the identifier for this converter.

        Subclasses can override this method to add converter-specific parameters
        by calling _create_identifier with additional arguments.

        The default implementation calls _create_identifier with no extra parameters.

        Returns:
            ComponentIdentifier: The constructed identifier.
        """
        return self._create_identifier()

    def _create_identifier(
        self,
        *,
        params: dict[str, Any] | None = None,
        converter_target: ComponentIdentifier | None = None,
        sub_converter: ComponentIdentifier | None = None,
    ) -> ComponentIdentifier:
        """
        Construct and return the converter identifier.

        Builds a ``ConverterIdentifier`` with the base converter params
        (supported_input_types, supported_output_types) and the converter's promoted
        child slots. The child slots are exposed as explicit named parameters
        (mirroring ``ConverterIdentifier``'s promoted fields) so they cannot drift
        into untyped ``children`` dicts.

        Subclasses should call this method in their _build_identifier() implementation
        to set the identifier with their specific parameters.

        Args:
            params (dict[str, Any] | None): Additional behavioral parameters from
                the subclass (e.g., font, encoding_func). Merged into the base params.
            converter_target (ComponentIdentifier | None): The target an LLM-backed
                converter calls, promoted to ``ConverterIdentifier.converter_target``.
            sub_converter (ComponentIdentifier | None): A nested converter a
                composite wraps, promoted to ``ConverterIdentifier.sub_converter``.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return ConverterIdentifier.of(
            self,
            params=params,
            supported_input_types=self.SUPPORTED_INPUT_TYPES,
            supported_output_types=self.SUPPORTED_OUTPUT_TYPES,
            converter_target=converter_target,
            sub_converter=sub_converter,
        )

    @property
    def supported_input_types(self) -> list[PromptDataType]:
        """
        A list of supported input types for the converter.

        Returns:
            list[PromptDataType]: A list of supported input types.
        """
        return [data_type for data_type in get_args(PromptDataType) if self.input_supported(data_type)]

    @property
    def supported_output_types(self) -> list[PromptDataType]:
        """
        A list of supported output types for the converter.

        Returns:
            list[PromptDataType]: A list of supported output types.
        """
        return [data_type for data_type in get_args(PromptDataType) if self.output_supported(data_type)]


def get_converter_modalities() -> list[tuple[str, list[PromptDataType], list[PromptDataType]]]:
    """
    Retrieve a list of all converter classes and their supported input/output modalities
    by reading the SUPPORTED_INPUT_TYPES and SUPPORTED_OUTPUT_TYPES class attributes.

    Returns:
        list[tuple[str, list[PromptDataType], list[PromptDataType]]]: A sorted list of tuples containing:
            - Converter class name (str)
            - List of supported input modalities (list[PromptDataType])
            - List of supported output modalities (list[PromptDataType])

        Sorted by input modality, then output modality, then converter name.
    """
    converter_modalities = []

    # Get all converter classes from the __all__ list
    for name in converter.__all__:
        if name in ("ConverterResult", "Converter") or "Strategy" in name:
            continue

        converter_class = getattr(converter, name)

        # Skip if not a class or not a subclass of Converter
        if not isinstance(converter_class, type) or not issubclass(converter_class, Converter):
            continue

        # Skip abstract base classes (they cannot be instantiated or used directly)
        if getattr(converter_class, "__abstractmethods__", None):
            continue

        # Read the class attributes
        input_modalities = list(converter_class.SUPPORTED_INPUT_TYPES)
        output_modalities = list(converter_class.SUPPORTED_OUTPUT_TYPES)

        converter_modalities.append((name, input_modalities, output_modalities))

    # Sort by input modality, then output modality, then converter name
    converter_modalities.sort(key=lambda x: (x[1][0] if x[1] else "", x[2][0] if x[2] else "", x[0]))

    return converter_modalities
