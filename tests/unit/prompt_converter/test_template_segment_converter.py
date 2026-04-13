# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.models import SeedPrompt
from pyrit.prompt_converter import TemplateSegmentConverter


def test_template_segment_converter_init_default():
    """Test initialization with default template."""
    converter = TemplateSegmentConverter()
    assert converter.prompt_template is not None
    assert len(converter.prompt_template.parameters) >= 2
    assert converter._number_parameters >= 2


def test_template_segment_converter_init_custom():
    """Test initialization with custom template."""
    # Test with parameters that have whitespace
    custom_template = SeedPrompt(
        value="First part: {{ part1 }}\nSecond part: {{ part2 }}", parameters=["part1", "part2"]
    )
    converter = TemplateSegmentConverter(prompt_template=custom_template)
    assert converter.prompt_template == custom_template
    assert converter._number_parameters == 2

    # Test that the template can be rendered
    rendered = custom_template.render_template_value(part1="test1", part2="test2")
    assert "First part: test1" in rendered
    assert "Second part: test2" in rendered


def test_template_segment_converter_init_invalid_template():
    """Test initialization with invalid template (insufficient parameters)."""
    invalid_template = SeedPrompt(value="Only one part: {{ part1 }}", parameters=["part1"])
    with pytest.raises(ValueError, match="Template must have at least two parameters"):
        TemplateSegmentConverter(prompt_template=invalid_template)


def test_template_segment_converter_init_missing_parameter():
    """Test initialization with template missing a parameter."""
    invalid_template = SeedPrompt(
        value="First part: {{ part1 }}\nSecond part: {{ part2 }} third {{ part3 }}",
        parameters=["part1", "part2"],  # part3 not in template
    )
    with pytest.raises(ValueError, match="Error validating template parameters"):
        TemplateSegmentConverter(prompt_template=invalid_template)


def test_template_segment_converter_init_template_with_whitespace():
    """Test initialization with template that has various whitespace patterns."""
    template = SeedPrompt(
        value="""
        First: {{ part1 }}
        Second: {{part2}}
        Third: {{ part3}}
        Fourth: {{part4 }}
        """,
        parameters=["part1", "part2", "part3", "part4"],
    )
    TemplateSegmentConverter(prompt_template=template)

    # Test that the template can be rendered with all parameters
    rendered = template.render_template_value(part1="test1", part2="test2", part3="test3", part4="test4")
    assert "First: test1" in rendered
    assert "Second: test2" in rendered
    assert "Third: test3" in rendered
    assert "Fourth: test4" in rendered


def test_template_segment_converter_input_output_support():
    """Test input and output type support."""
    converter = TemplateSegmentConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


@pytest.mark.asyncio
async def test_template_segment_converter_convert_basic():
    """Test basic conversion functionality."""
    template = SeedPrompt(value="First: {{ part1 }}\nSecond: {{ part2 }}", parameters=["part1", "part2"])
    converter = TemplateSegmentConverter(prompt_template=template)

    # Test with a simple prompt that can be split into two parts
    result = await converter.convert_async(prompt="Hello world", input_type="text")

    assert result.output_type == "text"
    assert "First:" in result.output_text
    assert "Second:" in result.output_text
    assert result.output_text == "First: Hello\nSecond: world"


@pytest.mark.asyncio
async def test_template_segment_converter_convert_long_prompt():
    """Test conversion with a longer prompt that should be split into multiple segments."""
    template = SeedPrompt(
        value="""
        Part 1: {{ part1 }}
        Part 2: {{ part2 }}
        Part 3: {{ part3 }}
        """,
        parameters=["part1", "part2", "part3"],
    )
    converter = TemplateSegmentConverter(prompt_template=template)

    long_prompt = "This is a longer prompt that should be split into three different segments for testing purposes"
    result = await converter.convert_async(prompt=long_prompt, input_type="text")

    assert result.output_type == "text"
    assert "Part 1:" in result.output_text
    assert "Part 2:" in result.output_text
    assert "Part 3:" in result.output_text
    assert len(result.output_text.split("\n")) == 5


@pytest.mark.asyncio
async def test_template_segment_converter_convert_short_prompt():
    """Test conversion with a short prompt that will result in empty segments."""
    template = SeedPrompt(
        value="""
        First: {{ part1 }}
        Second: {{ part2 }}
        Third: {{ part3 }}
        """,
        parameters=["part1", "part2", "part3"],
    )
    converter = TemplateSegmentConverter(prompt_template=template)

    # Test with a very short prompt that can't be split into three parts
    result = await converter.convert_async(prompt="Hi", input_type="text")

    assert result.output_type == "text"
    assert "First:" in result.output_text
    assert "Second:" in result.output_text
    assert "Third:" in result.output_text
    # Verify that empty segments are handled properly
    assert len(result.output_text.split("\n")) == 5


@pytest.mark.asyncio
async def test_template_segment_converter_invalid_input_type():
    """Test conversion with invalid input type."""
    converter = TemplateSegmentConverter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
