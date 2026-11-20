# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import LeetspeakConverter
from pyrit.scenario.scenarios.garak.encoding import _PIGPEN_SUBSTITUTIONS


async def test_pigpen_technique_substitutes_letters():
    """The pigpen Encoding technique is a LeetspeakConverter configured with the Pigpen glyph table."""
    converter = LeetspeakConverter(custom_substitutions=_PIGPEN_SUBSTITUTIONS)
    result = await converter.convert_async(prompt="abc")
    assert result.output_text == "".join(_PIGPEN_SUBSTITUTIONS[ch][0] for ch in "abc")
    assert result.output_text != "abc"
