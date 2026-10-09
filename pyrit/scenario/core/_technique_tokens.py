# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Grammar for the technique tokens that scenario runs and presets accept.

A token is a technique name optionally followed by colon-separated modifiers, as in
``role_play:converter.translation_spanish``. The grammar is shared rather than owned by
the launch path because every caller that reads a stored token has to agree on where the
technique name ends: a validator that compares a whole token against the scenario's
technique names rejects a token the launch path resolves successfully, and the operator
is told the preset is broken when it is not.

Only the split lives here. Turning a converter name into a converter instance needs the
``ConverterRegistry``, which scenarios do not depend on.
"""

from __future__ import annotations

from typing import NamedTuple

CONVERTER_MODIFIER_PREFIX = "converter."


class TechniqueToken(NamedTuple):
    """One parsed technique token."""

    base_name: str
    modifiers: tuple[str, ...]


def parse_technique_token(token: str) -> TechniqueToken:
    """
    Split one technique token into its technique name and its modifiers.

    Args:
        token (str): The token as written in a run request or a stored preset.

    Returns:
        TechniqueToken: The technique name and the modifiers that follow it, in token order.
    """
    base_name, _, remainder = token.partition(":")
    modifiers = tuple(modifier for modifier in remainder.split(":") if modifier)
    return TechniqueToken(base_name=base_name, modifiers=modifiers)


def converter_name_from_modifier(modifier: str) -> str | None:
    """
    Read the converter name out of a modifier.

    Args:
        modifier (str): One modifier from a technique token.

    Returns:
        str | None: The converter name, or None when the modifier is not a converter modifier.
    """
    if not modifier.startswith(CONVERTER_MODIFIER_PREFIX):
        return None
    return modifier[len(CONVERTER_MODIFIER_PREFIX) :]
