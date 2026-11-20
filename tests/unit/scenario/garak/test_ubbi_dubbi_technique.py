# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import SearchReplaceConverter


async def test_ubbi_dubbi_technique_inserts_ub_before_vowels():
    """The ubbi_dubbi Encoding technique (a SearchReplaceConverter config) prefixes vowel runs with 'ub'."""
    converter = SearchReplaceConverter(pattern=r"([aeiouAEIOU]+)", replace=r"ub\1")
    result = await converter.convert_async(prompt="speak")
    assert result.output_text == "spubeak"
