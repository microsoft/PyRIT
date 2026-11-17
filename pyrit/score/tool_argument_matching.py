# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pure evaluation of tool argument requirements against the arguments of one call."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pyrit.score.text_matching import match_text

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

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


def match_tool_arguments(
    *,
    requirement: ToolCallRequirement,
    arguments: Mapping[str, JsonValue] | None,
    kept: Collection[str] | None = None,
) -> bool | None:
    """
    Decide whether one call's arguments satisfy every argument requirement.

    A requirement without argument constraints is satisfied by any call. Arguments that were not
    retained cannot show a match, so they leave the call undetermined. A missing argument, or one
    that does not match, decides the call.

    ``kept`` names the arguments the evidence kept when the rest were redacted at write time;
    None means every argument was retained. A redacted argument is unknown, not absent: a
    constraint on it can neither match nor fail, so the call is undetermined unless another,
    kept argument refutes it.

    Returns:
        bool | None: True when every constraint matches, false when one fails, otherwise None.
    """
    if not requirement.arguments:
        return True
    if arguments is None:
        return None
    verdict: bool | None = True
    for constraint in requirement.arguments:
        if kept is not None and constraint.name not in kept:
            verdict = None
        elif constraint.name not in arguments or not match_text(
            matcher=constraint.matcher, text=_argument_text(arguments[constraint.name])
        ):
            return False
    return verdict


def match_tool_requirement(
    *,
    requirement: ToolCallRequirement,
    calls: Sequence[Mapping[str, JsonValue] | None],
    kept: Collection[str] | None = None,
) -> bool | None:
    """
    Combine the calls of one tool into a verdict for its requirement.

    One matching call satisfies the requirement. It is refuted only when every call is refuted,
    including when there are none; any undetermined call leaves it undetermined. The caller
    decides whether its evidence is complete enough to keep a refutation. ``kept`` is as for
    ``match_tool_arguments``.

    Returns:
        bool | None: True when some call matches, false when every call is refuted, otherwise None.
    """
    verdicts = [match_tool_arguments(requirement=requirement, arguments=arguments, kept=kept) for arguments in calls]
    if True in verdicts:
        return True
    return None if None in verdicts else False
