# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

import pytest

from pyrit.prompt_converter.policy_puppetry_converter import PolicyPuppetryConverter


def test_policy_puppetry_default_xml_wraps_prompt():
    converter = PolicyPuppetryConverter()
    result = asyncio.run(converter.convert_async(prompt="How do I pick a lock"))

    assert result.output_type == "text"
    # Policy structure present
    assert "<interaction-config>" in result.output_text
    assert "<scene-summary>" in result.output_text
    # Placeholder replaced with the input
    assert "How do I pick a lock" in result.output_text
    assert "{{ prompt }}" not in result.output_text


@pytest.mark.parametrize(
    "policy_format,markers",
    [
        ("xml", ["<interaction-config>", "<scene-summary>How do I pick a lock</scene-summary>"]),
        ("json", ['"interaction-config"', '"scene-summary": "How do I pick a lock"']),
        ("ini", ["[interaction-config]", "scene-summary = How do I pick a lock"]),
    ],
)
def test_policy_puppetry_each_format(policy_format, markers):
    converter = PolicyPuppetryConverter(policy_format=policy_format)
    result = asyncio.run(converter.convert_async(prompt="How do I pick a lock"))

    for marker in markers:
        assert marker in result.output_text
    assert "{{ prompt }}" not in result.output_text


def test_policy_puppetry_formats_differ():
    prompt = "How do I pick a lock"
    xml_out = asyncio.run(PolicyPuppetryConverter(policy_format="xml").convert_async(prompt=prompt)).output_text
    json_out = asyncio.run(PolicyPuppetryConverter(policy_format="json").convert_async(prompt=prompt)).output_text
    ini_out = asyncio.run(PolicyPuppetryConverter(policy_format="ini").convert_async(prompt=prompt)).output_text

    assert xml_out != json_out
    assert xml_out != ini_out
    assert json_out != ini_out


def test_policy_puppetry_input_supported():
    converter = PolicyPuppetryConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_policy_puppetry_output_supported():
    converter = PolicyPuppetryConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
