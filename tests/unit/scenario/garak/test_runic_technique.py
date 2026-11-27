# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import LeetspeakConverter
from pyrit.scenario.scenarios.garak.encoding import _RUNIC_SUBSTITUTIONS


async def test_runic_technique_substitutes_letters():
    """The runic Encoding technique is a LeetspeakConverter configured with the runic glyph table."""
    converter = LeetspeakConverter(custom_substitutions=_RUNIC_SUBSTITUTIONS)
    result = await converter.convert_async(prompt="abc")
    assert result.output_text == "".join(_RUNIC_SUBSTITUTIONS[ch][0] for ch in "abc")
