# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for PolicyPuppetryConverter."""

import pytest

from pyrit.models import SeedPrompt
from pyrit.prompt_converter import (
    ConverterResult,
    PolicyPuppetryConverter,
    PolicyPuppetryTemplate,
)


def _dr_house() -> SeedPrompt:
    return PolicyPuppetryTemplate.DR_HOUSE.to_seed_prompt()


def _medical_advisor() -> SeedPrompt:
    return PolicyPuppetryTemplate.MEDICAL_ADVISOR.to_seed_prompt()


async def test_convert_async_dr_house_wraps_prompt():
    converter = PolicyPuppetryConverter(prompt_template=_dr_house())
    result = await converter.convert_async(prompt="HARMFUL_PAYLOAD")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert "HARMFUL_PAYLOAD" in result.output_text
    assert "DR HOUSE" in result.output_text
    # Placeholder must be fully substituted.
    assert "{{ prompt }}" not in result.output_text


async def test_convert_async_medical_advisor_wraps_prompt():
    converter = PolicyPuppetryConverter(prompt_template=_medical_advisor())
    result = await converter.convert_async(prompt="treatment plan for skin cancer")

    assert "treatment plan for skin cancer" in result.output_text
    assert "Medical Advisor" in result.output_text
    assert "{{ prompt }}" not in result.output_text


async def test_convert_async_templates_differ():
    dr_house = await PolicyPuppetryConverter(prompt_template=_dr_house()).convert_async(prompt="X")
    medical = await PolicyPuppetryConverter(prompt_template=_medical_advisor()).convert_async(prompt="X")
    assert dr_house.output_text != medical.output_text


async def test_convert_async_accepts_custom_seed_prompt():
    custom = SeedPrompt(value="CUSTOM PREFIX {{ prompt }} CUSTOM SUFFIX", parameters=["prompt"], data_type="text")
    converter = PolicyPuppetryConverter(prompt_template=custom)
    result = await converter.convert_async(prompt="INJECT")
    assert result.output_text == "CUSTOM PREFIX INJECT CUSTOM SUFFIX"


def test_default_template_is_random_member():
    # The default template must be one of the enum-backed templates.
    template_values = {t.value for t in PolicyPuppetryTemplate}
    converter = PolicyPuppetryConverter()
    assert converter._prompt_template.name in template_values


def test_template_random_returns_enum_member():
    assert PolicyPuppetryTemplate.random() in set(PolicyPuppetryTemplate)


def test_to_seed_prompt_returns_named_template():
    assert _dr_house().name == "dr_house"
    assert _medical_advisor().name == "medical_advisor"


async def test_convert_async_rejects_unsupported_input_type():
    converter = PolicyPuppetryConverter(prompt_template=_dr_house())
    with pytest.raises(ValueError, match="not supported"):
        await converter.convert_async(prompt="X", input_type="image_path")


def test_identifier_includes_template():
    dr_house_id = PolicyPuppetryConverter(prompt_template=_dr_house()).get_identifier()
    medical_id = PolicyPuppetryConverter(prompt_template=_medical_advisor()).get_identifier()

    assert dr_house_id.params["template"] == "dr_house"
    assert medical_id.params["template"] == "medical_advisor"


def test_supported_types():
    assert PolicyPuppetryConverter.SUPPORTED_INPUT_TYPES == ("text",)
    assert PolicyPuppetryConverter.SUPPORTED_OUTPUT_TYPES == ("text",)
