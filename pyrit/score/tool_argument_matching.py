# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pure evaluation of tool argument requirements against the arguments of one call."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pyrit.score.text_matching import match_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from pyrit.models import ToolCallRequirement


def _argument_text(value: JsonValue) -> str:
    """
    Render one argument value as the text a matcher compares.

    Returns:
        str: The string itself, or the compact JSON text of any other value.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def describe_tool_requirement(requirement: ToolCallRequirement) -> str:
    """
    Name a requirement in a rationale, with the arguments it constrains.

    Returns:
        str: The tool name, followed by its constrained argument names when there are any.
    """
    if not requirement.arguments:
        return requirement.name
    return f"{requirement.name}({', '.join(argument.name for argument in requirement.arguments)})"


def match_tool_arguments(*, requirement: ToolCallRequirement, arguments: Mapping[str, JsonValue] | None) -> bool | None:
    """
    Decide whether one call's arguments satisfy every argument requirement.

    A requirement without argument constraints is satisfied by any call. Arguments that were not
    retained cannot show a match, so they leave the call undetermined. A missing argument, or one
    that does not match, decides the call.

    Returns:
        bool | None: True when every constraint matches, false when one fails, otherwise None.
    """
    if not requirement.arguments:
        return True
    if arguments is None:
        return None
    for constraint in requirement.arguments:
        if constraint.name not in arguments:
            return False
        if not match_text(matcher=constraint.matcher, text=_argument_text(arguments[constraint.name])):
            return False
    return True


def match_tool_requirement(
    *, requirement: ToolCallRequirement, calls: Sequence[Mapping[str, JsonValue] | None]
) -> bool | None:
    """
    Combine the calls of one tool into a verdict for its requirement.

    One matching call satisfies the requirement. It is refuted only when every call is refuted,
    including when there are none; any undetermined call leaves it undetermined. The caller
    decides whether its evidence is complete enough to keep a refutation.

    Returns:
        bool | None: True when some call matches, false when every call is refuted, otherwise None.
    """
    verdicts = [match_tool_arguments(requirement=requirement, arguments=arguments) for arguments in calls]
    if True in verdicts:
        return True
    return None if None in verdicts else False
