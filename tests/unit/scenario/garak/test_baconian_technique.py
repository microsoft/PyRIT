# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import LeetspeakConverter
from pyrit.scenario.scenarios.garak.encoding import _BACONIAN_SUBSTITUTIONS


async def test_baconian_technique_encodes_letters():
    """The baconian Encoding technique is a LeetspeakConverter configured with the Baconian a/b table."""
    converter = LeetspeakConverter(custom_substitutions=_BACONIAN_SUBSTITUTIONS)
    result = await converter.convert_async(prompt="ab")
    assert result.output_text == _BACONIAN_SUBSTITUTIONS["a"][0] + _BACONIAN_SUBSTITUTIONS["b"][0]
