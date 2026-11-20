# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import SearchReplaceConverter


async def test_disemvowel_technique_removes_vowels():
    """The disemvowel Encoding technique (a SearchReplaceConverter config) strips ASCII vowels."""
    converter = SearchReplaceConverter(pattern="[AEIOUaeiou]", replace="")
    result = await converter.convert_async(prompt="Hello World")
    assert result.output_text == "Hll Wrld"
