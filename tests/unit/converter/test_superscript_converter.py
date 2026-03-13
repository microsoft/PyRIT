# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, SuperscriptConverter


async def _check_conversion(converter, prompts, expected_outputs):
    for prompt, expected_output in zip(prompts, expected_outputs, strict=False):
        result = await converter.convert_async(prompt=prompt, input_type="text")
        assert isinstance(result, ConverterResult)
        assert result.output_text == expected_output


@pytest.mark.asyncio
async def test_superscript_converter():
    defalut_converter = SuperscriptConverter()
    await _check_conversion(
        defalut_converter,
        ["Let's test this converter!", "Unsupported characters stay the same: qCFQSXYZ"],
        [
            (
                "\u1d38\u1d49\u1d57'\u02e2 \u1d57\u1d49\u02e2\u1d57 \u1d57\u02b0\u2071\u02e2 "
                "\u1d9c\u1d52\u207f\u1d5b\u1d49\u02b3\u1d57\u1d49\u02b3!"
            ),
            (
                "\u1d41\u207f\u02e2\u1d58\u1d56\u1d56\u1d52\u02b3\u1d57\u1d49\u1d48 "
                "\u1d9c\u02b0\u1d43\u02b3\u1d43\u1d9c\u1d57\u1d49\u02b3\u02e2 "
                "\u02e2\u1d57\u1d43\u02b8 \u1d57\u02b0\u1d49 \u02e2\u1d43\u1d50\u1d49: qCFQSXYZ"
            ),
        ],
    )
