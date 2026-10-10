# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

from pyrit.converter import Converter
from pyrit.prompt_normalizer.converter_configuration import ConverterConfiguration


def _make_mock_converter(name: str = "MockConverter") -> Converter:
    return MagicMock(spec=Converter, name=name)


def test_init_with_converters():
    c1 = _make_mock_converter("Converter1")
    config = ConverterConfiguration(converters=[c1])
    assert config.converters == [c1]
    assert config.indexes_to_apply is None
    assert config.prompt_data_types_to_apply is None


def test_init_with_all_fields():
    c1 = _make_mock_converter()
    config = ConverterConfiguration(
        converters=[c1],
        indexes_to_apply=[0, 2],
        prompt_data_types_to_apply=["text", "image_path"],
    )
    assert config.indexes_to_apply == [0, 2]
    assert config.prompt_data_types_to_apply == ["text", "image_path"]


def test_from_converters_empty_list():
    result = ConverterConfiguration.from_converters(converters=[])
    assert result == []


def test_from_converters_single():
    c1 = _make_mock_converter()
    result = ConverterConfiguration.from_converters(converters=[c1])
    assert len(result) == 1
    assert result[0].converters == [c1]
    assert result[0].indexes_to_apply is None
    assert result[0].prompt_data_types_to_apply is None


def test_from_converters_multiple():
    c1 = _make_mock_converter("C1")
    c2 = _make_mock_converter("C2")
    c3 = _make_mock_converter("C3")
    result = ConverterConfiguration.from_converters(converters=[c1, c2, c3])
    assert len(result) == 3
    assert result[0].converters == [c1]
    assert result[1].converters == [c2]
    assert result[2].converters == [c3]


def test_from_converters_each_config_defaults_none():
    c1 = _make_mock_converter()
    c2 = _make_mock_converter()
    result = ConverterConfiguration.from_converters(converters=[c1, c2])
    for cfg in result:
        assert cfg.indexes_to_apply is None
        assert cfg.prompt_data_types_to_apply is None


def test_init_with_multiple_converters():
    c1 = _make_mock_converter("C1")
    c2 = _make_mock_converter("C2")
    config = ConverterConfiguration(converters=[c1, c2])
    assert len(config.converters) == 2
