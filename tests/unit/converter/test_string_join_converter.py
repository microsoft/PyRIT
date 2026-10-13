# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, StringJoinConverter
from pyrit.converter.text_selection_strategy import AllWordsSelectionStrategy, WordIndexSelectionStrategy
from pyrit.registry import ConverterRegistry


async def test_string_join_default():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h-e-l-l-o"
    assert result.output_type == "text"


async def test_string_join_custom_separator():
    converter = StringJoinConverter(join_value=".")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h.i"
    assert result.output_type == "text"


async def test_string_join_multi_word():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="hi there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h-i t-h-e-r-e"
    assert result.output_type == "text"


async def test_string_join_empty():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_string_join_single_char():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a"
    assert result.output_type == "text"


async def test_string_join_input_not_supported():
    converter = StringJoinConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_string_join_default_identifier_preserves_legacy_identity() -> None:
    converter = StringJoinConverter()
    assert converter.get_identifier().unique_name == "StringJoinConverter::d9ec3367"


def test_string_join_explicit_default_strategy_preserves_legacy_identity() -> None:
    converter = StringJoinConverter(
        word_selection_strategy=AllWordsSelectionStrategy(),
    )
    assert converter.get_identifier().unique_name == "StringJoinConverter::d9ec3367"


def test_string_join_identifier_includes_selection_parameters() -> None:
    first = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[0]),
    )
    second = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[1]),
    )

    assert first.get_identifier().hash != second.get_identifier().hash
    assert first.get_identifier().unique_name != second.get_identifier().unique_name


def test_string_join_identifier_preserves_index_order() -> None:
    first = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[1, 0]),
    )
    second = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[0, 1]),
    )

    assert first.get_identifier().hash != second.get_identifier().hash


def test_string_join_registry_accepts_distinct_selection_configurations() -> None:
    registry = ConverterRegistry()
    first = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[0]),
    )
    second = StringJoinConverter(
        word_selection_strategy=WordIndexSelectionStrategy(indices=[1]),
    )

    registry.instances.register(first)
    registry.instances.register(second)


@pytest.mark.parametrize(("indices", "join_value"), [([0], "-"), ([1, 0], "_")])
def test_string_join_identifier_parameter_contents(*, indices: list[int], join_value: str) -> None:
    converter = StringJoinConverter(
        join_value=join_value,
        word_selection_strategy=WordIndexSelectionStrategy(indices=indices),
    )

    assert converter.get_identifier().params == {
        "supported_input_types": ["text"],
        "supported_output_types": ["text"],
        "word_selection_strategy": "WordIndexSelectionStrategy",
        "word_selection_strategy_params": {"indices": indices},
        "word_split_separator": " ",
        "join_value": join_value,
    }


async def test_string_join_registry_accepts_default_and_partial_selection_async() -> None:
    registry = ConverterRegistry()
    default = StringJoinConverter()
    partial = StringJoinConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[0]))

    registry.instances.register(default)
    registry.instances.register(partial)

    assert registry.instances.get(default.get_identifier().unique_name) is default
    assert registry.instances.get(partial.get_identifier().unique_name) is partial
    assert (await default.convert_async(prompt="ab cd")).output_text == "a-b c-d"
    assert (await partial.convert_async(prompt="ab cd")).output_text == "a-b cd"


async def test_string_join_identifier_preserves_custom_all_words_subclass_async() -> None:
    class FirstWordSelectionStrategy(AllWordsSelectionStrategy):
        def select_words(self, *, words: list[str]) -> list[int]:
            return [0] if words else []

    default = StringJoinConverter()
    custom = StringJoinConverter(word_selection_strategy=FirstWordSelectionStrategy())
    identifier = custom.get_identifier()

    assert identifier.params["word_selection_strategy"] == "FirstWordSelectionStrategy"
    assert identifier.params["word_selection_strategy_params"] == {}
    assert identifier.hash != default.get_identifier().hash
    assert (await default.convert_async(prompt="ab cd")).output_text == "a-b c-d"
    assert (await custom.convert_async(prompt="ab cd")).output_text == "a-b cd"
