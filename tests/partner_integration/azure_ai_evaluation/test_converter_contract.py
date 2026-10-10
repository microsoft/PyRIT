# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for Converter interface and specific converters used by azure-ai-evaluation.

The azure-ai-evaluation red team module:
- Extends Converter via _DefaultConverter
- Imports 20+ specific converters in _agent/_agent_utils.py and strategy_utils.py
- Uses ConverterResult as the return type
"""

import pytest

from pyrit.converter import Converter, ConverterResult


class TestConverterContract:
    """Validate Converter base class interface stability."""

    def test_converter_has_convert_async(self):
        """_DefaultConverter overrides convert_async."""
        assert hasattr(Converter, "convert_async")

    def test_converter_subclassable(self):
        """_DefaultConverter subclasses Converter with convert_async."""

        class TestConverter(Converter):
            SUPPORTED_INPUT_TYPES = ("text",)
            SUPPORTED_OUTPUT_TYPES = ("text",)

            async def convert_async(self, *, prompt, input_type="text"):
                return ConverterResult(output_text=prompt, output_type="text")

        converter = TestConverter()
        assert isinstance(converter, Converter)


class TestSpecificConvertersImportable:
    """Validate that all converters imported by azure-ai-evaluation are available.

    These converters are imported in:
    - _agent/_agent_utils.py (20+ converters)
    - _utils/strategy_utils.py (converter instantiation)
    """

    @pytest.mark.parametrize(
        "converter_name",
        [
            "AnsiAttackConverter",
            "AsciiArtConverter",
            "AtbashConverter",
            "Base64Converter",
            "BinaryConverter",
            "CaesarConverter",
            "CharacterSpaceConverter",
            # NOTE: _agent/_agent_utils.py imports "CharSwapGenerator" but PyRIT
            # exports "CharSwapConverter". This is a naming discrepancy in the SDK;
            # the canonical PyRIT name is CharSwapConverter.
            "CharSwapConverter",
            "DiacriticConverter",
            "FlipConverter",
            "LeetspeakConverter",
            "MathPromptConverter",
            "MorseConverter",
            "ROT13Converter",
            "StringJoinConverter",
            "SuffixAppendConverter",
            "TenseConverter",
            "UnicodeConfusableConverter",
            "UnicodeSubstitutionConverter",
            "UrlConverter",
        ],
    )
    def test_converter_importable(self, converter_name):
        """Each converter used by azure-ai-evaluation must be importable from pyrit.converter."""
        import pyrit.converter as pc

        converter_class = getattr(pc, converter_name, None)
        assert converter_class is not None, (
            f"{converter_name} not found in pyrit.converter — azure-ai-evaluation depends on this converter"
        )

    def test_ascii_smuggler_converter_importable(self):
        """AsciiSmugglerConverter is imported in _agent/_agent_utils.py."""
        from pyrit.converter import AsciiSmugglerConverter

        assert AsciiSmugglerConverter is not None

    def test_llm_generic_text_converter_importable(self):
        """LLMGenericTextConverter is used for tense/translation strategies."""
        from pyrit.converter import LLMGenericTextConverter

        assert LLMGenericTextConverter is not None
