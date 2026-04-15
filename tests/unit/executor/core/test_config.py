# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

from pyrit.executor.core.config import StrategyConverterConfig
from pyrit.prompt_normalizer import PromptConverterConfiguration


def test_default_empty_lists():
    config = StrategyConverterConfig()
    assert config.request_converters == []
    assert config.response_converters == []


def test_with_request_converters():
    mock_converter = MagicMock()
    pcc = PromptConverterConfiguration(converters=[mock_converter])
    config = StrategyConverterConfig(request_converters=[pcc])
    assert len(config.request_converters) == 1
    assert config.request_converters[0] is pcc
    assert config.response_converters == []


def test_with_response_converters():
    mock_converter = MagicMock()
    pcc = PromptConverterConfiguration(converters=[mock_converter])
    config = StrategyConverterConfig(response_converters=[pcc])
    assert len(config.response_converters) == 1
    assert config.response_converters[0] is pcc
    assert config.request_converters == []


def test_with_both_converters():
    mock_req = MagicMock()
    mock_resp = MagicMock()
    req_pcc = PromptConverterConfiguration(converters=[mock_req])
    resp_pcc = PromptConverterConfiguration(converters=[mock_resp])
    config = StrategyConverterConfig(request_converters=[req_pcc], response_converters=[resp_pcc])
    assert len(config.request_converters) == 1
    assert len(config.response_converters) == 1


def test_multiple_converter_configs():
    mock1, mock2 = MagicMock(), MagicMock()
    pcc1 = PromptConverterConfiguration(converters=[mock1])
    pcc2 = PromptConverterConfiguration(converters=[mock2])
    config = StrategyConverterConfig(request_converters=[pcc1, pcc2])
    assert len(config.request_converters) == 2


def test_is_dataclass_mutable():
    config = StrategyConverterConfig()
    mock_converter = MagicMock()
    pcc = PromptConverterConfiguration(converters=[mock_converter])
    config.request_converters.append(pcc)
    assert len(config.request_converters) == 1
