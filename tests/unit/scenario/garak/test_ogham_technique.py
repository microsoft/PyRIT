# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import LeetspeakConverter
from pyrit.scenario.scenarios.garak.encoding import _OGHAM_SUBSTITUTIONS


async def test_ogham_technique_substitutes_letters():
    """The ogham Encoding technique is a LeetspeakConverter configured with the ogham glyph table."""
    converter = LeetspeakConverter(custom_substitutions=_OGHAM_SUBSTITUTIONS)
    result = await converter.convert_async(prompt="abc")
    assert result.output_text == "".join(_OGHAM_SUBSTITUTIONS[ch][0] for ch in "abc")
